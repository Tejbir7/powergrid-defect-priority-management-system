from datetime import datetime
from flask_login import UserMixin
from extensions import db, bcrypt


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

    @property
    def workspaces(self):
        from models.workspace import Workspace, WorkspaceMember
        return db.session.scalars(
            db.select(Workspace)
            .join(WorkspaceMember, Workspace.id == WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == self.id)
            .order_by(Workspace.name)
        ).all()

    def get_membership(self, workspace_id):
        from models.workspace import WorkspaceMember
        if not workspace_id:
            return None
        return db.session.scalar(
            db.select(WorkspaceMember).where(
                WorkspaceMember.user_id == self.id,
                WorkspaceMember.workspace_id == workspace_id,
            )
        )

    def get_role_in_workspace(self, workspace_id):
        m = self.get_membership(workspace_id)
        return m.role if m else self.role

    def is_approved_in_workspace(self, workspace_id):
        m = self.get_membership(workspace_id)
        return m.is_approved if m else self.is_approved

    # Compatibility properties for seamless template support
    @property
    def corporation(self):
        class _Corp:
            name = "POWERGRID"
        return _Corp()

    @property
    def reminder_enabled(self):
        from models.reminder import Reminder
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.enabled if r else False

    @property
    def reminder_frequency(self):
        from models.reminder import Reminder
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.frequency if r else "daily"

    @property
    def reminder_hour(self):
        from models.reminder import Reminder
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.hour if r else 9

    @property
    def reminder_minute(self):
        from models.reminder import Reminder
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.minute if r else 0

    @property
    def reminder_day_of_week(self):
        from models.reminder import Reminder
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.day_of_week if r else None

    @property
    def reminder_day_of_month(self):
        from models.reminder import Reminder
        r = db.session.scalar(db.select(Reminder).where(Reminder.user_id == self.id, Reminder.type == "DEFECT_SUMMARY"))
        return r.day_of_month if r else None
