import secrets
from datetime import datetime
from extensions import db


class Workspace(db.Model):
    """Grid workspace / transmission corporation partition."""
    __tablename__ = "workspaces"
    id            = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    name          = db.Column(db.String(150), nullable=False, unique=True)
    invite_code   = db.Column(db.String(32),  nullable=False, unique=True, default=lambda: secrets.token_urlsafe(12))
    created_by_id = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at    = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at    = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    members   = db.relationship("WorkspaceMember", backref="workspace", lazy="dynamic", cascade="all, delete-orphan")
    locations = db.relationship("Location", backref="workspace", lazy="dynamic", cascade="all, delete-orphan")
    assets    = db.relationship("Asset",    backref="workspace", lazy="dynamic", cascade="all, delete-orphan")
    defects   = db.relationship("Defect",   backref="workspace", lazy="dynamic", cascade="all, delete-orphan")

    def regenerate_invite_code(self):
        self.invite_code = secrets.token_urlsafe(12)
        db.session.commit()
        return self.invite_code


class WorkspaceMember(db.Model):
    """Membership linking users to workspaces with RBAC role and approval status."""
    __tablename__ = "workspace_members"
    __table_args__ = (
        db.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_user"),
        db.CheckConstraint("role IN ('head', 'manager', 'engineer', 'viewer')", name="ck_workspace_member_role"),
    )
    id           = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    workspace_id = db.Column(db.Integer,     db.ForeignKey("workspaces.id"), nullable=False)
    user_id      = db.Column(db.Integer,     db.ForeignKey("users.id"),      nullable=False)
    role         = db.Column(db.String(20),  nullable=False, default="engineer")
    is_approved  = db.Column(db.Boolean,     nullable=False, default=False)
    created_at   = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at   = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    user = db.relationship("User", backref=db.backref("workspace_memberships", lazy="dynamic", cascade="all, delete-orphan"))
