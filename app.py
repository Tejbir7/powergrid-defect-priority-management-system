import os
import secrets
import base64
import hashlib
from cryptography.fernet import Fernet
from collections import Counter
from datetime import date, datetime, timedelta
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for
from flask_bcrypt import Bcrypt
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import or_, text

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY must be set in the environment.")

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
if not app.config["SQLALCHEMY_DATABASE_URI"]:
    raise RuntimeError("DATABASE_URL must be set in the environment.")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAIL_SERVER"]   = os.getenv("MAIL_SERVER",   "smtp.gmail.com")
app.config["MAIL_PORT"]     = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"]  = os.getenv("MAIL_USE_TLS", "True") == "True"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")

db            = SQLAlchemy(app)
csrf          = CSRFProtect(app)
bcrypt        = Bcrypt(app)
mail          = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view               = "login"
login_manager.login_message            = "Please log in to access this page."
login_manager.login_message_category   = "warning"

# Make Python builtins available in Jinja2 templates
app.jinja_env.globals.update(enumerate=enumerate)


# ═══════════════════════════════════════════════════════════════
#  MODELS (9-Table 3NF Schema)
# ═══════════════════════════════════════════════════════════════

class AppSettings(db.Model):
    """Key-value store for system-wide settings (e.g. SMTP config)."""
    __tablename__ = "app_settings"
    key   = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text,       nullable=True, default="")

    @classmethod
    def get(cls, key, default=""):
        row = db.session.get(cls, key)
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = db.session.get(cls, key)
        if row:
            row.value = value
        else:
            db.session.add(cls(key=key, value=value))
        db.session.commit()


class LocationType(db.Model):
    """Lookup table for location categories (e.g. Region, Substation, Bay)."""
    __tablename__ = "location_types"
    id   = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), nullable=False, unique=True)

    locations = db.relationship("Location", backref="location_type", lazy="dynamic")


class Location(db.Model):
    """Hierarchical physical locations."""
    __tablename__ = "locations"
    id                 = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    parent_location_id = db.Column(db.Integer,     db.ForeignKey("locations.id"), nullable=True)
    location_type_id   = db.Column(db.Integer,     db.ForeignKey("location_types.id"), nullable=False)
    name               = db.Column(db.String(150), nullable=False)
    created_at         = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at         = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    parent   = db.relationship("Location", remote_side=[id], backref="children")
    assets   = db.relationship("Asset", backref="location", lazy="dynamic")


class AssetType(db.Model):
    """Lookup table for equipment categories (e.g. Transformer, Circuit Breaker)."""
    __tablename__ = "asset_types"
    id   = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    assets = db.relationship("Asset", backref="asset_type", lazy="dynamic")


class Asset(db.Model):
    """Individually identifiable equipment."""
    __tablename__ = "assets"
    id              = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    asset_type_id   = db.Column(db.Integer,     db.ForeignKey("asset_types.id"), nullable=False)
    parent_asset_id = db.Column(db.Integer,     db.ForeignKey("assets.id"), nullable=True)
    location_id     = db.Column(db.Integer,     db.ForeignKey("locations.id"), nullable=False)
    asset_name      = db.Column(db.String(150), nullable=False)
    created_at      = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at      = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    parent  = db.relationship("Asset", remote_side=[id], backref="children")
    defects = db.relationship("Defect", backref="asset", lazy="dynamic", cascade="all, delete-orphan")


@db.event.listens_for(Asset, "before_insert")
@db.event.listens_for(Asset, "before_update")
def sync_child_asset_location(mapper, connection, target):
    """Enforce 3NF invariant: Child asset location must match parent asset location."""
    if target.parent_asset_id:
        if target.parent:
            target.location_id = target.parent.location_id
        else:
            parent_loc = connection.execute(
                db.text("SELECT location_id FROM assets WHERE id = :pid"),
                {"pid": target.parent_asset_id}
            ).scalar()
            if parent_loc:
                target.location_id = parent_loc


class User(db.Model, UserMixin):
    """Engineers, managers, admins, and viewers."""
    __tablename__ = "users"
    id            = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    username      = db.Column(db.String(80),  nullable=False, unique=True)
    email         = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(20),  nullable=False, default="viewer")
    is_approved   = db.Column(db.Boolean,     nullable=False, default=False)
    created_at    = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at    = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    assigned_defects = db.relationship(
        "Defect", foreign_keys="Defect.assigned_user_id",
        backref="assigned_user", lazy="dynamic"
    )
    created_defects = db.relationship(
        "Defect", foreign_keys="Defect.created_by_id",
        backref="created_by", lazy="dynamic"
    )
    status_logs = db.relationship("DefectStatusLog", backref="changed_by", lazy=True)
    reminders   = db.relationship("Reminder", backref="user", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

    # Compatibility properties for seamless template support
    @property
    def corporation(self):
        class _Corp:
            name = "POWERGRID"
        return _Corp()

    @property
    def reminder_enabled(self):
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.enabled if r else False

    @property
    def reminder_frequency(self):
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.frequency if r else "daily"

    @property
    def reminder_hour(self):
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.hour if r else 9

    @property
    def reminder_minute(self):
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.minute if r else 0

    @property
    def reminder_day_of_week(self):
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.day_of_week if r else None

    @property
    def reminder_day_of_month(self):
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.day_of_month if r else None


class Defect(db.Model):
    """Core maintenance operational records."""
    __tablename__ = "defects"
    __table_args__ = (
        db.CheckConstraint(
            "priority IN ('Low', 'Medium', 'High', 'Critical')",
            name="ck_defect_priority"
        ),
        db.CheckConstraint(
            "status IN ('Open', 'In Progress', 'Resolved')",
            name="ck_defect_status"
        ),
    )
    id                  = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    asset_id            = db.Column(db.Integer,     db.ForeignKey("assets.id"), nullable=False)
    created_by_id       = db.Column(db.Integer,     db.ForeignKey("users.id"),  nullable=False)
    assigned_user_id    = db.Column(db.Integer,     db.ForeignKey("users.id"),  nullable=True)
    priority            = db.Column(db.String(20),  nullable=False, default="Medium")
    status              = db.Column(db.String(20),  nullable=False, default="Open")
    description         = db.Column(db.Text,        nullable=False)
    reported_at         = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    expected_resolution = db.Column(db.DateTime,    nullable=True)
    resolved_at         = db.Column(db.DateTime,    nullable=True)
    created_at          = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at          = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    status_logs = db.relationship(
        "DefectStatusLog", backref="defect", lazy=True,
        order_by="DefectStatusLog.changed_at.desc()",
        cascade="all, delete-orphan"
    )

    # Compatibility properties for seamless template support
    @property
    def asset_name(self):
        return self.asset.asset_name if self.asset else ""

    @property
    def location(self):
        return self.asset.location.name if self.asset and self.asset.location else ""

    @property
    def assigned_engineer(self):
        return self.assigned_user.username if self.assigned_user else ""

    @property
    def date_reported(self):
        return self.reported_at


class DefectStatusLog(db.Model):
    """Audit trail of defect lifecycle and notes."""
    __tablename__ = "defect_status_logs"
    id            = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    defect_id     = db.Column(db.Integer,    db.ForeignKey("defects.id"), nullable=False)
    changed_by_id = db.Column(db.Integer,    db.ForeignKey("users.id"),   nullable=False)
    old_status    = db.Column(db.String(20), nullable=True)
    new_status    = db.Column(db.String(20), nullable=False)
    comment       = db.Column(db.Text,       nullable=True)
    changed_at    = db.Column(db.DateTime,   nullable=False, default=datetime.now)


class Reminder(db.Model):
    """Personalized reminder schedule for engineers."""
    __tablename__ = "reminders"
    __table_args__ = (
        db.UniqueConstraint("user_id", "type", name="uq_reminder_user_type"),
    )
    id            = db.Column(db.Integer,      primary_key=True, autoincrement=True)
    user_id       = db.Column(db.Integer,      db.ForeignKey("users.id"), nullable=False)
    type          = db.Column(db.String(40),   nullable=False, default="DEFECT_SUMMARY")
    enabled       = db.Column(db.Boolean,      nullable=False, default=False)
    frequency     = db.Column(db.String(20),   nullable=False, default="daily")
    hour          = db.Column(db.SmallInteger, nullable=False, default=9)
    minute        = db.Column(db.SmallInteger, nullable=False, default=0)
    day_of_week   = db.Column(db.SmallInteger, nullable=True)
    day_of_month  = db.Column(db.SmallInteger, nullable=True)
    created_at    = db.Column(db.DateTime,     nullable=False, default=datetime.now)
    updated_at    = db.Column(db.DateTime,     nullable=False, default=datetime.now, onupdate=datetime.now)


# ═══════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ═══════════════════════════════════════════════════════════════

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


def get_engineers():
    """Return approved users who can be assigned defects."""
    return db.session.scalars(
        db.select(User).where(
            User.is_approved == True,
            User.role.in_(["engineer", "manager", "head"])
        ).order_by(User.username)
    ).all()


# ═══════════════════════════════════════════════════════════════
#  QUERY HELPERS
# ═══════════════════════════════════════════════════════════════

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


def defect_query(include_resolved=True, start_dt=None, end_dt=None):
    """Build a defect SELECT for all defects (single-org system)."""
    query = db.select(Defect)
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


# ═══════════════════════════════════════════════════════════════
#  EMAIL & REMINDERS
# ═══════════════════════════════════════════════════════════════

# Global scheduler instance so routes can reschedule jobs dynamically
scheduler = BackgroundScheduler()


def get_cipher():
    key = base64.urlsafe_b64encode(hashlib.sha256(app.config['SECRET_KEY'].encode()).digest())
    return Fernet(key)


def reload_mail_config():
    """Pull SMTP settings from AppSettings table into Flask-Mail config."""
    with app.app_context():
        app.config["MAIL_SERVER"]   = AppSettings.get("mail_server",   app.config.get("MAIL_SERVER",   "smtp.gmail.com"))
        app.config["MAIL_PORT"]     = int(AppSettings.get("mail_port",  str(app.config.get("MAIL_PORT",  587))))
        app.config["MAIL_USE_TLS"]  = AppSettings.get("mail_use_tls",  "True") == "True"
        app.config["MAIL_USERNAME"] = AppSettings.get("mail_username", app.config.get("MAIL_USERNAME", ""))

        encrypted_pw = AppSettings.get("mail_password", "")
        try:
            pw = get_cipher().decrypt(encrypted_pw.encode()).decode() if encrypted_pw else ""
        except Exception:
            pw = encrypted_pw
        app.config["MAIL_PASSWORD"] = pw

        mail.init_app(app)


def send_reminder_email(user, pending_defects):
    """Send a reminder email to one user."""
    smtp_user = app.config.get("MAIL_USERNAME", "")
    if not smtp_user:
        return
    subject = f"PowerGrid — You have {len(pending_defects)} pending defect(s)"
    lines = [
        f"Hi {user.username},",
        "",
        "You have the following defects pending action:",
        "",
    ]
    for d in pending_defects:
        due = (d.expected_resolution.strftime("%Y-%m-%d")
               if d.expected_resolution else "No due date")
        asset_name = d.asset.asset_name if d.asset else "Unknown Asset"
        lines.append(f"  • DFT-{d.id}: {asset_name} ({d.priority}) — Due: {due}")
    lines += ["", "Please log in to update your tasks.", "", "— PowerGrid System"]
    msg = Message(
        subject=subject,
        recipients=[user.email],
        body="\n".join(lines),
        sender=smtp_user,
    )
    try:
        mail.send(msg)
        app.logger.info(f"Reminder sent to {user.email}")
    except Exception as exc:
        app.logger.error(f"Failed to send reminder to {user.email}: {exc}")


def run_reminders_for_user(user_id):
    """APScheduler job for a single user — sends their personalised reminder."""
    with app.app_context():
        reload_mail_config()
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
            send_reminder_email(user, pending)


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
            run_reminders_for_user, "cron",
            id=job_id, day_of_week=dow, hour=h, minute=m,
            args=[reminder.user_id], replace_existing=True,
        )
    elif reminder.frequency == "monthly":
        dom = reminder.day_of_month or 1
        scheduler.add_job(
            run_reminders_for_user, "cron",
            id=job_id, day=dom, hour=h, minute=m,
            args=[reminder.user_id], replace_existing=True,
        )
    else:  # daily (default)
        scheduler.add_job(
            run_reminders_for_user, "cron",
            id=job_id, hour=h, minute=m,
            args=[reminder.user_id], replace_existing=True,
        )


def bootstrap_all_user_schedules():
    """Called at startup to register existing user reminder preferences."""
    with app.app_context():
        active_reminders = db.session.scalars(
            db.select(Reminder).where(Reminder.enabled == True)
        ).all()
        for reminder in active_reminders:
            schedule_user_reminder(reminder)


# ═══════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/register", methods=["GET", "POST"])
def register():
    user_count = db.session.scalar(
        db.select(db.func.count()).select_from(User)
    )
    if user_count and user_count > 0:
        flash("System already initialized. Contact the Head administrator for access.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email",    "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        errors = []
        if not username:        errors.append("Username is required.")
        if not email:           errors.append("Email is required.")
        if len(password) < 6:   errors.append("Password must be at least 6 characters.")
        if password != confirm:  errors.append("Passwords do not match.")
        if db.session.scalar(db.select(User).where(User.email == email)):
            errors.append("Email already registered.")
        if db.session.scalar(db.select(User).where(User.username == username)):
            errors.append("Username already taken.")

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template("register.html")

        user = User(
            email=email, username=username,
            role="head", is_approved=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("PowerGRID system initialized. You are the Head administrator.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    user_count = db.session.scalar(
        db.select(db.func.count()).select_from(User)
    )
    if not user_count:
        return redirect(url_for("register"))

    if request.method == "POST":
        email    = request.form.get("email",    "").strip().lower()
        password = request.form.get("password", "")
        user     = db.session.scalar(db.select(User).where(User.email == email))

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        login_user(user, remember=request.form.get("remember") == "on")

        if not user.is_approved:
            return redirect(url_for("pending_approval"))

        # In-app reminder banner
        pending_count = db.session.scalar(
            db.select(db.func.count()).select_from(Defect).where(
                Defect.assigned_user_id == user.id,
                Defect.status != "Resolved",
            )
        ) or 0
        if pending_count:
            flash(
                f"⚠️ You have {pending_count} pending defect(s) assigned to you.",
                "reminder",
            )

        next_page = request.args.get("next")
        if not next_page or not next_page.startswith('/') or next_page.startswith('//'):
            next_page = url_for("dashboard")
        return redirect(next_page)

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/pending-approval")
@login_required
def pending_approval():
    if current_user.is_approved:
        return redirect(url_for("dashboard"))
    return render_template("pending_approval.html")


# ═══════════════════════════════════════════════════════════════
#  ADMIN ROUTES (Head only)
# ═══════════════════════════════════════════════════════════════

@app.route("/admin/users")
@role_required("head")
def admin_users():
    users = db.session.scalars(
        db.select(User).order_by(User.created_at.asc())
    ).all()
    return render_template(
        "admin_users.html",
        active_page="admin_users",
        users=users,
    )


@app.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@role_required("head")
def approve_user(user_id):
    user = db.get_or_404(User, user_id)
    user.is_approved = True
    db.session.commit()
    flash(f"{user.username} has been approved and can now log in.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reject", methods=["POST"])
@role_required("head")
def reject_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot remove yourself.", "danger")
        return redirect(url_for("admin_users"))

    # Preserve audit integrity: users who authored defects or logs cannot be hard-deleted
    has_authored_defects = db.session.scalar(
        db.select(db.func.count()).select_from(Defect).where(Defect.created_by_id == user.id)
    ) > 0
    has_status_logs = db.session.scalar(
        db.select(db.func.count()).select_from(DefectStatusLog).where(DefectStatusLog.changed_by_id == user.id)
    ) > 0

    if has_authored_defects or has_status_logs:
        user.is_approved = False
        db.session.commit()
        flash(f"{user.username} has defect history. Access revoked (disabled) to preserve audit records.", "warning")
        return redirect(url_for("admin_users"))

    # Safely unassign any active defects before removing the user
    db.session.execute(
        db.update(Defect).where(Defect.assigned_user_id == user.id).values(assigned_user_id=None)
    )

    db.session.delete(user)
    db.session.commit()
    flash(f"User {user.username} removed.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
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
    flash(f"{user.username}'s role updated to {new_role.title()}.", "success")
    return redirect(url_for("admin_users"))


# ═══════════════════════════════════════════════════════════════
#  MAIN APP ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/search-defects")
@approved_required
def search_defects():
    destination = "defect_history" if request.args.get("scope") == "resolved" else "defects"
    return redirect(url_for(destination, q=request.args.get("q", "").strip()))


@app.route("/dashboard")
@approved_required
def dashboard():
    active_defects = db.session.scalars(defect_query(include_resolved=False)).all()

    # Engineers see their own assigned defects on dashboard
    my_defects = None
    if current_user.role == "engineer":
        my_defects = [d for d in active_defects if d.assigned_user_id == current_user.id]

    recent_defects  = active_defects[:5]
    status_counts   = Counter(d.status   for d in active_defects)
    priority_counts = Counter(d.priority for d in active_defects)
    now             = datetime.now()
    start_of_today  = datetime.combine(date.today(), datetime.min.time())

    resolved_today = db.session.scalars(
        db.select(Defect).where(
            Defect.status == "Resolved",
            Defect.resolved_at >= start_of_today,
            Defect.resolved_at <= now,
        )
    ).all()

    dashboard_cards = [
        {"label": "Open",     "value": status_counts.get("Open", 0),  "icon": "O", "tone": "bg-yellow"},
        {
            "label": "Overdue",
            "value": sum(
                d.expected_resolution is not None and d.expected_resolution < now
                for d in active_defects
            ),
            "icon": "!", "tone": "bg-red",
        },
        {
            "label": "Unassigned",
            "value": sum(d.assigned_user_id is None for d in active_defects),
            "icon": "U", "tone": "bg-azure",
        },
        {"label": "Resolved Today", "value": len(resolved_today), "icon": "R", "tone": "bg-green"},
    ]

    priority_order = ["Critical", "High", "Medium", "Low"]
    priority_breakdown = [
        {
            "label":    p,
            "count":    priority_counts.get(p, 0),
            "progress": round(
                (priority_counts.get(p, 0) / len(active_defects) * 100) if active_defects else 0
            ),
        }
        for p in priority_order if priority_counts.get(p, 0)
    ]

    status_order = ["Open", "In Progress"]
    status_breakdown = [
        {
            "label":    s,
            "count":    status_counts.get(s, 0),
            "progress": round(
                (status_counts.get(s, 0) / len(active_defects) * 100) if active_defects else 0
            ),
        }
        for s in status_order if status_counts.get(s, 0)
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


@app.route("/defects")
@approved_required
def defects():
    start_dt, end_dt, selected_range = resolve_time_window()
    search_query = request.args.get("q", "").strip()
    query        = defect_query(include_resolved=False, start_dt=start_dt, end_dt=end_dt)
    all_defects  = db.session.scalars(apply_defect_search(query, search_query)).all()

    selected_defect_id = request.args.get("defect_id", type=int)
    selected_defect    = next((d for d in all_defects if d.id == selected_defect_id), None)
    if selected_defect is None and all_defects:
        selected_defect    = all_defects[0]
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


@app.route("/defect-history")
@approved_required
def defect_history():
    start_dt, end_dt, selected_range = resolve_time_window()
    search_query     = request.args.get("q", "").strip()
    resolved_defects = db.session.scalars(
        apply_defect_search(
            defect_query(include_resolved="resolved-only", start_dt=start_dt, end_dt=end_dt),
            search_query,
        )
    ).all()

    selected_defect_id = request.args.get("defect_id", type=int)
    selected_defect    = next((d for d in resolved_defects if d.id == selected_defect_id), None)
    if selected_defect is None and resolved_defects:
        selected_defect    = resolved_defects[0]
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


@app.route("/defects/add", methods=["GET", "POST"])
@role_required("head", "manager", "engineer")
def add_defect():
    engineers = get_engineers()
    assets    = db.session.scalars(db.select(Asset).order_by(Asset.asset_name)).all()

    if request.method == "POST":
        asset_id_raw = request.form.get("asset_id", "").strip()
        asset_name   = request.form.get("asset_name", "").strip()
        description  = request.form.get("description", "").strip()
        priority     = request.form.get("priority", "Medium") if current_user.role != "engineer" else "Medium"
        status       = request.form.get("status", "Open")
        assigned_uid = request.form.get("assigned_user_id", "").strip()

        errors = []
        asset_id = int(asset_id_raw) if asset_id_raw.isdigit() else None
        if not asset_id and asset_name:
            # Fallback for name matching during template transition
            matched_asset = db.session.scalar(db.select(Asset).where(Asset.asset_name == asset_name))
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
            )

        defect = Defect(
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
    )


@app.route("/defects/<int:defect_id>/edit", methods=["GET", "POST"])
@approved_required
def edit_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)

    if current_user.role == "viewer":
        abort(403)
    # Engineers may only edit defects assigned to themselves
    if current_user.role == "engineer" and defect.assigned_user_id != current_user.id:
        abort(403)

    engineers = get_engineers()
    assets    = db.session.scalars(db.select(Asset).order_by(Asset.asset_name)).all()
    engineer_restricted = current_user.role == "engineer"

    if request.method == "POST":
        old_status = defect.status
        new_status = request.form.get("status", defect.status)
        comment    = request.form.get("status_comment", "").strip()

        if new_status not in ("Open", "In Progress", "Resolved"):
            flash("Invalid status.", "danger")
            return redirect(url_for("edit_defect", defect_id=defect.id))

        if not engineer_restricted:
            asset_id_raw = request.form.get("asset_id", "").strip()
            asset_name   = request.form.get("asset_name", "").strip()
            desc         = request.form.get("description", "").strip()
            priority     = request.form.get("priority", defect.priority)

            if asset_id_raw and asset_id_raw.isdigit():
                defect.asset_id = int(asset_id_raw)
            elif asset_name:
                matched_asset = db.session.scalar(db.select(Asset).where(Asset.asset_name == asset_name))
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
                defect_id     = defect.id,
                old_status    = old_status,
                new_status    = new_status,
                comment       = comment,
                changed_by_id = current_user.id,
                changed_at    = datetime.now(),
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


@app.route("/defects/<int:defect_id>/delete", methods=["POST"])
@role_required("head", "manager")
def delete_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)
    db.session.delete(defect)
    db.session.commit()
    flash("Defect deleted successfully.", "success")
    if request.form.get("return_to") == "history":
        return redirect(url_for("defect_history"))
    return redirect(url_for("defects"))


@app.route("/assets")
@approved_required
def assets():
    start_dt, end_dt, selected_range = resolve_time_window()
    all_assets = db.session.scalars(
        db.select(Asset).order_by(Asset.asset_name)
    ).all()

    asset_rows = []
    total_defects_count = 0
    total_active_count = 0
    total_resolved_count = 0

    for asset in all_assets:
        defect_q = db.select(Defect).where(Defect.asset_id == asset.id)
        defect_q = apply_time_window(defect_q, start_dt, end_dt)
        asset_defects = db.session.scalars(defect_q).all()

        total    = len(asset_defects)
        active   = sum(1 for d in asset_defects if d.status != "Resolved")
        resolved = total - active
        last_act = max((d.reported_at for d in asset_defects), default=None)

        total_defects_count += total
        total_active_count += active
        total_resolved_count += resolved

        asset_rows.append({
            "asset_name":       asset.asset_name,
            "asset_type":       asset.asset_type.name if asset.asset_type else "General",
            "location":         asset.location.name if asset.location else "Main Site",
            "total_defects":    total,
            "active_defects":   active,
            "resolved_defects": resolved,
            "last_activity":    last_act,
        })

    asset_rows.sort(key=lambda r: r["last_activity"] or datetime.min, reverse=True)
    return render_template(
        "assets.html",
        active_page="assets",
        assets=asset_rows,
        total_assets=len(asset_rows),
        total_defects=total_defects_count,
        active_defects=total_active_count,
        resolved_defects=total_resolved_count,
        selected_range=selected_range,
        filter_start=request.args.get("start", ""),
        filter_end=request.args.get("end", ""),
    )


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


# ═══════════════════════════════════════════════════════════════
#  SETTINGS ROUTE
# ═══════════════════════════════════════════════════════════════

@app.route("/settings", methods=["GET", "POST"])
@approved_required
def settings():
    reminder = db.session.scalar(
        db.select(Reminder).where(
            Reminder.user_id == current_user.id,
            Reminder.type == "DEFECT_SUMMARY",
        )
    )

    if request.method == "POST":
        action = request.form.get("action", "reminder")

        if action == "smtp" and current_user.role == "head":
            AppSettings.set("mail_server",  request.form.get("mail_server",  "smtp.gmail.com").strip())
            AppSettings.set("mail_port",    request.form.get("mail_port",    "587").strip())
            AppSettings.set("mail_use_tls", "True" if request.form.get("mail_use_tls") else "False")
            AppSettings.set("mail_username", request.form.get("mail_username", "").strip())
            new_pw = request.form.get("mail_password", "").strip()
            if new_pw:
                encrypted_pw = get_cipher().encrypt(new_pw.encode()).decode()
                AppSettings.set("mail_password", encrypted_pw)
            reload_mail_config()
            flash("SMTP settings saved and applied.", "success")

        elif action == "reminder":
            freq     = request.form.get("frequency", "daily")
            time_str = request.form.get("reminder_time", "09:00")
            try:
                h, m = map(int, time_str.split(":"))
            except (ValueError, AttributeError):
                h, m = 9, 0

            dow     = request.form.get("day_of_week",  type=int)
            dom     = request.form.get("day_of_month", type=int)
            enabled = bool(request.form.get("reminder_enabled"))

            if not reminder:
                reminder = Reminder(
                    user_id=current_user.id,
                    type="DEFECT_SUMMARY",
                )
                db.session.add(reminder)

            reminder.enabled       = enabled
            reminder.frequency     = freq
            reminder.hour          = h
            reminder.minute        = m
            reminder.day_of_week   = dow
            reminder.day_of_month  = dom
            reminder.updated_at    = datetime.now()
            db.session.commit()

            schedule_user_reminder(reminder)

            if reminder.enabled:
                freq_label = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}.get(freq, freq)
                flash(f"Email reminder set to {freq_label} at {time_str}.", "success")
            else:
                flash("Email reminders disabled.", "info")

        return redirect(url_for("settings"))

    # GET
    smtp_configured = bool(AppSettings.get("mail_username"))
    smtp_settings = {
        "server":   AppSettings.get("mail_server",  "smtp.gmail.com"),
        "port":     AppSettings.get("mail_port",    "587"),
        "use_tls":  AppSettings.get("mail_use_tls", "True") == "True",
        "username": AppSettings.get("mail_username", ""),
    }

    days_of_week  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_of_month = list(range(1, 29))

    return render_template(
        "settings.html",
        active_page="settings",
        reminder=reminder,
        smtp_configured=smtp_configured,
        smtp_settings=smtp_settings,
        days_of_week=days_of_week,
        days_of_month=days_of_month,
        now=datetime.now(),
    )


# ═══════════════════════════════════════════════════════════════
#  SEED & INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def seed_demo_data():
    """Populate initial 3NF master data and sample operational records."""
    if db.session.scalar(db.select(db.func.count()).select_from(User)) > 0:
        return

    # 1. Location Types
    region_type = LocationType(name="Region")
    sub_type    = LocationType(name="Substation")
    bay_type    = LocationType(name="Bay")
    db.session.add_all([region_type, sub_type, bay_type])
    db.session.flush()

    # 2. Locations (Punjab hierarchy: Region -> Substation -> Bay)
    punjab = Location(name="Punjab Region", location_type_id=region_type.id)
    db.session.add(punjab)
    db.session.flush()

    asr = Location(name="Amritsar Substation", location_type_id=sub_type.id, parent_location_id=punjab.id)
    lud = Location(name="Ludhiana Substation", location_type_id=sub_type.id, parent_location_id=punjab.id)
    db.session.add_all([asr, lud])
    db.session.flush()

    bay1 = Location(name="Bay 1", location_type_id=bay_type.id, parent_location_id=asr.id)
    bay2 = Location(name="Bay 2", location_type_id=bay_type.id, parent_location_id=asr.id)
    db.session.add_all([bay1, bay2])
    db.session.flush()

    # 3. Asset Types
    tf_type  = AssetType(name="Transformer")
    cb_type  = AssetType(name="Circuit Breaker")
    fan_type = AssetType(name="Cooling Fan")
    db.session.add_all([tf_type, cb_type, fan_type])
    db.session.flush()

    # 4. Assets
    t1 = Asset(asset_name="Transformer T1", asset_type_id=tf_type.id, location_id=bay1.id)
    t2 = Asset(asset_name="Transformer T2", asset_type_id=tf_type.id, location_id=bay2.id)
    cb1 = Asset(asset_name="Circuit Breaker CB-01", asset_type_id=cb_type.id, location_id=asr.id)
    db.session.add_all([t1, t2, cb1])
    db.session.flush()

    # Child Assets (Fans on T1 - inheriting T1 bay1 location)
    fan1 = Asset(asset_name="Cooling Fan 1", asset_type_id=fan_type.id, location_id=bay1.id, parent_asset_id=t1.id)
    fan2 = Asset(asset_name="Cooling Fan 2", asset_type_id=fan_type.id, location_id=bay1.id, parent_asset_id=t1.id)
    db.session.add_all([fan1, fan2])
    db.session.flush()

    # 5. Default Users
    head = User(username="admin", email="admin@powergrid.in", role="head", is_approved=True, updated_at=datetime.now())
    head.set_password("powergrid123")

    engineer = User(username="engineer1", email="engineer1@powergrid.in", role="engineer", is_approved=True, updated_at=datetime.now())
    engineer.set_password("powergrid123")

    db.session.add_all([head, engineer])
    db.session.flush()

    # 6. Demo Defects
    now = datetime.now()
    d1 = Defect(
        asset_id=t1.id,
        created_by_id=head.id,
        assigned_user_id=engineer.id,
        priority="Critical",
        status="Open",
        description="Oil leakage observed from main tank near valve 2.",
        reported_at=now - timedelta(days=2),
        expected_resolution=now + timedelta(days=1),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )
    d2 = Defect(
        asset_id=fan1.id,
        created_by_id=engineer.id,
        assigned_user_id=engineer.id,
        priority="High",
        status="In Progress",
        description="Cooling fan vibrating abnormally during peak load.",
        reported_at=now - timedelta(days=1),
        expected_resolution=now + timedelta(days=3),
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=4),
    )
    d3 = Defect(
        asset_id=cb1.id,
        created_by_id=head.id,
        assigned_user_id=None,
        priority="Medium",
        status="Open",
        description="Contact resistance out of tolerance on Phase B.",
        reported_at=now - timedelta(hours=6),
        expected_resolution=now + timedelta(days=5),
        created_at=now - timedelta(hours=6),
        updated_at=now - timedelta(hours=6),
    )
    db.session.add_all([d1, d2, d3])
    db.session.flush()

    # 7. Status Logs
    for d in [d1, d2, d3]:
        db.session.add(DefectStatusLog(
            defect_id=d.id,
            changed_by_id=d.created_by_id,
            old_status=None,
            new_status=d.status,
            comment="Initial defect report.",
            changed_at=d.created_at,
        ))

    db.session.commit()
    app.logger.info("Demo data seeded successfully.")


@app.cli.command("seed-db")
def seed_db_command():
    """CLI command to seed demo data."""
    seed_demo_data()
    print("Database seeded with demo data.")


@app.cli.command("reset-db")
def reset_db_command():
    """Drop and recreate all tables, then seed with demo data."""
    db.drop_all()
    db.create_all()
    seed_demo_data()
    print("Database reset and seeded with demo data.")


# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_demo_data()
        reload_mail_config()
        bootstrap_all_user_schedules()

    scheduler.start()

    try:
        debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
        app.run(debug=debug_mode)
    finally:
        scheduler.shutdown()
