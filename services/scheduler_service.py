from apscheduler.jobstores.base import JobLookupError
from flask import current_app
from extensions import db, scheduler
from models.user import User
from models.defect import Defect
from models.reminder import Reminder
from services.email_service import reload_mail_config, send_reminder_email

_flask_app = None


def init_scheduler_app(app):
    """Store the Flask app reference for background scheduler threads."""
    global _flask_app
    _flask_app = app


def run_reminders_for_user(user_id):
    """APScheduler job for a single user — sends their personalised reminder."""
    global _flask_app
    app = _flask_app or (current_app._get_current_object() if current_app else None)
    if not app:
        return

    with app.app_context():
        reload_mail_config(app)
        if not app.config.get("MAIL_USERNAME"):
            return
        user = db.session.get(User, user_id)
        if not user or not user.is_approved:
            return
        reminder = db.session.scalar(
            db.select(Reminder).where(
                Reminder.user_id == user_id,
                Reminder.type == "DEFECT_SUMMARY",
                Reminder.enabled == True,
            )
        )
        if not reminder:
            return
        pending = db.session.scalars(
            db.select(Defect).where(
                Defect.assigned_user_id == user_id,
                Defect.status != "Resolved",
            )
        ).all()
        if pending:
            send_reminder_email(user, pending, app=app)


def schedule_user_reminder(reminder):
    """Add or replace a scheduler job for a user based on their Reminder row."""
    job_id = f"reminder_user_{reminder.user_id}"
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass

    if not reminder.enabled:
        return

    h = reminder.hour if reminder.hour is not None else 9
    m = reminder.minute if reminder.minute is not None else 0

    if reminder.frequency == "weekly":
        dow = reminder.day_of_week if reminder.day_of_week is not None else 0
        scheduler.add_job(
            run_reminders_for_user,
            "cron",
            id=job_id,
            day_of_week=dow,
            hour=h,
            minute=m,
            args=[reminder.user_id],
            replace_existing=True,
        )
    elif reminder.frequency == "monthly":
        dom = reminder.day_of_month or 1
        scheduler.add_job(
            run_reminders_for_user,
            "cron",
            id=job_id,
            day=dom,
            hour=h,
            minute=m,
            args=[reminder.user_id],
            replace_existing=True,
        )
    else:  # daily (default)
        scheduler.add_job(
            run_reminders_for_user,
            "cron",
            id=job_id,
            hour=h,
            minute=m,
            args=[reminder.user_id],
            replace_existing=True,
        )


def bootstrap_all_user_schedules(app=None):
    """Called at startup to register existing user reminder preferences."""
    target_app = app or _flask_app or (current_app._get_current_object() if current_app else None)
    if not target_app:
        return

    with target_app.app_context():
        active_reminders = db.session.scalars(
            db.select(Reminder).where(Reminder.enabled == True)
        ).all()
        for reminder in active_reminders:
            schedule_user_reminder(reminder)
