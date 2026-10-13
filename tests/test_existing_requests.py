import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest
from asgiref.sync import sync_to_async
from django.db import connection, connections
from django.utils import timezone

from core.jobs import deliver_notices
from core.models import Notice, Operation, Request, TelegramIdentity
from core.orders import bot_contact, request_existing_subscription
from core.services import admin_event, notify
from tests.test_bot_purchase import bot_fixture, callback, texts

pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_button_sends_profile_once_and_waits(admin):
    dp, bot, session = bot_fixture()
    event = callback("existing")
    from aiogram.types import Update

    payload = event.model_dump(mode="json", by_alias=True)
    payload["callback_query"]["from"].update(username="alice_test", last_name="Тестовая")
    event = Update.model_validate(payload)
    for _ in range(2):
        asyncio.run(dp.feed_update(bot, event))
    item = Request.objects.get(kind="binding")
    assert "@alice_test" in item.text and "Alice" in item.text and "Тестовая" in item.text
    notice = Notice.objects.get(admin_event=True)
    assert notice.text.endswith("/staff/requests/") and "Telegram ID: 101" in notice.text
    assert any("Ожидайте ответа" in text for text in texts(session))
    assert not Operation.objects.exists() and not TelegramIdentity.objects.exists()
    asyncio.run(sync_to_async(connections.close_all)())


def test_missing_profile_duplicate_and_closed_request(admin):
    contact = bot_contact(101, 101, "Alice")
    first = request_existing_subscription(contact, first_name="Alice")
    assert "не указан" in first.text and "не указана" in first.text
    assert request_existing_subscription(contact).pk == first.pk
    first.status = "closed"
    first.save()
    assert request_existing_subscription(contact).pk != first.pk
    assert Notice.objects.filter(admin_event=True).count() == 2


def test_linked_or_group_cannot_request(admin, user):
    contact = bot_contact(101, 101)
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    assert request_existing_subscription(contact) is None
    dp, bot, _session = bot_fixture()
    asyncio.run(dp.feed_update(bot, callback("existing", private=False)))
    assert not Request.objects.exists()
    asyncio.run(sync_to_async(connections.close_all)())


def test_group_events_override_admin_identity_but_personal_notices_remain_private(admin, user, settings):
    settings.BOT_TOKEN = "synthetic-test-token"
    settings.ADMIN_GROUP_CHAT_ID = "-100123456"
    settings.ADMIN_CHAT_ID = "909"
    TelegramIdentity.objects.create(user=admin, telegram_id=707, chat_id=707)
    TelegramIdentity.objects.create(user=user, telegram_id=808, chat_id=808)
    admin_event("synthetic-event", "Новое событие")
    notify(user, "synthetic-personal", "Персональное уведомление")
    seen = []

    def post(url, json, timeout):
        seen.append(json)
        return httpx.Response(200, json={"ok": True})

    with patch("core.jobs.httpx.post", side_effect=post):
        assert deliver_notices() == 2
    assert {m["chat_id"] for m in seen} == {"-100123456", 808}
    assert next(m for m in seen if m["chat_id"] == "-100123456")["text"] == "Новое событие"
    assert all("entities" not in m and "parse_mode" not in m for m in seen)


def test_group_fallback_and_retry_without_duplicate(admin, settings):
    settings.BOT_TOKEN = "synthetic-test-token"
    settings.ADMIN_GROUP_CHAT_ID = ""
    settings.ADMIN_CHAT_ID = "-100999"
    admin_event("synthetic-retry", "Новое событие")
    admin_event("synthetic-retry", "Новое событие")
    with patch(
        "core.jobs.httpx.post",
        return_value=httpx.Response(
            200, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 30}}
        ),
    ) as post:
        assert deliver_notices() == 0
    assert post.call_args.kwargs["json"]["chat_id"] == "-100999"
    notice = Notice.objects.get()
    assert notice.status == "pending" and notice.attempts == 1


def test_postgres_serializes_existing_requests(admin):
    if connection.vendor != "postgresql":
        pytest.skip("Production row locking requires PostgreSQL")
    contact = bot_contact(101, 101)

    def submit(_):
        try:
            return request_existing_subscription(contact).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert Request.objects.count() == 1 and Notice.objects.filter(admin_event=True).count() == 1


def test_staff_reply_closes_binding_and_queues_private_answer(staff, admin):
    contact = bot_contact(101, 101, "Alice")
    item = request_existing_subscription(contact, first_name="Alice")
    response = staff.post(
        "/staff/requests/", {"id": item.pk, "reply": "Проверили контакт. Ожидайте личное приглашение."}
    )
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == "closed"
    private = Notice.objects.get(telegram_contact=contact)
    assert not private.admin_event and "Проверили контакт" in private.text
    assert not TelegramIdentity.objects.exists()


def test_waiting_text_does_not_create_support_request(admin):
    from tests.test_bot import update

    contact = bot_contact(101, 101)
    request_existing_subscription(contact, first_name="Alice")
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("Что дальше?")))
    assert Request.objects.count() == 1
    assert any("Ожидайте ответа" in text for text in texts(session))
    asyncio.run(sync_to_async(connections.close_all)())


def test_staff_operation_error_is_one_group_event(admin, sub):
    from core.jobs import finish_error
    from core.services import prepare_operation

    op = prepare_operation(
        sub.pk, admin, kind="renew", reason="Synthetic test", version=sub.version, months=1
    )
    finish_error(op, "failed", "Синтетическая ошибка")
    assert Notice.objects.filter(key__contains="op-error:").count() == 1
    assert Notice.objects.get(key__contains="op-error:").admin_event


@pytest.mark.parametrize("username", ["alice_test", ""])
def test_profile_link_entities_survive_delivery_retry(admin, settings, username):
    from aiogram.types import MessageEntity

    settings.BOT_TOKEN = "synthetic-test-token"
    settings.ADMIN_GROUP_CHAT_ID = "-100123456"
    contact = bot_contact(101, 101)
    first_name = '🚀 Alice <a href="https://example.invalid">name</a> & [text]'
    item = request_existing_subscription(
        contact, username=username, first_name=first_name, last_name="Тестовая 🌍"
    )
    assert request_existing_subscription(contact, username="changed").pk == item.pk
    notice = Notice.objects.get(admin_event=True)
    assert Notice.objects.filter(admin_event=True).count() == 1
    assert "tg://user" not in notice.text and "tg://user" not in item.text
    assert first_name in notice.text
    assert len(notice.telegram_entities) == 2
    for entity in notice.telegram_entities:
        MessageEntity.model_validate(entity)
    link, cabinet_link = notice.telegram_entities
    expected_url = "https://t.me/alice_test" if username else "tg://user?id=101"
    assert link["type"] == "text_link" and link["url"] == expected_url
    text_bytes = notice.text.encode("utf-16-le")
    selected = text_bytes[2 * link["offset"] : 2 * (link["offset"] + link["length"])].decode("utf-16-le")
    assert selected == "Открыть профиль"
    cabinet_selected = text_bytes[
        2 * cabinet_link["offset"] : 2 * (cabinet_link["offset"] + cabinet_link["length"])
    ].decode("utf-16-le")
    assert cabinet_selected == settings.SITE_URL + "/staff/requests/"
    assert ("https://t.me/alice_test" in item.text) if username else ("ответьте через обращение" in item.text)

    with patch(
        "core.jobs.httpx.post", return_value=httpx.Response(200, json={"ok": False, "error_code": 429})
    ) as post:
        assert deliver_notices() == 0
        first_payload = post.call_args.kwargs["json"]
    notice.refresh_from_db()
    assert notice.status == "pending" and notice.telegram_entities == first_payload["entities"]
    notice.available_at = timezone.now()
    notice.save(update_fields=["available_at"])
    with patch("core.jobs.httpx.post", return_value=httpx.Response(200, json={"ok": True})) as post:
        assert deliver_notices() == 1
        payload = post.call_args.kwargs["json"]
    assert payload == first_payload
    assert payload["chat_id"] == "-100123456" and "parse_mode" not in payload
