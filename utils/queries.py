from datetime import date, datetime, timedelta
from flask import request
from sqlalchemy import or_
from extensions import db
from models.defect import Defect
from models.asset import Asset


def parse_datetime_input(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def resolve_time_window():
    selected_range = request.args.get("range", "all")
    now = datetime.now()
    if selected_range == "today":
        start = datetime.combine(date.today(), datetime.min.time())
        return start, now, selected_range
    if selected_range == "7d":
        return now - timedelta(days=7), now, selected_range
    if selected_range == "30d":
        return now - timedelta(days=30), now, selected_range
    if selected_range == "custom":
        return (
            parse_datetime_input(request.args.get("start")),
            parse_datetime_input(request.args.get("end")),
            selected_range,
        )
    return None, None, "all"


def apply_time_window(query, start_dt, end_dt):
    if start_dt is not None:
        query = query.where(Defect.reported_at >= start_dt)
    if end_dt is not None:
        query = query.where(Defect.reported_at <= end_dt)
    return query


def defect_query(include_resolved=True, start_dt=None, end_dt=None, workspace_id=None):
    """Build a defect SELECT query optionally scoped to a workspace."""
    query = db.select(Defect)
    if workspace_id is not None:
        query = query.where(Defect.workspace_id == workspace_id)
    if include_resolved is False:
        query = query.where(Defect.status != "Resolved")
    elif include_resolved == "resolved-only":
        query = query.where(Defect.status == "Resolved")
    query = apply_time_window(query, start_dt, end_dt)
    return query.order_by(Defect.reported_at.desc())


def apply_defect_search(query, search_term):
    """Filter defects by asset name or an ID entered as 123 or DFT-123."""
    if not search_term or not search_term.strip():
        return query
    normalized = search_term.strip()
    defect_id = normalized.upper().removeprefix("DFT-").strip()
    query = query.join(Asset, Defect.asset_id == Asset.id)
    filters = [Asset.asset_name.ilike(f"%{normalized}%")]
    if defect_id.isdigit():
        filters.append(Defect.id == int(defect_id))
    return query.where(or_(*filters))
