import os
import secrets
import base64
import hashlib
from cryptography.fernet import Fernet
from collections import Counter
from datetime import date, datetime, timedelta
from functools import wraps
from itertools import groupby

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
from sqlalchemy import inspect, or_, text

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

db           = SQLAlchemy(app)
csrf         = CSRFProtect(app)
bcrypt       = Bcrypt(app)
mail         = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view          = "login"
login_manager.login_message       = "Please log in to access this page."
login_manager.login_message_category = "warning"

# Make Python builtins available in Jinja2 templates
app.jinja_env.globals.update(enumerate=enumerate)


# ═══════════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════════

class Corporation(db.Model):
    __tablename__ = "corporations"
    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name        = db.Column(db.String(150), nullable=False, unique=True)
    invite_code = db.Column(db.String(32),  nullable=False, unique=True)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    users   = db.relationship("User",   backref="corporation", lazy="dynamic")
    defects = db.relationship("Defect", backref="corporation", lazy="dynamic")


class AppSettings(db.Model):
    """Key-value store for system-wide settings (e.g. SMTP config).
    Stored in DB so they survive Heroku dynos."""
    __tablename__ = "app_settings"
    key   = db.Column(db.String(80),  primary_key=True)
    value = db.Column(db.String(500), nullable=True, default="")

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


class User(db.Model, UserMixin):
    __tablename__ = "users"
    id              = db.Column(db.Integer,      primary_key=True, autoincrement=True)
    email           = db.Column(db.String(120),  nullable=False, unique=True)
    username        = db.Column(db.String(80),   nullable=False)
    password_hash   = db.Column(db.String(255),  nullable=False)
    role            = db.Column(db.String(20),   nullable=False, default="viewer")
    corporation_id  = db.Column(db.Integer,      db.ForeignKey("corporations.id"), nullable=False)
    is_approved     = db.Column(db.Boolean,      nullable=False, default=False)
    created_at      = db.Column(db.DateTime,     nullable=False, default=datetime.utcnow)

    # ── Per-user reminder preferences ──────────────────────────
    reminder_enabled      = db.Column(db.Boolean,   nullable=False, default=False)
    reminder_frequency    = db.Column(db.String(20), nullable=False, default="daily")   # daily/weekly/monthly
    reminder_hour         = db.Column(db.Integer,    nullable=False, default=9)
    reminder_minute       = db.Column(db.Integer,    nullable=False, default=0)
    reminder_day_of_week  = db.Column(db.Integer,    nullable=True)   # 0=Mon … 6=Sun (weekly)
    reminder_day_of_month = db.Column(db.Integer,    nullable=True)   # 1-28 (monthly)

    assigned_defects = db.relationship(
        "Defect", foreign_keys="Defect.assigned_user_id",
        backref="assigned_user", lazy="dynamic"
    )
    created_defects = db.relationship(
        "Defect", foreign_keys="Defect.created_by_id",
        backref="created_by", lazy="dynamic"
    )
    status_logs = db.relationship("DefectStatusLog", backref="changed_by", lazy=True)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)


class Defect(db.Model):
    __tablename__ = "defects"
    id                  = db.Column(db.Integer,      primary_key=True, autoincrement=True)
    asset_name          = db.Column(db.String(150),  nullable=False)
    location            = db.Column(db.String(200),  nullable=False)
    priority            = db.Column(db.String(20),   nullable=False, default="Medium")
    status              = db.Column(db.String(20),   nullable=False, default="Open")
    description         = db.Column(db.Text,         nullable=True)
    assigned_engineer   = db.Column(db.String(120),  nullable=True)
    assigned_user_id    = db.Column(db.Integer,      db.ForeignKey("users.id"),         nullable=True)
    created_by_id       = db.Column(db.Integer,      db.ForeignKey("users.id"),         nullable=True)
    corporation_id      = db.Column(db.Integer,      db.ForeignKey("corporations.id"),  nullable=True)
    date_reported       = db.Column(db.DateTime,     nullable=False, default=datetime.utcnow)
    expected_resolution = db.Column(db.DateTime,     nullable=True)
    resolved_at         = db.Column(db.DateTime,     nullable=True)

    status_logs = db.relationship(
        "DefectStatusLog", backref="defect", lazy=True,
        order_by="DefectStatusLog.changed_at.desc()",
        cascade="all, delete-orphan"
    )


class Asset(db.Model):
    __tablename__ = "assets"
    id            = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    asset_name    = db.Column(db.String(150), nullable=False, unique=True)
    location      = db.Column(db.String(200), nullable=False)
    health_status = db.Column(db.String(20),  nullable=False, default="Good")
    last_activity = db.Column(db.DateTime,    nullable=True)


class DefectStatusLog(db.Model):
    __tablename__ = "defect_status_logs"
    id             = db.Column(db.Integer,   primary_key=True, autoincrement=True)
    defect_id      = db.Column(db.Integer,   db.ForeignKey("defects.id"), nullable=False)
    old_status     = db.Column(db.String(20), nullable=True)
    new_status     = db.Column(db.String(20), nullable=False)
    comment        = db.Column(db.Text,       nullable=True)
    changed_by_id  = db.Column(db.Integer,   db.ForeignKey("users.id"), nullable=False)
    changed_at     = db.Column(db.DateTime,  nullable=False, default=datetime.utcnow)


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


def get_corp_engineers():
    """Return users in the current corp who can be assigned defects."""
    return db.session.scalars(
        db.select(User).where(
            User.corporation_id == current_user.corporation_id,
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
        query = query.where(Defect.date_reported >= start_dt)
    if end_dt is not None:
        query = query.where(Defect.date_reported <= end_dt)
    return query


def corp_defect_query(include_resolved=True, start_dt=None, end_dt=None):
    """Build a defect SELECT scoped to the current user's corporation."""
    query = db.select(Defect).where(Defect.corporation_id == current_user.corporation_id)
    if include_resolved is False:
        query = query.where(Defect.status != "Resolved")
    elif include_resolved == "resolved-only":
        query = query.where(Defect.status == "Resolved")
    query = apply_time_window(query, start_dt, end_dt)
    return query.order_by(Defect.date_reported.desc())


def apply_defect_search(query, search_term):
    """Filter defects by asset name or an ID entered as 123 or DFT-123."""
    if not search_term:
        return query
    normalized = search_term.strip()
    if not normalized:
        return query
    defect_id = normalized.upper().removeprefix("DFT-").strip()
    filters = [Defect.asset_name.ilike(f"%{normalized}%")]
    if defect_id.isdigit():
        filters.append(Defect.id == int(defect_id))
    return query.where(or_(*filters))


def build_asset_summaries(defects):
    grouped = {}
    for defect in defects:
        summary = grouped.setdefault(
            defect.asset_name,
            {
                "asset_name":       defect.asset_name,
                "location":         defect.location,
                "total_defects":    0,
                "active_defects":   0,
                "resolved_defects": 0,
                "last_activity":    defect.date_reported,
            },
        )
        summary["total_defects"] += 1
        if defect.status == "Resolved":
            summary["resolved_defects"] += 1
        else:
            summary["active_defects"] += 1
        if defect.date_reported and (
            summary["last_activity"] is None
            or defect.date_reported > summary["last_activity"]
        ):
            summary["last_activity"] = defect.date_reported
            summary["location"]      = defect.location
    return sorted(
        grouped.values(),
        key=lambda row: (row["last_activity"] or datetime.min),
        reverse=True,
    )


# ═══════════════════════════════════════════════════════════════
#  DB SCHEMA MIGRATION
# ═══════════════════════════════════════════════════════════════

def ensure_schema():
    """Add any missing columns to existing tables for backwards compat."""
    inspector   = inspect(db.engine)
    existing_tb = inspector.get_table_names()

    def has_col(table, col):
        return any(c["name"] == col for c in inspector.get_columns(table))

    stmts = []
    if "defects" in existing_tb:
        if not has_col("defects", "resolved_at"):
            stmts.append("ALTER TABLE defects ADD COLUMN resolved_at DATETIME NULL")
        if not has_col("defects", "corporation_id"):
            stmts.append("ALTER TABLE defects ADD COLUMN corporation_id INT NULL")
        if not has_col("defects", "assigned_user_id"):
            stmts.append("ALTER TABLE defects ADD COLUMN assigned_user_id INT NULL")
        if not has_col("defects", "created_by_id"):
            stmts.append("ALTER TABLE defects ADD COLUMN created_by_id INT NULL")

    if "users" in existing_tb:
        new_user_cols = [
            ("reminder_enabled",      "TINYINT(1) NOT NULL DEFAULT 0"),
            ("reminder_frequency",    "VARCHAR(20) NOT NULL DEFAULT 'daily'"),
            ("reminder_hour",         "INT NOT NULL DEFAULT 9"),
            ("reminder_minute",       "INT NOT NULL DEFAULT 0"),
            ("reminder_day_of_week",  "INT NULL"),
            ("reminder_day_of_month", "INT NULL"),
        ]
        for col_name, col_def in new_user_cols:
            if not has_col("users", col_name):
                stmts.append(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

    for stmt in stmts:
        db.session.execute(text(stmt))
    if stmts:
        db.session.commit()


# ═══════════════════════════════════════════════════════════════
#  EMAIL & REMINDERS
# ═══════════════════════════════════════════════════════════════

# Global scheduler instance so routes can reschedule jobs dynamically
scheduler = BackgroundScheduler()

def get_cipher():
    # Derive a 32-byte url-safe base64 key from your app's SECRET_KEY
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
            pw = encrypted_pw # Fallback if it wasn't encrypted previously
        app.config["MAIL_PASSWORD"] = pw
        
        # Reinitialise Flask-Mail with the updated config
        mail.init_app(app)


def send_reminder_email(user, pending_defects):
    """Send a reminder email to one user."""
    smtp_user = app.config.get("MAIL_USERNAME", "")
    if not smtp_user:
        return  # SMTP not configured
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
        lines.append(f"  • DFT-{d.id}: {d.asset_name} ({d.priority}) — Due: {due}")
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
        if not user or not user.is_approved or not user.reminder_enabled:
            return
        pending = db.session.scalars(
            db.select(Defect).where(
                Defect.assigned_user_id == user_id,
                Defect.status != "Resolved",
            )
        ).all()
        if pending:
            send_reminder_email(user, pending)


def schedule_user_reminder(user):
    """Add or replace a scheduler job for a user based on their preferences."""
    job_id = f"reminder_user_{user.id}"
    # Remove existing job if present
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass

    if not user.reminder_enabled:
        return  # User has disabled reminders — nothing to schedule

    h = user.reminder_hour   or 9
    m = user.reminder_minute or 0

    if user.reminder_frequency == "weekly":
        dow = user.reminder_day_of_week if user.reminder_day_of_week is not None else 0
        scheduler.add_job(
            run_reminders_for_user, "cron",
            id=job_id, day_of_week=dow, hour=h, minute=m,
            args=[user.id], replace_existing=True,
        )
    elif user.reminder_frequency == "monthly":
        dom = user.reminder_day_of_month or 1
        scheduler.add_job(
            run_reminders_for_user, "cron",
            id=job_id, day=dom, hour=h, minute=m,
            args=[user.id], replace_existing=True,
        )
    else:  # daily (default)
        scheduler.add_job(
            run_reminders_for_user, "cron",
            id=job_id, hour=h, minute=m,
            args=[user.id], replace_existing=True,
        )


def bootstrap_all_user_schedules():
    """Called at startup to register existing user reminder preferences."""
    with app.app_context():
        users = db.session.scalars(
            db.select(User).where(User.reminder_enabled == True)
        ).all()
        for user in users:
            schedule_user_reminder(user)


# ═══════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/register", methods=["GET", "POST"])
def register():
    corp_count = db.session.scalar(
        db.select(db.func.count()).select_from(Corporation)
    )
    if corp_count and corp_count > 0:
        flash("A workspace already exists. Use an invite link to join.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        corp_name = request.form.get("corp_name", "").strip()
        username  = request.form.get("username",  "").strip()
        email     = request.form.get("email",     "").strip().lower()
        password  = request.form.get("password",  "")
        confirm   = request.form.get("confirm_password", "")

        errors = []
        if not corp_name:              errors.append("Corporation name is required.")
        if not username:               errors.append("Username is required.")
        if not email:                  errors.append("Email is required.")
        if len(password) < 6:          errors.append("Password must be at least 6 characters.")
        if password != confirm:        errors.append("Passwords do not match.")
        if db.session.scalar(db.select(User).where(User.email == email)):
            errors.append("Email already registered.")

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template("register.html")

        corp = Corporation(name=corp_name, invite_code=secrets.token_urlsafe(16))
        db.session.add(corp)
        db.session.flush()

        user = User(
            email=email, username=username,
            role="head", corporation_id=corp.id, is_approved=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash(f"Welcome! Workspace '{corp_name}' is ready. Share your invite link to add team members.", "success")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    corp_count = db.session.scalar(
        db.select(db.func.count()).select_from(Corporation)
    )
    if not corp_count:
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

        # ── In-app reminder banner ──────────────────────────────
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


@app.route("/join/<invite_code>", methods=["GET", "POST"])
def join(invite_code):
    corp = db.session.scalar(
        db.select(Corporation).where(Corporation.invite_code == invite_code)
    )
    if not corp:
        flash("Invalid or expired invite link.", "danger")
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

        if errors:
            for err in errors:
                flash(err, "danger")
            return render_template("join.html", corp=corp, invite_code=invite_code)

        user = User(
            email=email, username=username,
            role="viewer", corporation_id=corp.id, is_approved=False,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Registration successful! Your account is pending approval by the Head.", "info")
        return redirect(url_for("pending_approval"))

    return render_template("join.html", corp=corp, invite_code=invite_code)


# ═══════════════════════════════════════════════════════════════
#  ADMIN ROUTES  (Head only)
# ═══════════════════════════════════════════════════════════════

@app.route("/admin/users")
@role_required("head")
def admin_users():
    users = db.session.scalars(
        db.select(User)
        .where(User.corporation_id == current_user.corporation_id)
        .order_by(User.created_at.asc())
    ).all()
    corp        = current_user.corporation
    invite_link = url_for("join", invite_code=corp.invite_code, _external=True)
    return render_template(
        "admin_users.html",
        active_page="admin_users",
        users=users,
        invite_link=invite_link,
        corp=corp,
    )


@app.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@role_required("head")
def approve_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.corporation_id != current_user.corporation_id:
        abort(403)
    user.is_approved = True
    db.session.commit()
    flash(f"{user.username} has been approved and can now log in.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reject", methods=["POST"])
@role_required("head")
def reject_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.corporation_id != current_user.corporation_id:
        abort(403)
    if user.id == current_user.id:
        flash("You cannot remove yourself.", "danger")
        return redirect(url_for("admin_users"))
    db.session.delete(user)
    db.session.commit()
    flash("User removed.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@role_required("head")
def change_user_role(user_id):
    user = db.get_or_404(User, user_id)
    if user.corporation_id != current_user.corporation_id:
        abort(403)
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


@app.route("/admin/invite/regenerate", methods=["POST"])
@role_required("head")
def regenerate_invite():
    corp             = current_user.corporation
    corp.invite_code = secrets.token_urlsafe(16)
    db.session.commit()
    flash("Invite code regenerated. Share the new link with your team.", "success")
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
    active_defects = db.session.scalars(corp_defect_query(include_resolved=False)).all()

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
            Defect.corporation_id == current_user.corporation_id,
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
            "value": sum(not (d.assigned_engineer or "").strip() for d in active_defects),
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
    query        = corp_defect_query(include_resolved=False, start_dt=start_dt, end_dt=end_dt)
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
            corp_defect_query(include_resolved="resolved-only", start_dt=start_dt, end_dt=end_dt),
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
    engineers = get_corp_engineers()

    if request.method == "POST":
        defect                = Defect()
        defect.corporation_id = current_user.corporation_id
        defect.created_by_id  = current_user.id
        defect.asset_name     = request.form["asset_name"].strip()
        defect.location       = request.form["location"].strip()
        # Engineers default to Medium priority; Head/Manager can set it
        defect.priority       = (request.form.get("priority", "Medium")
                                 if current_user.role != "engineer" else "Medium")
        defect.status         = request.form.get("status", "Open")
        defect.description    = request.form.get("description", "").strip()

        uid = request.form.get("assigned_user_id", "")
        if uid and uid.isdigit():
            u = db.session.get(User, int(uid))
            if u and u.corporation_id == current_user.corporation_id:
                defect.assigned_user_id  = u.id
                defect.assigned_engineer = u.username
            else:
                defect.assigned_user_id  = None
                defect.assigned_engineer = ""
        else:
            defect.assigned_user_id  = None
            defect.assigned_engineer = ""

        defect.expected_resolution = parse_datetime_input(
            request.form.get("expected_resolution")
        )

        db.session.add(defect)
        db.session.flush()  # get defect.id before logging

        log = DefectStatusLog(
            defect_id     = defect.id,
            old_status    = None,
            new_status    = defect.status,
            comment       = "Defect created.",
            changed_by_id = current_user.id,
            changed_at    = datetime.now(),
        )
        db.session.add(log)
        db.session.commit()
        flash("Defect created successfully.", "success")
        return redirect(url_for("defects"))

    return render_template(
        "add_defect.html",
        active_page="add_defect",
        defect=None,
        corp_users=engineers,
        engineer_restricted=False,
    )


@app.route("/defects/<int:defect_id>/edit", methods=["GET", "POST"])
@approved_required
def edit_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)

    if defect.corporation_id != current_user.corporation_id:
        abort(403)
    if current_user.role == "viewer":
        abort(403)
    # Engineers may only edit defects assigned to themselves
    if current_user.role == "engineer" and defect.assigned_user_id != current_user.id:
        abort(403)

    engineers           = get_corp_engineers()
    engineer_restricted = current_user.role == "engineer"

    if request.method == "POST":
        old_status = defect.status
        new_status = request.form.get("status", defect.status)
        comment    = request.form.get("status_comment", "").strip()

        if not engineer_restricted:
            defect.asset_name  = request.form["asset_name"].strip()
            defect.location    = request.form["location"].strip()
            defect.priority    = request.form["priority"]
            defect.description = request.form.get("description", "").strip()

            uid = request.form.get("assigned_user_id", "")
            if uid and uid.isdigit():
                u = db.session.get(User, int(uid))
                if u and u.corporation_id == current_user.corporation_id:
                    defect.assigned_user_id  = u.id
                    defect.assigned_engineer = u.username
                else:
                    defect.assigned_user_id  = None
                    defect.assigned_engineer = ""
            else:
                defect.assigned_user_id  = None
                defect.assigned_engineer = request.form.get("assigned_engineer", "").strip()

            defect.expected_resolution = parse_datetime_input(
                request.form.get("expected_resolution")
            )

        if new_status == "Resolved" and defect.status != "Resolved":
            defect.resolved_at = datetime.now()
        elif new_status != "Resolved":
            defect.resolved_at = None
        defect.status = new_status

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
        engineer_restricted=engineer_restricted,
        corp_users=engineers,
    )


@app.route("/defects/<int:defect_id>/delete", methods=["POST"])
@role_required("head", "manager")
def delete_defect(defect_id):
    defect = db.get_or_404(Defect, defect_id)
    if defect.corporation_id != current_user.corporation_id:
        abort(403)
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
    filtered_defects = db.session.scalars(
        corp_defect_query(start_dt=start_dt, end_dt=end_dt)
    ).all()
    asset_rows          = build_asset_summaries(filtered_defects)
    total_defects       = len(filtered_defects)
    active_defects_cnt  = sum(a["active_defects"]   for a in asset_rows)
    resolved_defects_cnt= sum(a["resolved_defects"] for a in asset_rows)
    return render_template(
        "assets.html",
        active_page="assets",
        assets=asset_rows,
        total_assets=len(asset_rows),
        total_defects=total_defects,
        active_defects=active_defects_cnt,
        resolved_defects=resolved_defects_cnt,
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

    if request.method == "POST":
        action = request.form.get("action", "reminder")

        if action == "smtp" and current_user.role == "head":
            # Save SMTP settings to AppSettings table
            AppSettings.set("mail_server",  request.form.get("mail_server",  "smtp.gmail.com").strip())
            AppSettings.set("mail_port",    request.form.get("mail_port",    "587").strip())
            AppSettings.set("mail_use_tls", "True" if request.form.get("mail_use_tls") else "False")
            AppSettings.set("mail_username", request.form.get("mail_username", "").strip())
            # Only update password if one was provided (blank = keep existing)
            new_pw = request.form.get("mail_password", "").strip()
            if new_pw:
                encrypted_pw = get_cipher().encrypt(new_pw.encode()).decode()
                AppSettings.set("mail_password", encrypted_pw)
            reload_mail_config()
            flash("SMTP settings saved and applied.", "success")

        elif action == "reminder":
            freq    = request.form.get("frequency", "daily")
            time_str = request.form.get("reminder_time", "09:00")
            try:
                h, m = map(int, time_str.split(":"))
            except (ValueError, AttributeError):
                h, m = 9, 0

            dow = request.form.get("day_of_week",  type=int)
            dom = request.form.get("day_of_month", type=int)

            current_user.reminder_enabled      = bool(request.form.get("reminder_enabled"))
            current_user.reminder_frequency    = freq
            current_user.reminder_hour         = h
            current_user.reminder_minute       = m
            current_user.reminder_day_of_week  = dow
            current_user.reminder_day_of_month = dom
            db.session.commit()

            # Re-register this user's scheduler job with the new settings
            schedule_user_reminder(current_user)

            if current_user.reminder_enabled:
                freq_label = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}.get(freq, freq)
                flash(f"Email reminder set to {freq_label} at {time_str}.", "success")
            else:
                flash("Email reminders disabled.", "info")

        return redirect(url_for("settings"))

    # ── GET ─────────────────────────────────────────────────────
    smtp_configured = bool(AppSettings.get("mail_username"))
    smtp_settings = {
        "server":   AppSettings.get("mail_server",  "smtp.gmail.com"),
        "port":     AppSettings.get("mail_port",    "587"),
        "use_tls":  AppSettings.get("mail_use_tls", "True") == "True",
        "username": AppSettings.get("mail_username", ""),
    }

    days_of_week  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_of_month = list(range(1, 29))  # 1-28 safe for all months

    return render_template(
        "settings.html",
        active_page="settings",
        smtp_configured=smtp_configured,
        smtp_settings=smtp_settings,
        days_of_week=days_of_week,
        days_of_month=days_of_month,
        now=datetime.now(),
    )


# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_schema()
        reload_mail_config()       # Load SMTP from DB on startup
        bootstrap_all_user_schedules()  # Re-register all user jobs from DB

    scheduler.start()

    try:
        debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
        app.run(debug=debug_mode)
    finally:
        scheduler.shutdown()
