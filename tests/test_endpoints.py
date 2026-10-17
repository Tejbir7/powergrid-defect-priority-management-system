from tests.conftest import login
from models.asset import Asset
from models.defect import Defect
from models.user import User
from models.workspace import Workspace, WorkspaceMember
from extensions import db


def test_public_pages_and_auth_wall(client):
    home = client.get("/")
    assert home.status_code == 302
    assert "/login" in home.headers["Location"]

    for path in ("/login", "/register", "/forgot-password"):
        page = client.get(path)
        assert page.status_code == 200

    blocked = client.get("/dashboard")
    assert blocked.status_code == 302
    assert "/login" in blocked.headers["Location"]


def test_login_rejects_bad_password_and_accepts_demo_admin(client):
    bad = client.post(
        "/login",
        data={"email": "admin@powergrid.in", "password": "wrong-password"},
        follow_redirects=True,
    )
    assert bad.status_code == 200
    assert b"Invalid email or password" in bad.data

    good = client.post(
        "/login",
        data={"email": "admin@powergrid.in", "password": "powergrid123"},
        follow_redirects=True,
    )
    assert good.status_code == 200
    assert b"Transmission Grid Workspaces" in good.data


def test_register_login_and_logout(client, app):
    created = client.post(
        "/register",
        data={
            "username": "newengineer",
            "email": "newengineer@powergrid.in",
            "password": "powergrid123",
            "confirm_password": "powergrid123",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "newengineer@powergrid.in"))
        assert user is not None
        assert user.role == "engineer"

    logged_out = client.get("/logout", follow_redirects=True)
    assert logged_out.status_code == 200
    assert b"Sign In" in logged_out.data


def test_password_reset_pages(client):
    missing = client.post(
        "/forgot-password",
        data={"email": "nobody@powergrid.in"},
        follow_redirects=True,
    )
    assert missing.status_code == 200
    assert b"If that email is registered" in missing.data

    known = client.post(
        "/forgot-password",
        data={"email": "engineer1@powergrid.in"},
        follow_redirects=True,
    )
    assert known.status_code == 200

    expired = client.get("/reset-password/not-a-real-token", follow_redirects=True)
    assert expired.status_code == 200
    assert b"invalid or has expired" in expired.data


def test_admin_can_open_operational_pages(admin):
    for path in (
        "/dashboard",
        "/defects",
        "/defect-history",
        "/defects/add",
        "/assets",
        "/workspaces",
        "/settings",
        "/admin/users",
        "/search-defects?q=Transformer",
    ):
        response = admin.get(path, follow_redirects=True)
        assert response.status_code == 200, path


def test_engineer_cannot_open_crew_admin(engineer):
    response = engineer.get("/admin/users")
    assert response.status_code == 403


def test_create_edit_and_delete_defect(admin, app):
    with app.app_context():
        asset = db.session.scalar(db.select(Asset).where(Asset.asset_name == "Transformer T1"))
        asset_id = asset.id

    created = admin.post(
        "/defects/add",
        data={
            "asset_id": str(asset_id),
            "description": "Bushing temperature above the operating limit.",
            "priority": "High",
            "status": "Open",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"Defect created successfully" in created.data

    with app.app_context():
        defect = db.session.scalar(
            db.select(Defect).where(Defect.description.like("%Bushing temperature%"))
        )
        defect_id = defect.id

    edited = admin.post(
        f"/defects/{defect_id}/edit",
        data={
            "asset_id": str(asset_id),
            "description": "Bushing temperature above the operating limit.",
            "priority": "Critical",
            "status": "In Progress",
            "status_comment": "Crew dispatched.",
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    assert b"Defect updated successfully" in edited.data

    removed = admin.post(f"/defects/{defect_id}/delete", follow_redirects=True)
    assert removed.status_code == 200
    assert b"Defect deleted successfully" in removed.data


def test_engineer_cannot_delete_defect(engineer, app):
    with app.app_context():
        defect_id = db.session.scalar(db.select(Defect.id))
    response = engineer.post(f"/defects/{defect_id}/delete")
    assert response.status_code == 403


def test_add_asset_and_workspace_flows(admin, app):
    added = admin.post(
        "/assets/add",
        data={"asset_name": "Isolator IS-09", "new_asset_type": "Isolator"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert b"registered successfully" in added.data

    created = admin.post(
        "/workspaces/create",
        data={"name": "Northern Test Grid"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"created successfully" in created.data

    with app.app_context():
        workspace = db.session.scalar(db.select(Workspace).where(Workspace.name == "Northern Test Grid"))
        code = workspace.invite_code
        workspace_id = workspace.id

    joined = admin.post(
        "/workspaces/join",
        data={"invite_code": "not-a-real-code"},
        follow_redirects=True,
    )
    assert b"Invalid or expired invite code" in joined.data

    invite = admin.get(f"/join/{code}")
    assert invite.status_code == 200
    assert b"Northern Test Grid" in invite.data

    switched = admin.get(f"/workspaces/{workspace_id}/select", follow_redirects=True)
    assert switched.status_code == 200

    regenerated = admin.post(
        f"/workspaces/{workspace_id}/regenerate-invite",
        follow_redirects=True,
    )
    assert regenerated.status_code == 200
    assert b"New invite code generated" in regenerated.data


def test_engineer_cannot_regenerate_invite(engineer, app):
    with app.app_context():
        workspace_id = db.session.scalar(db.select(Workspace.id))
    response = engineer.post(
        f"/workspaces/{workspace_id}/regenerate-invite",
        follow_redirects=True,
    )
    assert b"Only the workspace Head" in response.data


def test_admin_changes_member_role_and_reminder(admin, app):
    with app.app_context():
        membership = db.session.scalar(
            db.select(WorkspaceMember).where(WorkspaceMember.role == "engineer")
        )
        membership_id = membership.id
        engineer_id = membership.user_id

    role = admin.post(
        f"/admin/workspace/members/{membership_id}/role",
        data={"role": "viewer"},
        follow_redirects=True,
    )
    assert b"workspace role updated" in role.data

    global_role = admin.post(
        f"/admin/users/{engineer_id}/role",
        data={"role": "engineer"},
        follow_redirects=True,
    )
    assert b"global role updated" in global_role.data

    settings = admin.post(
        "/settings",
        data={
            "action": "reminder",
            "frequency": "weekly",
            "reminder_time": "08:30",
            "day_of_week": "1",
            "reminder_enabled": "on",
        },
        follow_redirects=True,
    )
    assert settings.status_code == 200
    assert b"Email reminder set to weekly" in settings.data


def test_password_reset_completes_and_new_password_works(client, app):
    from services.crypto import get_cipher

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "engineer1@powergrid.in"))
        token = get_cipher(app.config["SECRET_KEY"]).encrypt(
            f"{user.id}:1".encode()
        ).decode()

    page = client.get(f"/reset-password/{token}")
    assert page.status_code == 200

    reset = client.post(
        f"/reset-password/{token}",
        data={"password": "newpass123", "confirm_password": "newpass123"},
        follow_redirects=True,
    )
    assert b"successfully reset" in reset.data

    logged_in = login(client, "engineer1@powergrid.in", password="newpass123")
    assert b"Transmission Grid Workspaces" in logged_in.data


def test_pending_approval_redirects_approved_users(admin):
    response = admin.get("/pending-approval", follow_redirects=True)
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_admin_user_and_membership_actions(admin, app):
    with app.app_context():
        engineer = db.session.scalar(db.select(User).where(User.email == "engineer1@powergrid.in"))
        engineer_id = engineer.id
        membership = db.session.scalar(
            db.select(WorkspaceMember).where(WorkspaceMember.user_id == engineer_id)
        )
        membership_id = membership.id
        defect_id = db.session.scalar(db.select(Defect.id))
        code = db.session.scalar(db.select(Workspace.invite_code))

    approved = admin.post(f"/admin/users/{engineer_id}/approve", follow_redirects=True)
    assert b"has been approved" in approved.data

    rejected = admin.post(f"/admin/users/{engineer_id}/reject", follow_redirects=True)
    assert b"defect history" in rejected.data

    removed = admin.post(
        f"/admin/workspace/members/{membership_id}/remove",
        follow_redirects=True,
    )
    assert b"removed from the grid workspace" in removed.data

    editor = admin.get(f"/defects/{defect_id}/edit", follow_redirects=True)
    assert editor.status_code == 200
    assert b"Oil leakage" in editor.data or b"description" in editor.data

    switched = admin.post(f"/join/{code}", follow_redirects=True)
    assert b"Switched to" in switched.data

    resolved_search = admin.get(
        "/search-defects?q=Transformer&scope=resolved",
        follow_redirects=True,
    )
    assert resolved_search.status_code == 200


def test_engineer_cannot_approve_users(engineer, app):
    with app.app_context():
        admin_id = db.session.scalar(
            db.select(User.id).where(User.email == "admin@powergrid.in")
        )
    response = engineer.post(f"/admin/users/{admin_id}/approve")
    assert response.status_code == 403


def test_settings_smtp_and_test_mail_endpoints(admin):
    saved = admin.post(
        "/settings",
        data={
            "action": "smtp",
            "mail_server": "smtp.example.com",
            "mail_port": "587",
            "mail_encryption": "tls",
            "mail_username": "",
            "mail_default_sender": "",
            "mail_password": "",
        },
        follow_redirects=True,
    )
    assert b"SMTP server configuration saved" in saved.data

    tested = admin.post(
        "/settings",
        data={"action": "test_smtp", "test_recipient": "ops@powergrid.in"},
        follow_redirects=True,
    )
    assert b"must be configured" in tested.data


def test_invalid_invite_link_sends_visitor_to_login(client):
    response = client.get("/join/not-a-real-code", follow_redirects=True)
    assert response.status_code == 200
    assert b"invalid or has expired" in response.data
