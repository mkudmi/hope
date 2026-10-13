import asyncio
from unittest.mock import patch

import httpx
import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.utils import timezone

from core.bot import menu
from core.crypto import unseal
from core.jobs import deliver_notices
from core.models import Notice, TelegramIdentity
from core.services import DomainError, confirm_bot, confirm_web, issue_token, telegram_candidate
from tests.test_bot import update
from tests.test_bot_purchase import bot_fixture, callback, texts

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def close_bot_thread():
    yield
    asyncio.run(sync_to_async(connections.close_all)())


def complete_binding(user):
    raw, token = issue_token("telegram", user=user, session="test-browser")
    telegram_candidate(raw, 101, 101, "Alice TG")
    confirm_bot(token.pk, 101)
    confirm_web(token.pk, user, "test-browser")
    return token, Notice.objects.get(telegram_account_menu=True)


def test_browser_completion_sends_own_account_menu(portal, user, sub, admin, settings):
    settings.SITE_URL = "https://example.invalid/hope"
    settings.BOT_TOKEN = "synthetic-test-token"
    settings.ADMIN_GROUP_CHAT_ID = "-100123456"
    before_snapshot, before_version = sub.snapshot, sub.version
    assert portal.post("/telegram/", {"action": "create"}).status_code == 302
    raw = unseal(portal.session["tg_link_raw"])
    token_id = portal.session["tg_link_id"]
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("/start " + raw)))
    asyncio.run(dp.feed_update(bot, callback("bind:" + token_id)))
    assert not TelegramIdentity.objects.exists()
    assert not Notice.objects.filter(telegram_account_menu=True).exists()
    assert "До этого аккаунты ещё не связаны" in texts(session)[-1]
    assert getattr(session.sent[-1], "reply_markup", None) is None

    assert portal.post("/telegram/", {"action": "confirm"}).status_code == 302
    notice = Notice.objects.get(telegram_account_menu=True)
    assert notice.user_id == user.pk and not notice.admin_event
    with pytest.raises(DomainError):
        confirm_web(token_id, user, portal.session.session_key)
    assert Notice.objects.filter(telegram_account_menu=True).count() == 1
    with patch("core.jobs.httpx.post", return_value=httpx.Response(200, json={"ok": True})) as send:
        assert deliver_notices() == 2
        payloads = [call.kwargs["json"] for call in send.call_args_list]
    personal = next(payload for payload in payloads if payload["chat_id"] == 101)
    group = next(payload for payload in payloads if payload["chat_id"] == "-100123456")
    assert personal["reply_markup"] == menu().model_dump(mode="json", exclude_none=True)
    assert "меню ниже" in personal["text"]
    assert "reply_markup" not in group
    keys = [button for row in personal["reply_markup"]["inline_keyboard"] for button in row]
    assert {button["callback_data"] for button in keys if "callback_data" in button} == {
        "prices", "connect", "status", "renew", "orders", "help"
    }
    assert [button["url"] for button in keys if "url" in button] == [
        "https://example.invalid/hope/login/"
    ]
    asyncio.run(dp.feed_update(bot, callback("status")))
    assert sub.number in texts(session)[-1]
    sub.refresh_from_db()
    assert sub.snapshot == before_snapshot and sub.version == before_version


def test_binding_menu_retry_preserves_keyboard(user, settings):
    settings.BOT_TOKEN = "synthetic-test-token"
    _token, notice = complete_binding(user)
    with patch(
        "core.jobs.httpx.post",
        return_value=httpx.Response(200, json={"ok": False, "error_code": 429}),
    ) as send:
        assert deliver_notices() == 0
        first_payload = send.call_args.kwargs["json"]
    notice.refresh_from_db()
    assert notice.status == "pending" and notice.attempts == 1
    notice.available_at = timezone.now()
    notice.save(update_fields=["available_at"])
    with patch("core.jobs.httpx.post", return_value=httpx.Response(200, json={"ok": True})) as send:
        assert deliver_notices() == 1
        assert send.call_args.kwargs["json"] == first_payload
    assert deliver_notices() == 0
    assert Notice.objects.filter(telegram_account_menu=True).count() == 1


def test_unlink_before_delivery_cancels_menu(user, settings):
    settings.BOT_TOKEN = "synthetic-test-token"
    settings.ADMIN_CHAT_ID = "909"
    _token, notice = complete_binding(user)
    TelegramIdentity.objects.filter(user=user).delete()
    with patch("core.jobs.httpx.post") as send:
        assert deliver_notices() == 0
        send.assert_not_called()
    notice.refresh_from_db()
    assert notice.status == "cancelled"


def test_account_menu_cannot_be_mirrored_to_admin_group(admin, settings):
    settings.BOT_TOKEN = "synthetic-test-token"
    settings.ADMIN_GROUP_CHAT_ID = "-100123456"
    notice = Notice.objects.create(
        user=admin, key="synthetic-invalid-menu", text="Cabinet",
        admin_event=True, telegram_account_menu=True,
    )
    with patch("core.jobs.httpx.post") as send:
        assert deliver_notices() == 0
        send.assert_not_called()
    notice.refresh_from_db()
    assert notice.status == "cancelled"
