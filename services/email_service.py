from datetime import datetime
from flask import current_app
from flask_mail import Message
from extensions import mail
from models.settings import AppSettings
from services.crypto import get_cipher


def reload_mail_config(app=None):
    """Pull SMTP settings from AppSettings table into Flask-Mail config."""
    target_app = app or (current_app._get_current_object() if current_app else None)
    if not target_app:
        return

    with target_app.app_context():
        target_app.config["MAIL_SERVER"] = AppSettings.get(
            "mail_server", target_app.config.get("MAIL_SERVER", "smtp.gmail.com")
        )
        target_app.config["MAIL_PORT"] = int(
            AppSettings.get("mail_port", str(target_app.config.get("MAIL_PORT", 587)))
        )
        
        use_ssl = AppSettings.get("mail_use_ssl", "False") == "True"
        use_tls = AppSettings.get("mail_use_tls", "True") == "True" if not use_ssl else False
        target_app.config["MAIL_USE_TLS"] = use_tls
        target_app.config["MAIL_USE_SSL"] = use_ssl

        target_app.config["MAIL_USERNAME"] = AppSettings.get(
            "mail_username", target_app.config.get("MAIL_USERNAME", "")
        )

        default_sender = AppSettings.get("mail_default_sender", "").strip()
        if not default_sender:
            default_sender = target_app.config.get("MAIL_USERNAME", "")
        target_app.config["MAIL_DEFAULT_SENDER"] = default_sender

        encrypted_pw = AppSettings.get("mail_password", "")
        try:
            pw = (
                get_cipher(target_app.config.get("SECRET_KEY")).decrypt(encrypted_pw.encode()).decode()
                if encrypted_pw
                else ""
            )
        except Exception:
            pw = encrypted_pw
        target_app.config["MAIL_PASSWORD"] = pw

        mail.init_app(target_app)


def send_test_email(recipient_email, app=None):
    """Attempt to send a test email to verify SMTP credentials."""
    target_app = app or (current_app._get_current_object() if current_app else None)
    if not target_app:
        return False, "Application context is unavailable."

    reload_mail_config(target_app)

    server = target_app.config.get("MAIL_SERVER")
    username = target_app.config.get("MAIL_USERNAME")
    sender = target_app.config.get("MAIL_DEFAULT_SENDER") or username

    if not server or not username:
        return False, "SMTP Server and Email Address / Username must be configured before testing."

    if not recipient_email:
        return False, "Please specify a recipient email address for the test."

    subject = "PowerGrid Operations — Test Email Connection"
    lines = [
        "Hello,",
        "",
        "This is an automated test email from your PowerGrid Maintenance Defect Tracking System.",
        "",
        "If you received this message, your SMTP email settings have been verified and are working properly!",
        "",
        f"• Host: {server}:{target_app.config.get('MAIL_PORT')}",
        f"• Encryption: {'SSL (Port 465)' if target_app.config.get('MAIL_USE_SSL') else ('TLS (STARTTLS)' if target_app.config.get('MAIL_USE_TLS') else 'None')}",
        f"• Sender Address: {sender}",
        f"• Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "— PowerGrid Operations Dispatch",
    ]

    msg = Message(
        subject=subject,
        recipients=[recipient_email],
        body="\n".join(lines),
        sender=sender,
    )
    try:
        mail.send(msg)
        target_app.logger.info(f"Test email successfully delivered to {recipient_email}")
        return True, f"Test email sent successfully to {recipient_email}!"
    except Exception as exc:
        target_app.logger.error(f"Failed to send test email to {recipient_email}: {exc}")
        return False, f"SMTP Error: {str(exc)}"


def send_reminder_email(user, pending_defects, app=None):
    """Send a reminder email to one user."""
    target_app = app or (current_app._get_current_object() if current_app else None)
    if not target_app:
        return

    smtp_user = target_app.config.get("MAIL_USERNAME", "")
    if not smtp_user:
        return

    sender = target_app.config.get("MAIL_DEFAULT_SENDER") or smtp_user

    subject = f"PowerGrid — You have {len(pending_defects)} pending defect(s)"
    lines = [
        f"Hi {user.username},",
        "",
        "You have the following defects pending action:",
        "",
    ]
    for d in pending_defects:
        due = (
            d.expected_resolution.strftime("%Y-%m-%d")
            if d.expected_resolution
            else "No due date"
        )
        asset_name = d.asset.asset_name if d.asset else "Unknown Asset"
        lines.append(f"  • DFT-{d.id}: {asset_name} ({d.priority}) — Due: {due}")
    lines += ["", "Please log in to update your tasks.", "", "— PowerGrid System"]

    msg = Message(
        subject=subject,
        recipients=[user.email],
        body="\n".join(lines),
        sender=sender,
    )
    try:
        mail.send(msg)
        target_app.logger.info(f"Reminder sent to {user.email}")
    except Exception as exc:
        target_app.logger.error(f"Failed to send reminder to {user.email}: {exc}")
