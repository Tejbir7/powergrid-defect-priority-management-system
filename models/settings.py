from extensions import db


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
