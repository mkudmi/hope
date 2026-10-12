import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.utils import timezone

from core.crypto import digest, seal
from core.models import RecoveryCode, Subscription, TelegramIdentity, Token, User
from core.services import (
    DomainError,
    activate,
    confirm_bot,
    confirm_web,
    invitation,
    issue_token,
    recovery_codes,
    reset_password,
    telegram_candidate,
)
from tests.conftest import PASSWORD, logged_client

pytestmark = pytest.mark.django_db


def test_invitation_keeps_existing_config(admin, sub):
    sub.owner = None
    sub.save()
    snapshot = sub.snapshot
    raw = invitation(sub, admin)
    user, codes = activate(raw, "MyLogin", PASSWORD)
    sub.refresh_from_db()
    assert sub.snapshot == snapshot
    assert sub.owner_id == user.id and user.username == "mylogin"
    assert len(codes) == 8 and RecoveryCode.objects.filter(user=user).count() == 8
    with pytest.raises(DomainError):
        activate(raw, "other-login", PASSWORD)


def test_invitation_get_does_not_consume(client, admin):
    sub = Subscription.objects.create(label="Old", panel_email="old")
    raw = invitation(sub, admin)
    response = client.get("/invite/" + raw + "/")
    assert response.status_code == 200
    assert Token.objects.get(digest=digest(raw)).consumed_at is None
    assert response.headers["Cache-Control"] == "no-store, private"


def test_expired_invite_cannot_activate(admin):
    sub = Subscription.objects.create(label="Old", panel_email="old")
    raw = invitation(sub, admin)
    Token.objects.filter(digest=digest(raw)).update(expires_at=timezone.now())
    with pytest.raises(DomainError):
        activate(raw, "alice", PASSWORD)
    assert not User.objects.filter(username="alice").exists()


def test_link_needs_both_sides_and_original_browser(user):
    raw, token = issue_token("telegram", user=user, session="browser-a")
    telegram_candidate(raw, 101, 101, "Alice TG")
    with pytest.raises(DomainError):
        confirm_web(token.id, user, "browser-a")
    confirm_bot(token.id, 101)
    with pytest.raises(DomainError):
        confirm_web(token.id, user, "browser-b")
    confirm_web(token.id, user, "browser-a")
    assert TelegramIdentity.objects.get(user=user).telegram_id == 101
    with pytest.raises(DomainError):
        confirm_web(token.id, user, "browser-a")


def test_forwarded_link_cannot_change_candidate(user):
    raw, _ = issue_token("telegram", user=user, session="browser")
    telegram_candidate(raw, 101, 101, "Alice")
    with pytest.raises(DomainError):
        telegram_candidate(raw, 102, 102, "Mallory")
    with pytest.raises(DomainError):
        telegram_candidate(raw, 101, -999, "Group")


def test_existing_telegram_cannot_be_stolen(user):
    other = User.objects.create_user(username="other", password=PASSWORD)
    TelegramIdentity.objects.create(user=other, telegram_id=101, chat_id=101)
    raw, _ = issue_token("telegram", user=user, session="browser")
    with pytest.raises(DomainError):
        telegram_candidate(raw, 101, 101, "Alice")


def test_recovery_revokes_old_sessions(user):
    old_client = logged_client(user)
    codes = recovery_codes(user)
    user, new_codes = reset_password("ALICE", PASSWORD + "-new", code=codes[0])
    assert user.check_password(PASSWORD + "-new")
    assert len(new_codes) == 8
    assert old_client.get("/cabinet/").status_code == 302
    with pytest.raises(DomainError):
        reset_password("alice", PASSWORD, code=codes[0])


def test_recovery_token_cannot_target_other_user(user):
    other = User.objects.create_user(username="other", password=PASSWORD)
    raw, _ = issue_token("reset", user=other)
    with pytest.raises(DomainError):
        reset_password("alice", PASSWORD, raw=raw)


def test_session_codes_are_encrypted(user):
    client = logged_client(user)
    session = client.session
    codes = ["private-recovery-test-code"]
    session["new_codes"] = seal(codes)
    session.save()
    stored = Session.objects.get(session_key=session.session_key).get_decoded()
    assert codes[0] not in stored["new_codes"]
    assert codes[0].encode() in client.get("/codes/").content


def test_csrf_and_admin_access(user, sub):
    client = logged_client(user, csrf=True)
    assert client.get("/staff/").status_code == 403
    assert client.post("/renewal/", {"text": "Request"}).status_code == 403
    assert Client().get("/connect/qr/").status_code == 302


def test_case_insensitive_login_and_rate_limit(client, user):
    assert client.post("/login/", {"username": "ALICE", "password": PASSWORD}).status_code == 302
    assert client.get("/cabinet/").status_code == 200
    client.post("/logout/")
    for _ in range(9):
        response = client.post("/login/", {"username": "alice", "password": "bad"})
    assert "Слишком много попыток".encode() in response.content


def test_staff_requires_mfa(admin):
    client = logged_client(admin)
    assert client.get("/staff/").url == "/staff/mfa/setup/"


def test_browser_can_bind_without_bot_network(portal, user):
    portal.post("/telegram/", {"action": "create"})
    session = portal.session
    token = Token.objects.get(pk=session["tg_link_id"])
    raw = __import__("core.crypto", fromlist=["unseal"]).unseal(session["tg_link_raw"])
    telegram_candidate(raw, 303, 303, "Alice")
    confirm_bot(token.id, 303)
    response = portal.post("/telegram/", {"action": "confirm"})
    assert response.status_code == 302
    assert TelegramIdentity.objects.get(user=user).telegram_id == 303
