from datetime import datetime
from extensions import db


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
    workspace_id        = db.Column(db.Integer,     db.ForeignKey("workspaces.id"), nullable=True)
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
    def location_full_path(self):
        return self.asset.location.full_path if self.asset and self.asset.location else "Main Site"

    @property
    def assigned_engineer(self):
        return self.assigned_user.username if self.assigned_user else ""

    @property
    def date_reported(self):
        return self.reported_at

    @property
    def is_overdue(self):
        if self.status != "Resolved" and self.expected_resolution:
            return datetime.now() > self.expected_resolution
        return False

    @property
    def sla_info(self):
        now = datetime.now()
        if self.status == "Resolved":
            if self.resolved_at and self.expected_resolution:
                if self.resolved_at <= self.expected_resolution:
                    return {"label": "Resolved on time", "tone": "bg-green-lt text-green"}
                return {"label": "Resolved late", "tone": "bg-yellow-lt text-yellow"}
            return {"label": "Resolved", "tone": "bg-green-lt text-green"}
        if self.expected_resolution:
            if now > self.expected_resolution:
                diff = now - self.expected_resolution
                days = diff.days
                return {"label": f"Overdue by {days}d" if days > 0 else "Overdue today", "tone": "bg-red text-white"}
            diff = self.expected_resolution - now
            days = diff.days
            return {"label": f"{days}d remaining" if days > 0 else "Due today", "tone": "bg-azure-lt text-azure"}
        return {"label": "No SLA", "tone": "bg-secondary-lt text-secondary"}


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
