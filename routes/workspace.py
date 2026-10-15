import re
from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from extensions import db
from models.workspace import Workspace, WorkspaceMember
from models.user import User
from models.asset import Asset, AssetType
from models.location import Location, LocationType
from models.defect import Defect

workspace_bp = Blueprint("workspace", __name__)


def clean_invite_code(raw_input):
    """Extract clean 12-char or custom invite token from code or full URL."""
    if not raw_input:
        return ""
    code = raw_input.strip()
    if "/join/" in code:
        code = code.split("/join/")[-1].split("?")[0].split("#")[0].strip()
    return code


@workspace_bp.route("/workspaces")
@login_required
def workspaces_hub():
    """Google Classroom / Slack style Workspaces Hub."""
    memberships = db.session.scalars(
        db.select(WorkspaceMember)
        .where(WorkspaceMember.user_id == current_user.id)
        .order_by(WorkspaceMember.created_at.desc())
    ).all()

    workspaces_data = []
    active_ws_id = session.get("active_workspace_id")

    for mem in memberships:
        ws = mem.workspace
        if not ws:
            continue

        active_defects = db.session.scalar(
            db.select(db.func.count()).select_from(Defect).where(
                Defect.workspace_id == ws.id,
                Defect.status != "Resolved",
            )
        ) or 0

        resolved_defects = db.session.scalar(
            db.select(db.func.count()).select_from(Defect).where(
                Defect.workspace_id == ws.id,
                Defect.status == "Resolved",
            )
        ) or 0

        assets_count = db.session.scalar(
            db.select(db.func.count()).select_from(Asset).where(
                Asset.workspace_id == ws.id,
            )
        ) or 0

        members_count = db.session.scalar(
            db.select(db.func.count()).select_from(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws.id,
            )
        ) or 0

        workspaces_data.append({
            "workspace": ws,
            "membership": mem,
            "role": mem.role,
            "is_active": ws.id == active_ws_id,
            "active_defects": active_defects,
            "resolved_defects": resolved_defects,
            "assets_count": assets_count,
            "members_count": members_count,
        })

    return render_template(
        "workspaces.html",
        workspaces_data=workspaces_data,
        active_page="workspaces",
    )


@workspace_bp.route("/workspaces/create", methods=["POST"])
@login_required
def create_workspace():
    """Create a new transmission grid workspace with creator as Head."""
    name = request.form.get("name", "").strip()
    if not name:
        flash("Workspace name is required.", "danger")
        return redirect(url_for("workspace.workspaces_hub"))

    existing = db.session.scalar(db.select(Workspace).where(Workspace.name == name))
    if existing:
        flash("A grid workspace with that name already exists. Please choose a unique sector or station name.", "danger")
        return redirect(url_for("workspace.workspaces_hub"))

    ws = Workspace(
        name=name,
        created_by_id=current_user.id,
    )
    db.session.add(ws)
    db.session.flush()

    # Creator is Head with immediate approval
    membership = WorkspaceMember(
        workspace_id=ws.id,
        user_id=current_user.id,
        role="head",
        is_approved=True,
    )
    db.session.add(membership)
    db.session.flush()

    # Starter location structure for seamless onboarding
    reg_type = db.session.scalar(db.select(LocationType).where(LocationType.name == "Region"))
    if not reg_type:
        reg_type = LocationType(name="Region")
        db.session.add(reg_type)
        db.session.flush()

    sub_type = db.session.scalar(db.select(LocationType).where(LocationType.name == "Substation"))
    if not sub_type:
        sub_type = LocationType(name="Substation")
        db.session.add(sub_type)
        db.session.flush()

    bay_type = db.session.scalar(db.select(LocationType).where(LocationType.name == "Bay"))
    if not bay_type:
        bay_type = LocationType(name="Bay")
        db.session.add(bay_type)
        db.session.flush()

    root_loc = Location(
        workspace_id=ws.id,
        name=f"{name} Sector",
        location_type_id=reg_type.id,
    )
    db.session.add(root_loc)
    db.session.flush()

    sub_loc = Location(
        workspace_id=ws.id,
        name=f"{name} 400kV Yard",
        location_type_id=sub_type.id,
        parent_location_id=root_loc.id,
    )
    db.session.add(sub_loc)
    db.session.flush()

    bay_loc = Location(
        workspace_id=ws.id,
        name="Bay 1 (Main Bus)",
        location_type_id=bay_type.id,
        parent_location_id=sub_loc.id,
    )
    db.session.add(bay_loc)
    db.session.commit()

    session["active_workspace_id"] = ws.id
    flash(f"Workspace '{ws.name}' created successfully! You are the Head administrator.", "success")
    return redirect(url_for("dashboard"))


@workspace_bp.route("/workspaces/join", methods=["POST"])
@login_required
def join_workspace():
    """Join an existing workspace using an invite code or link."""
    raw_code = request.form.get("invite_code", "")
    code = clean_invite_code(raw_code)

    if not code:
        flash("Please enter an invite code or paste an invite link.", "danger")
        return redirect(url_for("workspace.workspaces_hub"))

    ws = db.session.scalar(db.select(Workspace).where(Workspace.invite_code == code))
    if not ws:
        flash("Invalid or expired invite code. Please contact your workspace head for an active invite code.", "danger")
        return redirect(url_for("workspace.workspaces_hub"))

    existing_mem = db.session.scalar(
        db.select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == current_user.id,
        )
    )

    if existing_mem:
        session["active_workspace_id"] = ws.id
        flash(f"You are already a member of '{ws.name}'. Switched to this workspace.", "info")
        return redirect(url_for("dashboard"))

    # Add as engineer
    new_mem = WorkspaceMember(
        workspace_id=ws.id,
        user_id=current_user.id,
        role="engineer",
        is_approved=True,
    )
    db.session.add(new_mem)
    db.session.commit()

    session["active_workspace_id"] = ws.id
    flash(f"Successfully joined '{ws.name}'! You are now an active team member.", "success")
    return redirect(url_for("dashboard"))


@workspace_bp.route("/join/<invite_code>", methods=["GET", "POST"])
def join_by_link(invite_code):
    """Shareable invite link handler (/join/<invite_code>)."""
    code = clean_invite_code(invite_code)
    ws = db.session.scalar(db.select(Workspace).where(Workspace.invite_code == code))
    if not ws:
        flash("The workspace invite link is invalid or has expired.", "danger")
        if current_user.is_authenticated:
            return redirect(url_for("workspace.workspaces_hub"))
        return redirect(url_for("login"))

    # If visitor is unauthenticated, remember code and send to login/register
    if not current_user.is_authenticated:
        session["pending_invite_code"] = code
        flash(f"You've been invited to join '{ws.name}'. Sign in or create an account to accept.", "info")
        return redirect(url_for("login", join=code))

    # User is authenticated
    membership = db.session.scalar(
        db.select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.user_id == current_user.id,
        )
    )

    if request.method == "POST":
        if not membership:
            membership = WorkspaceMember(
                workspace_id=ws.id,
                user_id=current_user.id,
                role="engineer",
                is_approved=True,
            )
            db.session.add(membership)
            db.session.commit()
            flash(f"Welcome to '{ws.name}'! You have joined as an Engineer.", "success")
        else:
            flash(f"Switched to '{ws.name}'.", "info")

        session["active_workspace_id"] = ws.id
        session.pop("pending_invite_code", None)
        return redirect(url_for("dashboard"))

    # GET request - show landing card
    creator = db.session.get(User, ws.created_by_id) if ws.created_by_id else None
    members_count = db.session.scalar(
        db.select(db.func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id
        )
    ) or 0
    assets_count = db.session.scalar(
        db.select(db.func.count()).select_from(Asset).where(
            Asset.workspace_id == ws.id
        )
    ) or 0

    return render_template(
        "join.html",
        workspace=ws,
        creator=creator,
        members_count=members_count,
        assets_count=assets_count,
        is_already_member=membership is not None,
    )


@workspace_bp.route("/workspaces/<int:workspace_id>/select")
@login_required
def select_workspace(workspace_id):
    """Switch active workspace session."""
    membership = db.session.scalar(
        db.select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
    )
    if not membership:
        flash("You are not a member of that workspace.", "danger")
        return redirect(url_for("workspace.workspaces_hub"))

    session["active_workspace_id"] = workspace_id
    flash(f"Switched to workspace '{membership.workspace.name}'.", "success")
    return redirect(url_for("dashboard"))


@workspace_bp.route("/workspaces/<int:workspace_id>/regenerate-invite", methods=["POST"])
@login_required
def regenerate_invite(workspace_id):
    """Regenerate shareable invite code (Head only)."""
    membership = db.session.scalar(
        db.select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
    )
    if not membership or membership.role != "head":
        flash("Only the workspace Head can regenerate invite codes.", "danger")
        return redirect(url_for("workspace.workspaces_hub"))

    ws = membership.workspace
    new_code = ws.regenerate_invite_code()
    flash(f"New invite code generated: {new_code}", "success")
    next_url = request.referrer or url_for("admin_users")
    return redirect(next_url)
