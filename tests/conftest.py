import os

os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_SCHEDULER"] = "False"

import pytest
from sqlalchemy.pool import StaticPool

from app import create_app
from config import Config
from extensions import db
from cli.commands import seed_demo_data


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    MAIL_SUPPRESS_SEND = True
    MAIL_SERVER = "smtp.example.com"
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = ""
    MAIL_PASSWORD = ""


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        seed_demo_data()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email, password="powergrid123"):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


@pytest.fixture
def admin(client):
    login(client, "admin@powergrid.in")
    return client


@pytest.fixture
def engineer(client):
    login(client, "engineer1@powergrid.in")
    return client
