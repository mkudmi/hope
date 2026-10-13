from datetime import timedelta
from unittest.mock import Mock

import pyotp
import pytest
from django.utils import timezone

from core.auth import TRUSTED_BROWSER_COOKIE
from core.crypto import seal, unseal
from core.models import TrustedBrowser
from core.panel import PanelError
from core.services import create_trusted_browser
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db
SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture
def panel_import(monkeypatch):
    mocked = Mock(return_value=2)
    monkeypatch.setattr("core.views.import_clients", mocked)
    return mocked


def configure_mfa(admin):
    admin.totp_secret = seal(SECRET)
    admin.save()


def login(client, user):
    return client.post("/login/", {"username": user.username, "password": PASSWORD})


def test_import_after_mfa_once_and_outside_transaction(client, admin, panel_import):
    from django.db import connection

    configure_mfa(admin)
    login(client, admin)
    assert client.get("/staff/").url == "/staff/mfa/"
    panel_import.assert_not_called()
    assert client.post("/staff/mfa/", {"code": "bad"}).status_code == 200
    panel_import.assert_not_called()
    # pytest wraps this test in a transaction: external import must not add
    # the user-row locking transaction around its network calls.
    depth = len(connection.atomic_blocks)

    def import_without_mfa_lock(actor):
        assert len(connection.atomic_blocks) == depth
        return 2

    panel_import.side_effect = import_without_mfa_lock
    response = client.post("/staff/mfa/", {"code": pyotp.TOTP(SECRET).now()})
    assert response.url == "/staff/"
    panel_import.assert_called_once_with(admin)
    assert not client.session.get("staff_import_pending")
    assert client.get("/staff/").status_code == 200
    assert client.get("/staff/?q=test").status_code == 200
    assert client.get("/staff/requests/").status_code == 200
    panel_import.assert_called_once()
    session = client.session
    session["mfa_at"] = 0
    session.save()
    admin.refresh_from_db()
    admin.totp_last_step = -1
    admin.save()
    client.post("/staff/mfa/", {"code": pyotp.TOTP(SECRET).now()})
    panel_import.assert_called_once()


def test_setup_finishes_pending_import(client, admin, panel_import):
    login(client, admin)
    assert client.get("/staff/").url == "/staff/mfa/setup/"
    client.get("/staff/mfa/setup/")
    secret = unseal(client.session["totp_setup"])
    panel_import.assert_not_called()
    assert client.post("/staff/mfa/setup/", {"code": pyotp.TOTP(secret).now()}).url == "/staff/"
    panel_import.assert_called_once_with(admin)


def test_remembered_browser_imports_once_on_each_password_login(client, admin, panel_import):
    configure_mfa(admin)
    login(client, admin)
    client.post("/staff/mfa/", {"code": pyotp.TOTP(SECRET).now(), "remember_browser": "on"})
    assert TRUSTED_BROWSER_COOKIE in client.cookies
    panel_import.assert_called_once()
    client.post("/logout/")
    panel_import.reset_mock()
    assert login(client, admin).url == "/staff/"
    panel_import.assert_called_once_with(admin)
    assert client.get("/staff/").status_code == 200
    assert client.get("/staff/").status_code == 200
    panel_import.assert_called_once()
    client.post("/logout/")
    login(client, admin)
    assert panel_import.call_count == 2


@pytest.mark.parametrize("invalidity", ["expired", "password", "version", "unknown"])
def test_invalid_trust_does_not_import_before_mfa(client, admin, panel_import, invalidity):
    configure_mfa(admin)
    raw = create_trusted_browser(admin)
    if invalidity == "expired":
        TrustedBrowser.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    elif invalidity == "password":
        admin.set_password(PASSWORD)
        admin.save()
    elif invalidity == "version":
        admin.auth_version += 1
        admin.save()
    else:
        raw = "unknown-token"
    client.cookies[TRUSTED_BROWSER_COOKIE] = raw
    login(client, admin)
    assert client.get("/staff/").url == "/staff/mfa/"
    panel_import.assert_not_called()


def test_failed_import_keeps_login_and_manual_retry(client, admin, panel_import):
    configure_mfa(admin)
    client.cookies[TRUSTED_BROWSER_COOKIE] = create_trusted_browser(admin)
    panel_import.side_effect = PanelError("Панель временно недоступна.")
    response = client.post("/login/", {"username": admin.username, "password": PASSWORD}, follow=True)
    assert response.status_code == 200
    assert "Панель временно недоступна." in response.content.decode()
    panel_import.assert_called_once()
    assert client.get("/staff/").status_code == 200
    panel_import.assert_called_once()
    panel_import.side_effect = None
    client.post("/staff/import/")
    assert panel_import.call_count == 2


def test_client_and_wrong_password_do_not_import(client, user, admin, panel_import):
    assert login(client, user).url == "/cabinet/"
    client.post("/logout/")
    configure_mfa(admin)
    client.cookies[TRUSTED_BROWSER_COOKIE] = create_trusted_browser(admin)
    assert client.post("/login/", {"username": admin.username, "password": "wrong"}).status_code == 200
    panel_import.assert_not_called()


def test_new_password_login_clears_old_mfa_in_same_session(client, admin, panel_import):
    configure_mfa(admin)
    login(client, admin)
    client.post("/staff/mfa/", {"code": pyotp.TOTP(SECRET).now()})
    panel_import.reset_mock()
    login(client, admin)
    assert "mfa_at" not in client.session
    assert client.get("/staff/").url == "/staff/mfa/"
    panel_import.assert_not_called()
