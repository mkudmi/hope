from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree

import pyotp
import pytest
import qrcode
from django.test import Client, override_settings
from django.urls import get_script_prefix, set_script_prefix

from core.crypto import unseal
from tests.conftest import logged_client

pytestmark = pytest.mark.django_db


def test_qr_uses_encrypted_session_secret_and_standard_authenticator_uri(admin):
    client = logged_client(admin)
    page = client.get("/staff/mfa/setup/")
    assert page.status_code == 200
    assert 'src="/staff/mfa/setup/qr/"' in page.content.decode()
    secret = unseal(client.session["totp_setup"])
    assert client.session["totp_setup"] != secret
    with patch("core.views.qrcode.make", wraps=qrcode.make) as make:
        image = client.get("/staff/mfa/setup/qr/")
    uri = urlparse(make.call_args.args[0])
    assert uri.scheme == "otpauth" and uri.netloc == "totp"
    assert unquote(uri.path).endswith("HOPE VPN:" + admin.username)
    assert parse_qs(uri.query)["secret"] == [secret]
    assert parse_qs(uri.query)["issuer"] == ["HOPE VPN"]
    assert image.status_code == 200 and image["Content-Type"] == "image/svg+xml"
    assert ElementTree.fromstring(image.content).tag.endswith("svg")
    assert secret.encode() not in image.content
    assert image["Cache-Control"] == "no-store, private"
    assert image["Referrer-Policy"] == "same-origin"
    admin.refresh_from_db()
    assert not admin.totp_secret  # Viewing/scanning must not enrol the factor.


def test_qr_requires_staff_prepared_session_and_cannot_cross_sessions(admin, user):
    assert Client().get("/staff/mfa/setup/qr/").status_code == 302
    assert logged_client(user).get("/staff/mfa/setup/qr/").status_code == 403
    first = logged_client(admin)
    assert first.get("/staff/mfa/setup/qr/").status_code == 404
    first.get("/staff/mfa/setup/")
    assert logged_client(admin).get("/staff/mfa/setup/qr/").status_code == 404
    assert first.post("/staff/mfa/setup/qr/").status_code == 405


def test_invalid_submission_keeps_same_qr_and_success_hides_setup(admin):
    client = logged_client(admin)
    client.get("/staff/mfa/setup/")
    secret = unseal(client.session["totp_setup"])
    image = client.get("/staff/mfa/setup/qr/").content
    response = client.post("/staff/mfa/setup/", {"code": "bad"})
    assert "шестизначный код" in response.content.decode()
    assert client.get("/staff/mfa/setup/qr/").content == image
    response = client.post("/staff/mfa/setup/", {"code": pyotp.TOTP(secret).now()})
    assert response.status_code == 302 and response.url == "/staff/"
    admin.refresh_from_db()
    assert unseal(admin.totp_secret) == secret
    assert "totp_setup" not in client.session
    assert client.get("/staff/mfa/setup/qr/").status_code == 404
    page = client.get("/staff/mfa/").content.decode()
    assert secret not in page and "staff/mfa/setup/qr/" not in page


def test_qr_setup_form_preserves_csrf(admin):
    client = logged_client(admin, csrf=True)
    client.get("/staff/mfa/setup/")
    secret = unseal(client.session["totp_setup"])
    assert client.post("/staff/mfa/setup/", {"code": pyotp.TOTP(secret).now()}).status_code == 403
    admin.refresh_from_db()
    assert not admin.totp_secret


def test_prefixed_setup_qr_image_url(admin):
    previous = get_script_prefix()
    with override_settings(FORCE_SCRIPT_NAME="/hope", STATIC_URL="/hope/static/"):
        set_script_prefix("/hope")
        try:
            client = logged_client(admin)
            page = client.get("/staff/mfa/setup/", SCRIPT_NAME="/hope")
            assert 'src="/hope/staff/mfa/setup/qr/"' in page.content.decode()
            assert client.get("/staff/mfa/setup/qr/", SCRIPT_NAME="/hope").status_code == 200
        finally:
            set_script_prefix(previous)
