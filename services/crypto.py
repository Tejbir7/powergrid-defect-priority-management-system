import os
import base64
import hashlib
from cryptography.fernet import Fernet
from flask import current_app


def get_cipher(secret_key=None):
    """Derive a Fernet cipher from the application SECRET_KEY."""
    if not secret_key:
        if current_app:
            secret_key = current_app.config.get("SECRET_KEY")
        if not secret_key:
            secret_key = os.getenv("SECRET_KEY", "default-secret-key")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    return Fernet(key)
