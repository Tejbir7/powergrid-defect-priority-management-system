from extensions import mail
from models.defect import Defect
from models.settings import AppSettings
from models.user import User
from services.crypto import get_cipher
from services.email_service import reload_mail_config, send_reminder_email, send_test_email
from extensions import db


def test_reload_mail_config_applies_saved_smtp_settings(app):
    with app.app_context():
        AppSettings.set("mail_server", "smtp.office365.com")
        AppSettings.set("mail_port", "587")
        AppSettings.set("mail_use_tls", "True")
        AppSettings.set("mail_use_ssl", "False")
        AppSettings.set("mail_username", "ops@powergrid.in")
        AppSettings.set("mail_default_sender", "PowerGrid <ops@powergrid.in>")
        encrypted = get_cipher(app.config["SECRET_KEY"]).encrypt(b"app-password").decode()
        AppSettings.set("mail_password", encrypted)

        reload_mail_config(app)

        assert app.config["MAIL_SERVER"] == "smtp.office365.com"
        assert app.config["MAIL_PORT"] == 587
        assert app.config["MAIL_USE_TLS"] is True
        assert app.config["MAIL_USERNAME"] == "ops@powergrid.in"
        assert app.config["MAIL_PASSWORD"] == "app-password"
        assert app.config["MAIL_DEFAULT_SENDER"] == "PowerGrid <ops@powergrid.in>"


def test_send_test_email_requires_smtp_username(app):
    with app.app_context():
        ok, message = send_test_email("engineer1@powergrid.in")
    assert ok is False
    assert "must be configured" in message


def test_send_test_email_requires_recipient(app):
    with app.app_context():
        AppSettings.set("mail_username", "ops@powergrid.in")
        AppSettings.set("mail_server", "smtp.example.com")
        ok, message = send_test_email("")
    assert ok is False
    assert "recipient" in message


def test_send_test_email_delivers_message(app, monkeypatch):
    sent = {}

    def capture(message):
        sent["message"] = message

    monkeypatch.setattr(mail, "send", capture)
    with app.app_context():
        AppSettings.set("mail_server", "smtp.example.com")
        AppSettings.set("mail_port", "587")
        AppSettings.set("mail_username", "ops@powergrid.in")
        AppSettings.set("mail_default_sender", "ops@powergrid.in")
        ok, message = send_test_email("engineer1@powergrid.in")

    assert ok is True
    assert "engineer1@powergrid.in" in message
    assert sent["message"].subject.startswith("PowerGrid Operations")
    assert sent["message"].recipients == ["engineer1@powergrid.in"]
    assert "smtp.example.com:587" in sent["message"].body


def test_send_test_email_reports_smtp_failure(app, monkeypatch):
    def fail(_message):
        raise ConnectionError("relay refused")

    monkeypatch.setattr(mail, "send", fail)
    with app.app_context():
        AppSettings.set("mail_server", "smtp.example.com")
        AppSettings.set("mail_username", "ops@powergrid.in")
        ok, message = send_test_email("engineer1@powergrid.in")

    assert ok is False
    assert "relay refused" in message


def test_reminder_is_skipped_without_smtp_username(app):
    called = {"send": False}

    def capture(_message):
        called["send"] = True

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "engineer1@powergrid.in"))
        defect = db.session.scalar(db.select(Defect))
        app.config["MAIL_USERNAME"] = ""
        send_reminder_email(user, [defect])
    assert called["send"] is False


def test_reminder_email_lists_pending_defects(app, monkeypatch):
    sent = {}

    def capture(message):
        sent["message"] = message

    monkeypatch.setattr(mail, "send", capture)
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "engineer1@powergrid.in"))
        defects = db.session.scalars(
            db.select(Defect).where(Defect.assigned_user_id == user.id)
        ).all()
        app.config["MAIL_USERNAME"] = "ops@powergrid.in"
        app.config["MAIL_DEFAULT_SENDER"] = "ops@powergrid.in"
        send_reminder_email(user, defects)

    body = sent["message"].body
    assert sent["message"].recipients == ["engineer1@powergrid.in"]
    assert "pending defect" in sent["message"].subject
    assert "engineer1" in body
    assert "DFT-" in body
