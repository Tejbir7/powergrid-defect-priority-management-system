import fcntl
import os
import time

bind = "0.0.0.0:5000"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
timeout = 60
accesslog = "-"
errorlog = "-"


def on_starting(server):
    """Create tables and seed the demo workspace once MySQL accepts connections."""
    from app import app
    from cli.commands import seed_demo_data
    from extensions import db
    from services.email_service import reload_mail_config
    from services.scheduler_service import bootstrap_all_user_schedules

    last_error = None
    for _ in range(30):
        try:
            with app.app_context():
                db.session.execute(db.text("SELECT 1"))
                db.create_all()
                seed_demo_data()
                reload_mail_config(app)
                bootstrap_all_user_schedules(app)
            return
        except Exception as exc:
            last_error = exc
            server.log.warning("Waiting for MySQL: %s", exc)
            time.sleep(2)
    raise RuntimeError("MySQL did not become ready") from last_error


def post_worker_init(worker):
    """Run the reminder clock in one worker. The lock moves if that worker restarts."""
    if os.getenv("ENABLE_SCHEDULER", "True").lower() != "true":
        return
    lock_file = open("/tmp/powergrid-scheduler.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return
    worker.scheduler_lock = lock_file
    from extensions import scheduler
    if not scheduler.running:
        scheduler.start()
