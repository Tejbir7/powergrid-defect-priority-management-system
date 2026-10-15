import os
from flask import Flask
from config import Config
from extensions import db, csrf, bcrypt, mail, login_manager, scheduler
from routes import register_blueprints
from cli import register_commands, seed_demo_data
from services.email_service import reload_mail_config
from services.scheduler_service import init_scheduler_app, bootstrap_all_user_schedules
from utils.auth import init_login_manager


def create_app(config_class=Config):
    """Application factory for PowerGrid."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    csrf.init_app(app)
    bcrypt.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    init_login_manager(login_manager)
    init_scheduler_app(app)

    # Jinja globals
    app.jinja_env.globals.update(enumerate=enumerate)

    @app.context_processor
    def inject_workspace_context():
        from flask import session
        from flask_login import current_user
        from models.workspace import Workspace, WorkspaceMember

        if not current_user.is_authenticated:
            return {
                "current_workspace": None,
                "user_workspaces": [],
                "current_workspace_role": None,
            }

        active_ws_id = session.get("active_workspace_id")
        current_ws = None
        if active_ws_id:
            current_ws = db.session.get(Workspace, active_ws_id)

        user_workspaces = current_user.workspaces
        if not current_ws and user_workspaces:
            current_ws = user_workspaces[0]
            session["active_workspace_id"] = current_ws.id

        role_in_ws = current_user.role
        if current_ws:
            mem = db.session.scalar(
                db.select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == current_ws.id,
                    WorkspaceMember.user_id == current_user.id,
                )
            )
            if mem:
                role_in_ws = mem.role

        return {
            "current_workspace": current_ws,
            "user_workspaces": user_workspaces,
            "current_workspace_role": role_in_ws,
        }

    # Register modular blueprints and CLI commands
    register_blueprints(app)
    register_commands(app)

    return app


# Root application instance for WSGI servers (e.g. gunicorn app:app)
app = create_app()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_demo_data()
        reload_mail_config(app)
        bootstrap_all_user_schedules(app)

    # Decoupled scheduler: can be toggled via environment in multi-worker setups
    enable_scheduler = os.getenv("ENABLE_SCHEDULER", "True").lower() == "true"
    if enable_scheduler:
        scheduler.start()

    try:
        debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
        app.run(debug=debug_mode)
    finally:
        if scheduler.running:
            scheduler.shutdown()
