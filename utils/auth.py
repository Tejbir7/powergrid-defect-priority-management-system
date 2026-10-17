from functools import wraps
from flask import abort, redirect, url_for
from flask_login import current_user, login_required
from extensions import db
from models.user import User


def init_login_manager(login_manager):
    """Configure Flask-Login user loader and redirection defaults."""
    login_manager.login_view = "login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))


def role_required(*allowed_roles):
    """Restrict a route to specific roles. User must also be approved."""
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.is_approved:
                return redirect(url_for("pending_approval"))
            if current_user.role not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def approved_required(f):
    """Allow any approved user, regardless of role."""
    @wraps(f)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_approved:
            return redirect(url_for("pending_approval"))
        return f(*args, **kwargs)
    return wrapped


def get_engineers(workspace_id=None):
    """Return approved users who belong to the specified workspace and can be assigned defects."""
    if workspace_id:
        from models.workspace import WorkspaceMember
        return db.session.scalars(
            db.select(User)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.is_approved == True,
                WorkspaceMember.role.in_(["engineer", "manager", "head"]),
            )
            .order_by(User.username)
        ).all()

    return db.session.scalars(
        db.select(User).where(
            User.is_approved == True,
            User.role.in_(["engineer", "manager", "head"])
        ).order_by(User.username)
    ).all()
