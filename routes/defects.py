from collections import Counter
from datetime import date, datetime
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from extensions import db
from models.user import User
from models.asset import Asset, AssetType
from models.location import Location
from models.defect import Defect, DefectStatusLog
from utils.auth import approved_required, role_required, get_engineers
from utils.queries import (
    parse_datetime_input,
    resolve_time_window,
    defect_query,
    apply_defect_search,
)

defects_bp = Blueprint("defects", __name__)


@defects_bp.route("/")
def home():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    if session.get("active_workspace_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("workspace.workspaces_hub"))


@defects_bp.route("/search-defects")
@approved_required
def search_defects():
    destination = "defect_history" if request.args.get("scope") == "resolved" else "defects"
    return redirect(url_for(destination, q=request.args.get("q", "").strip()))


@defects_bp.route("/dashboard")
@approved_required
def dashboard():
    from flask import session
    active_ws_id = session.get("active_workspace_id")
    if not active_ws_id and current_user.workspaces:
        active_ws_id = current_user.workspaces[0].id
        session["active_workspace_id"] = active_ws_id
    if not active_ws_id:
        return redirect(url_for("workspace.workspaces_hub"))

    active_defects = db.session.scalars(defect_query(include_resolved=False, workspace_id=active_ws_id)).all()

    # Engineers see their own assigned defects on dashboard
    my_defects = None
    if current_user.role == "engineer":
        my_defects = [d for d in active_defects if d.assigned_user_id == current_user.id]

    recent_defects = active_defects[:5]
    status_counts = Counter(d.status for d in active_defects)
    priority_counts = Counter(d.priority for d in active_defects)
    now = datetime.now()
    start_of_today = datetime.combine(date.today(), datetime.min.time())

    resolved_today = db.session.scalars(
        db.select(Defect).where(
            Defect.workspace_id == active_ws_id,
            Defect.status == "Resolved",
            Defect.resolved_at >= start_of_today,
            Defect.resolved_at <= now,
        )
    ).all()

    dashboard_cards = [
        {"label": "Open", "value": status_counts.get("Open", 0), "icon": "O", "tone": "bg-yellow"},
        {
            "label": "Overdue",
            "value": sum(
                d.expected_resolution is not None and d.expected_resolution < now
                for d in active_defects
            ),
            "icon": "!",
            "tone": "bg-red",
        },
        {
            "label": "Unassigned",
            "value": sum(d.assigned_user_id is None for d in active_defects),
            "icon": "U",
            "tone": "bg-azure",
        },
        {"label": "Resolved Today", "value": len(resolved_today), "icon": "R", "tone": "bg-green"},
    ]

    priority_order = ["Critical", "High", "Medium", "Low"]
    priority_breakdown = [
        {
            "label": p,
            "count": priority_counts.get(p, 0),
            "progress": round(
                (priority_counts.get(p, 0) / len(active_defects) * 100) if active_defects else 0
            ),
        }
        for p in priority_order
        if priority_counts.get(p, 0)
    ]

    status_order = ["Open", "In Progress"]
    status_breakdown = [
        {
            "label": s,
            "count": status_counts.get(s, 0),
            "progress": round(
                (status_counts.get(s, 0) / len(active_defects) * 100) if active_defects else 0
            ),
        }
        for s in status_order
        if status_counts.get(s, 0)
    ]

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        dashboard_cards=dashboard_cards,
        recent_defects=recent_defects,
        priority_breakdown=priority_breakdown,
        status_breakdown=status_breakdown,
        total_defects=len(active_defects),
        my_defects=my_defects,
    )


@defects_bp.route("/defects")
@approved_required
def defects():
    from flask import session
    active_ws_id = session.get("active_workspace_id")
    if not active_ws_id and current_user.workspaces:
        active_ws_id = current_user.workspaces[0].id
        session["active_workspace_id"] = active_ws_id
    if not active_ws_id:
        return redirect(url_for("workspace.workspaces_hub"))

    start_dt, end_dt, selected_range = resolve_time_window()
    search_query = request.args.get("q", "").strip()
    query = defect_query(include_resolved=False, start_dt=start_dt, end_dt=end_dt, workspace_id=active_ws_id)
    all_defects = db.session.scalars(apply_defect_search(query, search_query)).all()

    selected_defect_id = request.args.get("defect_id", type=int)
    selected_defect = next((d for d in all_defects if d.id == selected_defect_id), None)
    if selected_defect is None and all_defects:
        selected_defect = all_defects[0]
        selected_defect_id = selected_defect.id

    return render_template(
        "defects.html",
        active_page="defects",
        defects=all_defects,
        selected_defect=selected_defect,
        selected_defect_id=selected_defect_id,
        selected_range=selected_range,
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
        search_query=search_query,
    )


@defects_bp.route("/defect-history")
@approved_required
def defect_history():
    from flask import session
    active_ws_id = session.get("active_workspace_id")
    if not active_ws_id and current_user.workspaces:
        active_ws_id = current_user.workspaces[0].id
        session["active_workspace_id"] = active_ws_id
    if not active_ws_id:
        return redirect(url_for("workspace.workspaces_hub"))

    start_dt, end_dt, selected_range = resolve_time_window()
    search_query = request.args.get("q", "").strip()
    resolved_defects = db.session.scalars(
        apply_defect_search(
            defect_query(include_resolved="resolved-only", start_dt=start_dt, end_dt=end_dt, workspace_id=active_ws_id),
            search_query,
        )
    ).all()

    selected_defect_id = request.args.get("defect_id", type=int)
    selected_defect = next((d for d in resolved_defects if d.id == selected_defect_id), None)
    if selected_defect is None and resolved_defects:
        selected_defect = resolved_defects[0]
        selected_defect_id = selected_defect.id

    return render_template(
        "defect_history.html",
        active_page="history",
        defects=resolved_defects,
        selected_defect=selected_defect,
        selected_defect_id=selected_defect_id,
        selected_range=selected_range,
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
        search_query=search_query,
    )


@defects_bp.route("/defects/add", methods=["GET", "POST"])
@role_required("head", "manager", "engineer")
def add_defect():
    from flask import session
    active_ws_id = session.get("active_workspace_id")
    if not active_ws_id and current_user.workspaces:
        active_ws_id = current_user.workspaces[0].id
        session["active_workspace_id"] = active_ws_id
    if not active_ws_id:
        return redirect(url_for("workspace.workspaces_hub"))

    engineers = get_engineers(workspace_id=active_ws_id)
    assets = db.session.scalars(
        db.select(Asset).where(Asset.workspace_id == active_ws_id).order_by(Asset.asset_name)
    ).all()
    asset_types = db.session.scalars(db.select(AssetType).order_by(AssetType.name.asc())).all()
    locations = db.session.scalars(db.select(Location).where(Location.workspace_id == active_ws_id).order_by(Location.name.asc())).all()
    parent_candidates = db.session.scalars(db.select(Asset).where(Asset.workspace_id == active_ws_id, Asset.parent_asset_id == None).order_by(Asset.asset_name.asc())).all()
    selected_asset_id = request.args.get("selected_asset_id", type=int)

    if request.method == "POST":
        asset_id_raw = request.form.get("asset_id", "").strip()
        asset_name = request.form.get("asset_name", "").strip()
        description = request.form.get("description", "").strip()
        priority = request.form.get("priority", "Medium").strip()
        if priority not in ("Low", "Medium", "High", "Critical"):
            priority = "Medium"
        status = request.form.get("status", "Open")
        assigned_uid = request.form.get("assigned_user_id", "").strip()

        errors = []
        asset_id = int(asset_id_raw) if asset_id_raw.isdigit() else None
        if not asset_id and asset_name:
            # Fallback for name matching during template transition
            matched_asset = db.session.scalar(
                db.select(Asset).where(Asset.asset_name == asset_name, Asset.workspace_id == active_ws_id)
            )
            if matched_asset:
                asset_id = matched_asset.id

        if not asset_id:
            errors.append("Please select a valid asset.")
        elif not db.session.get(Asset, asset_id):
            errors.append("Selected asset does not exist.")

        if not description:
            errors.append("Description is required.")
        if priority not in ("Low", "Medium", "High", "Critical"):
            errors.append("Invalid priority.")
        if status not in ("Open", "In Progress", "Resolved"):
            errors.append("Invalid status.")

        assigned_user_id = None
        if assigned_uid and assigned_uid.isdigit():
            u = db.session.get(User, int(assigned_uid))
            if u and u.is_approved:
                assigned_user_id = u.id

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template(
                "add_defect.html",
                active_page="add_defect",
                defect=None,
                assets=assets,
                corp_users=engineers,
                engineers=engineers,
                engineer_restricted=False,
                asset_types=asset_types,
                locations=locations,
                parent_candidates=parent_candidates,
                selected_asset_id=selected_asset_id,
            )

        defect = Defect(
            workspace_id=active_ws_id,
            asset_id=asset_id,
            created_by_id=current_user.id,
            assigned_user_id=assigned_user_id,
            priority=priority,
            status=status,
            description=description,
            reported_at=datetime.now(),
            expected_resolution=parse_datetime_input(request.form.get("expected_resolution")),
            resolved_at=datetime.now() if status == "Resolved" else None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.session.add(defect)
        db.session.flush()

        log = DefectStatusLog(
            defect_id=defect.id,
            old_status=None,
            new_status=defect.status,
            comment="Defect created.",
            changed_by_id=current_user.id,
            changed_at=datetime.now(),
        )
        db.session.add(log)
        db.session.commit()
        flash("Defect created successfully.", "success")
        return redirect(url_for("defects"))

    return render_template(
        "add_defect.html",
        active_page="add_defect",
        defect=None,
        assets=assets,
        corp_users=engineers,
        engineers=engineers,
        engineer_restricted=False,
        asset_types=asset_types,
        locations=locations,
        parent_candidates=parent_candidates,
        selected_asset_id=selected_asset_id,
    )


@defects_bp.route("/defects/<int:defect_id>/edit", methods=["GET", "POST"])
@approved_required
def edit_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)

    ws_role = current_user.get_role_in_workspace(defect.workspace_id)
    if ws_role == "viewer":
        abort(403)
    # Engineers may only edit defects assigned to themselves
    if ws_role == "engineer" and defect.assigned_user_id != current_user.id:
        abort(403)

    engineers = get_engineers(workspace_id=defect.workspace_id)
    assets = db.session.scalars(
        db.select(Asset)
        .where(Asset.workspace_id == defect.workspace_id)
        .order_by(Asset.asset_name)
    ).all()
    engineer_restricted = (ws_role == "engineer")

    if request.method == "POST":
        old_status = defect.status
        new_status = request.form.get("status", defect.status)
        comment = request.form.get("status_comment", "").strip()

        if new_status not in ("Open", "In Progress", "Resolved"):
            flash("Invalid status.", "danger")
            return redirect(url_for("edit_defect", defect_id=defect.id))

        if not engineer_restricted:
            asset_id_raw = request.form.get("asset_id", "").strip()
            asset_name = request.form.get("asset_name", "").strip()
            desc = request.form.get("description", "").strip()
            priority = request.form.get("priority", defect.priority)

            if asset_id_raw and asset_id_raw.isdigit():
                defect.asset_id = int(asset_id_raw)
            elif asset_name:
                matched_asset = db.session.scalar(
                    db.select(Asset).where(Asset.asset_name == asset_name, Asset.workspace_id == defect.workspace_id)
                )
                if matched_asset:
                    defect.asset_id = matched_asset.id

            if desc:
                defect.description = desc
            if priority in ("Low", "Medium", "High", "Critical"):
                defect.priority = priority

            uid = request.form.get("assigned_user_id", "")
            if uid and uid.isdigit():
                u = db.session.get(User, int(uid))
                defect.assigned_user_id = u.id if u else None
            else:
                defect.assigned_user_id = None

            defect.expected_resolution = parse_datetime_input(
                request.form.get("expected_resolution")
            )

        if new_status == "Resolved" and defect.status != "Resolved":
            defect.resolved_at = datetime.now()
        elif new_status != "Resolved":
            defect.resolved_at = None
        defect.status = new_status
        defect.updated_at = datetime.now()

        if old_status != new_status or comment:
            log = DefectStatusLog(
                defect_id=defect.id,
                old_status=old_status,
                new_status=new_status,
                comment=comment,
                changed_by_id=current_user.id,
                changed_at=datetime.now(),
            )
            db.session.add(log)

        db.session.commit()
        flash("Defect updated successfully.", "success")
        return redirect(url_for("defects"))

    return render_template(
        "add_defect.html",
        active_page="defects",
        defect=defect,
        assets=assets,
        engineer_restricted=engineer_restricted,
        corp_users=engineers,
        engineers=engineers,
    )


@defects_bp.route("/defects/<int:defect_id>/delete", methods=["POST"])
@login_required
def delete_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)
    ws_role = current_user.get_role_in_workspace(defect.workspace_id)
    if ws_role not in ("head", "manager") and current_user.role not in ("head", "manager"):
        abort(403)
    db.session.delete(defect)
    db.session.commit()
    flash("Defect deleted successfully.", "success")
    if request.form.get("return_to") == "history":
        return redirect(url_for("defect_history"))
    return redirect(url_for("defects"))
