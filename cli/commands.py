from datetime import datetime, timedelta
from flask import current_app
from extensions import db
from models.workspace import Workspace, WorkspaceMember
from models.location import LocationType, Location
from models.asset import AssetType, Asset
from models.user import User
from models.defect import Defect, DefectStatusLog


def seed_demo_data():
    """Populate initial 3NF master data and sample operational records."""
    if db.session.scalar(db.select(db.func.count()).select_from(User)) > 0:
        return

    # 1. Default Users
    head = User(username="admin", email="admin@powergrid.in", role="head", is_approved=True, updated_at=datetime.now())
    head.set_password("powergrid123")

    engineer = User(username="engineer1", email="engineer1@powergrid.in", role="engineer", is_approved=True, updated_at=datetime.now())
    engineer.set_password("powergrid123")

    db.session.add_all([head, engineer])
    db.session.flush()

    # 2. Default Workspace
    ws = Workspace(
        name="POWERGRID Punjab Transmission Grid",
        invite_code="pg-punjab-2026",
        created_by_id=head.id,
    )
    db.session.add(ws)
    db.session.flush()

    db.session.add_all([
        WorkspaceMember(workspace_id=ws.id, user_id=head.id, role="head", is_approved=True),
        WorkspaceMember(workspace_id=ws.id, user_id=engineer.id, role="engineer", is_approved=True),
    ])
    db.session.flush()

    # 3. Location Types
    region_type = LocationType(name="Region")
    sub_type = LocationType(name="Substation")
    bay_type = LocationType(name="Bay")
    db.session.add_all([region_type, sub_type, bay_type])
    db.session.flush()

    # 4. Locations (Punjab hierarchy: Region -> Substation -> Bay)
    punjab = Location(name="Punjab Region", location_type_id=region_type.id, workspace_id=ws.id)
    db.session.add(punjab)
    db.session.flush()

    asr = Location(name="Amritsar Substation", location_type_id=sub_type.id, parent_location_id=punjab.id, workspace_id=ws.id)
    lud = Location(name="Ludhiana Substation", location_type_id=sub_type.id, parent_location_id=punjab.id, workspace_id=ws.id)
    db.session.add_all([asr, lud])
    db.session.flush()

    bay1 = Location(name="Bay 1", location_type_id=bay_type.id, parent_location_id=asr.id, workspace_id=ws.id)
    bay2 = Location(name="Bay 2", location_type_id=bay_type.id, parent_location_id=asr.id, workspace_id=ws.id)
    db.session.add_all([bay1, bay2])
    db.session.flush()

    # 5. Asset Types
    tf_type = AssetType(name="Transformer")
    cb_type = AssetType(name="Circuit Breaker")
    fan_type = AssetType(name="Cooling Fan")
    db.session.add_all([tf_type, cb_type, fan_type])
    db.session.flush()

    # 6. Assets
    t1 = Asset(asset_name="Transformer T1", asset_type_id=tf_type.id, location_id=bay1.id, workspace_id=ws.id)
    t2 = Asset(asset_name="Transformer T2", asset_type_id=tf_type.id, location_id=bay2.id, workspace_id=ws.id)
    cb1 = Asset(asset_name="Circuit Breaker CB-01", asset_type_id=cb_type.id, location_id=asr.id, workspace_id=ws.id)
    db.session.add_all([t1, t2, cb1])
    db.session.flush()

    # Child Assets (Fans on T1 - inheriting T1 bay1 location)
    fan1 = Asset(asset_name="Cooling Fan 1", asset_type_id=fan_type.id, location_id=bay1.id, parent_asset_id=t1.id, workspace_id=ws.id)
    fan2 = Asset(asset_name="Cooling Fan 2", asset_type_id=fan_type.id, location_id=bay1.id, parent_asset_id=t1.id, workspace_id=ws.id)
    db.session.add_all([fan1, fan2])
    db.session.flush()

    # 7. Demo Defects
    now = datetime.now()
    d1 = Defect(
        workspace_id=ws.id,
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
        workspace_id=ws.id,
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
        workspace_id=ws.id,
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

    # 8. Status Logs
    for d in [d1, d2, d3]:
        db.session.add(
            DefectStatusLog(
                defect_id=d.id,
                changed_by_id=d.created_by_id,
                old_status=None,
                new_status=d.status,
                comment="Initial defect report.",
                changed_at=d.created_at,
            )
        )

    db.session.commit()
    if current_app:
        current_app.logger.info("Demo data seeded successfully.")


def register_commands(app):
    """Register custom Flask CLI commands."""

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
