import os
from collections import Counter
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import inspect, text

load_dotenv()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY must be set in the environment.")

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
if not app.config["SQLALCHEMY_DATABASE_URI"]:
    raise RuntimeError("DATABASE_URL must be set in the environment.")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
csrf = CSRFProtect(app)


class Defect(db.Model):
    __tablename__ = "defects"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_name = db.Column(db.String(150), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    priority = db.Column(db.String(20), nullable=False, default="Medium")
    status = db.Column(db.String(20), nullable=False, default="Open")
    description = db.Column(db.Text, nullable=True)
    assigned_engineer = db.Column(db.String(120), nullable=True)
    date_reported = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expected_resolution = db.Column(db.DateTime, nullable=True)


class Asset(db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_name = db.Column(db.String(150), nullable=False, unique=True)
    location = db.Column(db.String(200), nullable=False)
    health_status = db.Column(db.String(20), nullable=False, default="Good")
    last_activity = db.Column(db.DateTime, nullable=True)


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
        end = now
        return start, end, selected_range

    if selected_range == "7d":
        return now - timedelta(days=7), now, selected_range

    if selected_range == "30d":
        return now - timedelta(days=30), now, selected_range

    if selected_range == "custom":
        return parse_datetime_input(request.args.get("start")), parse_datetime_input(request.args.get("end")), selected_range

    return None, None, "all"


def apply_time_window(query, start_dt, end_dt):
    if start_dt is not None:
        query = query.where(Defect.date_reported >= start_dt)
    if end_dt is not None:
        query = query.where(Defect.date_reported <= end_dt)
    return query


def build_defect_query(include_resolved=True, start_dt=None, end_dt=None):
    query = db.select(Defect)
    if include_resolved is False:
        query = query.where(Defect.status != "Resolved")
    elif include_resolved == "resolved-only":
        query = query.where(Defect.status == "Resolved")

    query = apply_time_window(query, start_dt, end_dt)
    return query.order_by(Defect.date_reported.desc())


def build_asset_summaries(defects):
    grouped = {}
    for defect in defects:
        summary = grouped.setdefault(
            defect.asset_name,
            {
                "asset_name": defect.asset_name,
                "location": defect.location,
                "total_defects": 0,
                "active_defects": 0,
                "resolved_defects": 0,
                "last_activity": defect.date_reported,
            },
        )
        summary["total_defects"] += 1
        if defect.status == "Resolved":
            summary["resolved_defects"] += 1
        else:
            summary["active_defects"] += 1

        if defect.date_reported and (
            summary["last_activity"] is None or defect.date_reported > summary["last_activity"]
        ):
            summary["last_activity"] = defect.date_reported
            summary["location"] = defect.location

    return sorted(
        grouped.values(),
        key=lambda row: (row["last_activity"] or datetime.min),
        reverse=True,
    )


def ensure_datetime_schema():
    inspector = inspect(db.engine)
    dialect_name = db.engine.dialect.name

    def column_type(table_name, column_name):
        for column in inspector.get_columns(table_name):
            if column["name"] == column_name:
                return str(column["type"]).upper()
        return ""

    if dialect_name not in {"mysql", "mariadb"}:
        return

    statements = []
    if "DATETIME" not in column_type("defects", "date_reported"):
        statements.append(
            "ALTER TABLE defects MODIFY COLUMN date_reported DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "DATETIME" not in column_type("defects", "expected_resolution"):
        statements.append(
            "ALTER TABLE defects MODIFY COLUMN expected_resolution DATETIME NULL"
        )
    if "DATETIME" not in column_type("assets", "last_activity"):
        statements.append(
            "ALTER TABLE assets MODIFY COLUMN last_activity DATETIME NULL"
        )

    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()

def save_defect_form(defect):
    """Copy validated form data into a Defect object."""
    defect.asset_name = request.form["asset_name"].strip()
    defect.location = request.form["location"].strip()
    defect.priority = request.form["priority"]
    defect.status = request.form["status"]
    defect.description = request.form["description"].strip()
    defect.assigned_engineer = request.form["assigned_engineer"].strip()

    defect.expected_resolution = parse_datetime_input(request.form.get("expected_resolution"))

@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    active_defects = db.session.execute(build_defect_query(include_resolved=False)).scalars().all()

    recent_defects = active_defects[:5]
    status_counts = Counter(defect.status for defect in active_defects)
    priority_counts = Counter(defect.priority for defect in active_defects)

    dashboard_cards = [
        {
            "label": "Active Defects",
            "value": len(active_defects),
            "icon": "A",
            "tone": "bg-blue",
        },
        {
            "label": "Open",
            "value": status_counts.get("Open", 0),
            "icon": "O",
            "tone": "bg-yellow",
        },
        {
            "label": "In Progress",
            "value": status_counts.get("In Progress", 0),
            "icon": "P",
            "tone": "bg-azure",
        },
        {
            "label": "Critical",
            "value": priority_counts.get("Critical", 0),
            "icon": "C",
            "tone": "bg-red",
        },
    ]

    priority_order = ["Critical", "High", "Medium", "Low"]
    priority_breakdown = [
        {
            "label": priority,
            "count": priority_counts.get(priority, 0),
            "progress": round((priority_counts.get(priority, 0) / len(active_defects) * 100) if active_defects else 0),
        }
        for priority in priority_order
        if priority_counts.get(priority, 0)
    ]

    status_order = ["Open", "In Progress"]
    status_breakdown = [
        {
            "label": status,
            "count": status_counts.get(status, 0),
            "progress": round((status_counts.get(status, 0) / len(active_defects) * 100) if active_defects else 0),
        }
        for status in status_order
        if status_counts.get(status, 0)
    ]

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        dashboard_cards=dashboard_cards,
        recent_defects=recent_defects,
        priority_breakdown=priority_breakdown,
        status_breakdown=status_breakdown,
        total_defects=len(active_defects),
    )

# READ: show all defects
@app.route("/defects")
def defects():
    start_dt, end_dt, selected_range = resolve_time_window()
    all_defects = db.session.execute(
        build_defect_query(include_resolved=False, start_dt=start_dt, end_dt=end_dt)
    ).scalars().all()
    selected_defect_id = request.args.get("defect_id", type=int)
    selected_defect = next((defect for defect in all_defects if defect.id == selected_defect_id), None)
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
    )


@app.route("/defect-history")
def defect_history():
    start_dt, end_dt, selected_range = resolve_time_window()
    resolved_defects = db.session.execute(
        build_defect_query(include_resolved="resolved-only", start_dt=start_dt, end_dt=end_dt)
    ).scalars().all()
    selected_defect_id = request.args.get("defect_id", type=int)
    selected_defect = next((defect for defect in resolved_defects if defect.id == selected_defect_id), None)
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
    )

#CREATE: show table and save a new defect
@app.route("/defects/add", methods=["GET", "POST"])
def add_defect():
     if request.method == "POST":
        defect = Defect()
        save_defect_form(defect)

        db.session.add(defect)
        db.session.commit()

        flash("Defect created successfully.", "success")
        return redirect(url_for("defects"))

     return render_template(
            "add_defect.html", active_page="add_defect", defect = None,
                               )
#UPDATE: show current data and save changes
# UPDATE: show current data and save changes
@app.route("/defects/<int:defect_id>/edit", methods=["GET", "POST"])
def edit_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)

    if request.method == "POST":
        save_defect_form(defect)
        db.session.commit()

        flash("Defect updated successfully.", "success")
        return redirect(url_for("defects"))

    return render_template(
        "add_defect.html",
        active_page="defects",
        defect=defect,
    )
# DELETE: only accept POST, never GET
@app.route("/defects/<int:defect_id>/delete", methods=["POST"])
def delete_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)

    db.session.delete(defect)
    db.session.commit()

    flash("Defect deleted successfully.", "success")
    return redirect(url_for("defects"))

@app.route("/assets")
def assets():
    start_dt, end_dt, selected_range = resolve_time_window()
    filtered_defects = db.session.execute(
        build_defect_query(start_dt=start_dt, end_dt=end_dt)
    ).scalars().all()
    asset_rows = build_asset_summaries(filtered_defects)

    total_defects = len(filtered_defects)
    active_defects = sum(asset["active_defects"] for asset in asset_rows)
    resolved_defects = sum(asset["resolved_defects"] for asset in asset_rows)
    return render_template(
        "assets.html",
        active_page="assets",
        assets=asset_rows,
        total_assets=len(asset_rows),
        total_defects=total_defects,
        active_defects=active_defects,
        resolved_defects=resolved_defects,
        selected_range=selected_range,
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
    )

@app.route("/reports")
def reports():
    return render_template("reports.html", active_page="reports")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Creates the defects table if it does not exist.
        ensure_datetime_schema()

    app.run()
