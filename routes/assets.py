from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from extensions import db
from models.asset import Asset, AssetType
from models.location import Location, LocationType
from models.defect import Defect
from utils.auth import approved_required
from utils.queries import resolve_time_window, apply_time_window

assets_bp = Blueprint("assets", __name__)

STANDARD_ASSET_TYPES = [
    "Transformer",
    "Circuit Breaker",
    "Cooling Fan",
    "Shunt Reactor",
    "Isolator / Disconnector",
    "Current Transformer (CT)",
    "Potential Transformer (PT)",
    "Busbar System",
    "Control Relay Panel",
    "Surge Arrester",
]


def ensure_standard_asset_types():
    """Ensure standard grid electrical equipment categories are available."""
    existing = set(db.session.scalars(db.select(AssetType.name)).all())
    to_add = [AssetType(name=name) for name in STANDARD_ASSET_TYPES if name not in existing]
    if to_add:
        db.session.add_all(to_add)
        db.session.commit()


@assets_bp.route("/assets")
@approved_required
def assets():
    active_ws_id = session.get("active_workspace_id")
    if not active_ws_id and current_user.workspaces:
        active_ws_id = current_user.workspaces[0].id
        session["active_workspace_id"] = active_ws_id

    ensure_standard_asset_types()

    start_dt, end_dt, selected_range = resolve_time_window()
    q = db.select(Asset)
    if active_ws_id:
        q = q.where(Asset.workspace_id == active_ws_id)
    all_assets = db.session.scalars(
        q.order_by(Asset.parent_asset_id.asc(), Asset.asset_name.asc())
    ).all()

    asset_rows = []
    total_defects_count = 0
    total_active_count = 0
    total_resolved_count = 0
    primary_assets_count = 0
    components_count = 0

    for asset in all_assets:
        defect_q = db.select(Defect).where(Defect.asset_id == asset.id)
        defect_q = apply_time_window(defect_q, start_dt, end_dt)
        asset_defects = db.session.scalars(defect_q).all()

        total = len(asset_defects)
        active = sum(1 for d in asset_defects if d.status != "Resolved")
        resolved = total - active
        last_act = max((d.reported_at for d in asset_defects), default=None)

        total_defects_count += total
        total_active_count += active
        total_resolved_count += resolved

        if asset.parent_asset_id:
            components_count += 1
        else:
            primary_assets_count += 1

        child_count = len(asset.children) if asset.children else 0

        asset_rows.append({
            "id": asset.id,
            "asset_name": asset.asset_name,
            "asset_type": asset.asset_type.name if asset.asset_type else "Equipment",
            "location": asset.location.name if asset.location else "Main Site",
            "location_path": asset.location.full_path if asset.location else "Main Site",
            "location_badges": asset.location.path_list if asset.location else [],
            "is_component": asset.is_component,
            "parent_name": asset.parent_name,
            "child_count": child_count,
            "total_defects": total,
            "active_defects": active,
            "resolved_defects": resolved,
            "last_activity": last_act,
        })

    # Available options for Add Asset modal
    asset_types = db.session.scalars(db.select(AssetType).order_by(AssetType.name.asc())).all()
    locations = []
    parent_candidates = []
    if active_ws_id:
        locations = db.session.scalars(
            db.select(Location).where(Location.workspace_id == active_ws_id).order_by(Location.name.asc())
        ).all()
        parent_candidates = db.session.scalars(
            db.select(Asset).where(Asset.workspace_id == active_ws_id, Asset.parent_asset_id == None).order_by(Asset.asset_name.asc())
        ).all()

    return render_template(
        "assets.html",
        active_page="assets",
        assets=asset_rows,
        total_assets=len(asset_rows),
        primary_assets_count=primary_assets_count,
        components_count=components_count,
        total_defects=total_defects_count,
        active_defects=total_active_count,
        resolved_defects=total_resolved_count,
        selected_range=selected_range,
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
        asset_types=asset_types,
        locations=locations,
        parent_candidates=parent_candidates,
    )


@assets_bp.route("/assets/add", methods=["POST"])
@approved_required
def add_asset():
    """Register new high-voltage equipment or component in active workspace."""
    active_ws_id = session.get("active_workspace_id")
    if not active_ws_id and current_user.workspaces:
        active_ws_id = current_user.workspaces[0].id
        session["active_workspace_id"] = active_ws_id

    if not active_ws_id:
        flash("Please select a grid workspace before adding equipment.", "warning")
        return redirect(url_for("workspace.workspaces_hub"))

    asset_name = request.form.get("asset_name", "").strip()
    asset_type_id_raw = request.form.get("asset_type_id", "").strip()
    new_asset_type_name = request.form.get("new_asset_type", "").strip()
    location_id_raw = request.form.get("location_id", "").strip()
    parent_asset_id_raw = request.form.get("parent_asset_id", "").strip()
    redirect_to = request.form.get("redirect_to", "").strip()

    if not asset_name:
        flash("Equipment / Component name is required.", "danger")
        return redirect(redirect_to or url_for("assets"))

    # Resolve Asset Type
    asset_type = None
    if new_asset_type_name:
        asset_type = db.session.scalar(db.select(AssetType).where(AssetType.name == new_asset_type_name))
        if not asset_type:
            asset_type = AssetType(name=new_asset_type_name)
            db.session.add(asset_type)
            db.session.flush()
    elif asset_type_id_raw and asset_type_id_raw.isdigit():
        asset_type = db.session.get(AssetType, int(asset_type_id_raw))

    if not asset_type:
        flash("Please select or enter an equipment category.", "danger")
        return redirect(redirect_to or url_for("assets"))

    # Resolve Parent Asset (for components)
    parent_asset = None
    if parent_asset_id_raw and parent_asset_id_raw.isdigit():
        parent_asset = db.session.get(Asset, int(parent_asset_id_raw))

    # Resolve Location
    location_id = None
    if parent_asset:
        location_id = parent_asset.location_id
    elif location_id_raw and location_id_raw.isdigit():
        location_id = int(location_id_raw)
    else:
        # Fallback to first location in workspace, or auto-create Bay
        ws_loc = db.session.scalar(db.select(Location).where(Location.workspace_id == active_ws_id))
        if not ws_loc:
            bay_type = db.session.scalar(db.select(LocationType).where(LocationType.name == "Bay"))
            if not bay_type:
                bay_type = LocationType(name="Bay")
                db.session.add(bay_type)
                db.session.flush()
            ws_loc = Location(name="Main Substation Bay 1", location_type_id=bay_type.id, workspace_id=active_ws_id)
            db.session.add(ws_loc)
            db.session.flush()
        location_id = ws_loc.id

    new_asset = Asset(
        asset_name=asset_name,
        asset_type_id=asset_type.id,
        location_id=location_id,
        parent_asset_id=parent_asset.id if parent_asset else None,
        workspace_id=active_ws_id,
    )
    db.session.add(new_asset)
    db.session.commit()

    flash(f"Equipment '{new_asset.asset_name}' registered successfully.", "success")
    if redirect_to and redirect_to.startswith("/"):
        return redirect(f"{redirect_to}?selected_asset_id={new_asset.id}")
    return redirect(url_for("assets"))
