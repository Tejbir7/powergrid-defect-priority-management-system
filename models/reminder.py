from datetime import datetime
from extensions import db


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
