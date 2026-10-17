from datetime import datetime
from extensions import db


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
    workspace_id       = db.Column(db.Integer,     db.ForeignKey("workspaces.id"), nullable=True)
    parent_location_id = db.Column(db.Integer,     db.ForeignKey("locations.id"), nullable=True)
    location_type_id   = db.Column(db.Integer,     db.ForeignKey("location_types.id"), nullable=False)
    name               = db.Column(db.String(150), nullable=False)
    created_at         = db.Column(db.DateTime,    nullable=False, default=datetime.now)
    updated_at         = db.Column(db.DateTime,    nullable=False, default=datetime.now, onupdate=datetime.now)

    parent   = db.relationship("Location", remote_side=[id], backref="children")
    assets   = db.relationship("Asset", backref="location", lazy="dynamic")

    @property
    def full_path(self):
        """Hierarchical path string, e.g. Punjab Region › Amritsar Substation › Bay 1"""
        parts = [self.name]
        curr = self.parent
        while curr:
            parts.append(curr.name)
            curr = curr.parent
        return " › ".join(reversed(parts))

    @property
    def path_list(self):
        """List of (type_name, name) tuples from root to this location."""
        nodes = [(self.location_type.name if self.location_type else "Site", self.name)]
        curr = self.parent
        while curr:
            nodes.append((curr.location_type.name if curr.location_type else "Site", curr.name))
            curr = curr.parent
        nodes.reverse()
        return nodes
