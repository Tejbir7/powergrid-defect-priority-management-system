from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from extensions import db
from models.settings import AppSettings
from models.reminder import Reminder
from utils.auth import approved_required
from services.crypto import get_cipher
from services.email_service import reload_mail_config, send_test_email
from services.scheduler_service import schedule_user_reminder

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
@approved_required
def settings():
    reminder = db.session.scalar(
        db.select(Reminder).where(
            Reminder.user_id == current_user.id,
            Reminder.type == "DEFECT_SUMMARY",
        )
    )

    if request.method == "POST":
        action = request.form.get("action", "reminder")

        if action == "smtp" and current_user.role != "viewer":
            server = request.form.get("mail_server", "smtp.gmail.com").strip()
            port = request.form.get("mail_port", "587").strip()
            encryption = request.form.get("mail_encryption", "tls").strip()
            username = request.form.get("mail_username", "").strip()
            sender = request.form.get("mail_default_sender", "").strip()

            use_ssl = (encryption == "ssl")
            use_tls = (encryption == "tls")

            AppSettings.set("mail_server", server)
            AppSettings.set("mail_port", port)
            AppSettings.set("mail_use_tls", "True" if use_tls else "False")
            AppSettings.set("mail_use_ssl", "True" if use_ssl else "False")
            AppSettings.set("mail_username", username)
            AppSettings.set("mail_default_sender", sender or username)

            new_pw = request.form.get("mail_password", "").strip()
            if new_pw:
                encrypted_pw = get_cipher().encrypt(new_pw.encode()).decode()
                AppSettings.set("mail_password", encrypted_pw)

            reload_mail_config()
            flash("SMTP server configuration saved and applied successfully.", "success")

        elif action == "test_smtp" and current_user.role != "viewer":
            test_recipient = request.form.get("test_recipient", "").strip() or current_user.email
            ok, msg = send_test_email(test_recipient)
            if ok:
                flash(msg, "success")
            else:
                flash(msg, "danger")

        elif action == "reminder":
            freq = request.form.get("frequency", "daily")
            time_str = request.form.get("reminder_time", "09:00")
            try:
                h, m = map(int, time_str.split(":"))
            except (ValueError, AttributeError):
                h, m = 9, 0

            dow = request.form.get("day_of_week", type=int)
            dom = request.form.get("day_of_month", type=int)
            enabled = bool(request.form.get("reminder_enabled"))

            if not reminder:
                reminder = Reminder(
                    user_id=current_user.id,
                    type="DEFECT_SUMMARY",
                )
                db.session.add(reminder)

            reminder.enabled = enabled
            reminder.frequency = freq
            reminder.hour = h
            reminder.minute = m
            reminder.day_of_week = dow
            reminder.day_of_month = dom
            reminder.updated_at = datetime.now()
            db.session.commit()

            schedule_user_reminder(reminder)

            if reminder.enabled:
                freq_label = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}.get(freq, freq)
                flash(f"Email reminder set to {freq_label} at {time_str}.", "success")
            else:
                flash("Email reminders disabled.", "info")

        return redirect(url_for("settings"))

    # GET
    smtp_configured = bool(AppSettings.get("mail_username"))
    smtp_settings = {
        "server": AppSettings.get("mail_server", "smtp.gmail.com"),
        "port": AppSettings.get("mail_port", "587"),
        "use_tls": AppSettings.get("mail_use_tls", "True") == "True",
        "use_ssl": AppSettings.get("mail_use_ssl", "False") == "True",
        "username": AppSettings.get("mail_username", ""),
        "default_sender": AppSettings.get("mail_default_sender", ""),
    }

    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_of_month = list(range(1, 29))

    return render_template(
        "settings.html",
        active_page="settings",
        reminder=reminder,
        smtp_configured=smtp_configured,
        smtp_settings=smtp_settings,
        can_configure_smtp=(current_user.role != "viewer"),
        days_of_week=days_of_week,
        days_of_month=days_of_month,
        now=datetime.now(),
    )
