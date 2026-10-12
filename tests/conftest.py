from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.crypto import seal
from core.models import DemoClient, Subscription, User
from core.panel import new_state
from core.services import record_observation

PASSWORD = "Test-only-long-passphrase-2026"


@pytest.fixture(autouse=True)
def safe_environment(settings):
    settings.PANEL_MODE = "demo"
    settings.BOT_TOKEN = ""
    settings.PANEL_WRITES_ENABLED = False
    settings.PANEL_EXCLUDED_CLIENTS = set()
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]


@pytest.fixture
def user(db):
    return User.objects.create_user(username="alice", password=PASSWORD)


@pytest.fixture
def admin(db):
    return User.objects.create_user(username="owner", password=PASSWORD, is_staff=True)


@pytest.fixture
def sub(user):
    state = new_state("alice-panel", int((timezone.now() + timedelta(days=10)).timestamp() * 1000))
    state["record_id"] = 41
    DemoClient.objects.create(email="alice-panel", data=seal(state))
    subscription = Subscription.objects.create(
        owner=user, label="Alice", panel_email="alice-panel", managed=True, sync_status="verified"
    )
    record_observation(subscription, state, adopt=True)
    return subscription


def logged_client(user, staff=False, csrf=False):
    import time

    client = Client(enforce_csrf_checks=csrf)
    client.force_login(user)
    session = client.session
    session["auth_version"] = user.auth_version
    session["last_activity"] = int(time.time())
    session["login_at"] = int(time.time())
    if staff:
        from core.crypto import seal

        user.totp_secret = seal("JBSWY3DPEHPK3PXP")
        user.save()
        session["mfa_at"] = time.time()
    session.save()
    return client


@pytest.fixture
def portal(user):
    return logged_client(user)


@pytest.fixture
def staff(admin):
    return logged_client(admin, staff=True)
