import hashlib
import json
import secrets

from cryptography.fernet import Fernet
from django.conf import settings


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def seal(value):
    return Fernet(settings.DATA_KEY.encode()).encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def unseal(value):
    return json.loads(Fernet(settings.DATA_KEY.encode()).decrypt(value.encode())) if value else None


def random_token():
    return secrets.token_urlsafe(24)
