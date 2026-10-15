from datetime import datetime
from extensions import db


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
    workspace_id    = db.Column(db.Integer,     db.ForeignKey("workspaces.id"), nullable=True)
    asset_type_id   = db.Column(db.Integer,     db.ForeignKey("asset_types.id"), nullable=False)
    parent_asset_id = db.Column(db.Integer,     db.ForeignKey("assets.id"), nullable=True)
    location_id     = db.Column(db.Integer,     db.ForeignKey("locations.id"), nullable=False)
    asset_name      = db.Column(db.String(150), nullable=False)
    created_at      = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at      = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    parent  = db.relationship("Asset", remote_side=[id], backref="children")
    defects = db.relationship("Defect", backref="asset", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def is_component(self):
        """Check if asset is a child component of another equipment."""
        return self.parent_asset_id is not None

    @property
    def parent_name(self):
        return self.parent.asset_name if self.parent else ""

    @property
    def display_name(self):
        if self.parent:
            return f"{self.asset_name} (Part of {self.parent.asset_name})"
        return self.asset_name

    @property
    def location_full_path(self):
        return self.location.full_path if self.location else "Site"


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
