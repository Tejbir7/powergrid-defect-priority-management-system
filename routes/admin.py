from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from extensions import db
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from models.defect import Defect, DefectStatusLog
from utils.auth import role_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/users")
@login_required
def admin_users():
    """Manage team users and workspace memberships."""
    active_ws_id = session.get("active_workspace_id")
    workspace = None
    if active_ws_id:
        workspace = db.session.get(Workspace, active_ws_id)

    # Allow Head or Manager in current workspace, or global Head
    is_authorized = current_user.role == "head"
    if workspace:
        mem = db.session.scalar(
            db.select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == current_user.id,
            )
        )
        if mem and mem.role in ("head", "manager"):
            is_authorized = True

    if not is_authorized:
        abort(403)

    memberships = []
    if workspace:
        memberships = db.session.scalars(
            db.select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
            .order_by(WorkspaceMember.created_at.asc())
        ).all()

    users = db.session.scalars(
        db.select(User).order_by(User.created_at.asc())
    ).all()

    return render_template(
        "admin_users.html",
        active_page="admin_users",
        workspace=workspace,
        memberships=memberships,
        users=users,
    )


@admin_bp.route("/admin/workspace/members/<int:membership_id>/role", methods=["POST"])
@login_required
def change_workspace_member_role(membership_id):
    """Change user role within active grid workspace."""
    mem = db.get_or_404(WorkspaceMember, membership_id)
    if mem.user_id == current_user.id:
        flash("You cannot change your own role in the workspace.", "danger")
        return redirect(url_for("admin_users"))

    new_role = request.form.get("role", "engineer")
    if new_role not in ("head", "manager", "engineer", "viewer"):
        flash("Invalid role.", "danger")
        return redirect(url_for("admin_users"))

    mem.role = new_role
    db.session.commit()
    flash(f"{mem.user.username}'s workspace role updated to {new_role.title()}.", "success")
    return redirect(url_for("admin_users"))


@admin_bp.route("/admin/workspace/members/<int:membership_id>/remove", methods=["POST"])
@login_required
def remove_workspace_member(membership_id):
    """Remove user membership from active grid workspace."""
    mem = db.get_or_404(WorkspaceMember, membership_id)
    if mem.user_id == current_user.id:
        flash("You cannot remove yourself from the workspace.", "danger")
        return redirect(url_for("admin_users"))

    username = mem.user.username
    db.session.delete(mem)
    db.session.commit()
    flash(f"{username} removed from the grid workspace.", "success")
    return redirect(url_for("admin_users"))


@admin_bp.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@role_required("head")
def approve_user(user_id):
    user = db.get_or_404(User, user_id)
    user.is_approved = True
    db.session.commit()
    flash(f"{user.username} has been approved and can now log in.", "success")
    return redirect(url_for("admin_users"))


@admin_bp.route("/admin/users/<int:user_id>/reject", methods=["POST"])
@role_required("head")
def reject_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot remove yourself.", "danger")
        return redirect(url_for("admin_users"))

    # Preserve audit integrity: users who authored defects or logs cannot be hard-deleted
    has_authored_defects = (
        db.session.scalar(
            db.select(db.func.count()).select_from(Defect).where(Defect.created_by_id == user.id)
        )
        > 0
    )
    has_status_logs = (
        db.session.scalar(
            db.select(db.func.count()).select_from(DefectStatusLog).where(
                DefectStatusLog.changed_by_id == user.id
            )
        )
        > 0
    )

    if has_authored_defects or has_status_logs:
        user.is_approved = False
        db.session.commit()
        flash(
            f"{user.username} has defect history. Access revoked (disabled) to preserve audit records.",
            "warning",
        )
        return redirect(url_for("admin_users"))

    # Safely unassign any active defects before removing the user
    db.session.execute(
        db.update(Defect).where(Defect.assigned_user_id == user.id).values(assigned_user_id=None)
    )

    db.session.delete(user)
    db.session.commit()
    flash(f"User {user.username} removed.", "success")
    return redirect(url_for("admin_users"))


@admin_bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@role_required("head")
def change_user_role(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot change your own role.", "danger")
        return redirect(url_for("admin_users"))
    new_role = request.form.get("role", "viewer")
    if new_role not in ("head", "manager", "engineer", "viewer"):
        flash("Invalid role.", "danger")
        return redirect(url_for("admin_users"))
    user.role = new_role
    db.session.commit()
    flash(f"{user.username}'s global role updated to {new_role.title()}.", "success")
    return redirect(url_for("admin_users"))
