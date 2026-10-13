import asyncio
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from aiogram.types import Update
from asgiref.sync import sync_to_async
from django.db import connection, connections
from django.utils import timezone

from core.crypto import unseal
from core.jobs import apply_operation, deliver_notices
from core.models import Notice, Operation, OrderApproval, OrderMessage, PurchaseOrder, TelegramIdentity, User
from core.orders import (
    approve_renewal,
    bot_contact,
    offer_key,
    place_order,
    place_web_order,
    plans,
    preview_renewal,
    report_web_payment,
    sync_order_results,
)
from core.panel import DemoPanel
from core.services import DomainError
from tests.conftest import PASSWORD, logged_client
from tests.test_bot_purchase import bot_fixture, callback, texts

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def configured(settings):
    settings.TARIFFS_APPROVED = True
    settings.TARIFF_PRICES = {"week": 99, "month": 299, "quarter": 799, "halfyear": 1499, "year": 2799}
    settings.ADMIN_GROUP_CHAT_ID = "-100123456"
    settings.ADMIN_TELEGRAM_USER_IDS = {707, 708}
    settings.ADMIN_TELEGRAM_LOGIN = "owner"
    yield
    asyncio.run(sync_to_async(connections.close_all)())


@pytest.fixture
def web_order(user, sub, admin):
    plan = plans(include_long=True)[1]
    return place_web_order(user, plan["code"], offer_key(plan))


def admin_callback(data, user_id=707, chat_id=-100123456):
    event = callback(data, user_id=user_id, private=False).model_dump(mode="json", by_alias=True)
    event["callback_query"]["message"]["chat"]["id"] = chat_id
    return Update.model_validate(event)


def group_reply(user_id=707, reply_id=501, chat_id=-100123456, anonymous=False):
    message = {
        "message_id": 601, "date": 1700000000,
        "chat": {"id": chat_id, "type": "supergroup", "title": "Synthetic staff"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
        "text": "Тестовые реквизиты: согласованный перевод, без реальных данных.",
        "reply_to_message": {
            "message_id": reply_id, "date": 1700000000,
            "chat": {"id": chat_id, "type": "supergroup", "title": "Synthetic staff"},
            "from": {"id": 123456789, "is_bot": True, "first_name": "Bot"},
            "text": "Untrusted quoted text is not used as an order identifier",
        },
    }
    if anonymous:
        message["sender_chat"] = message["chat"]
    return Update.model_validate({"update_id": 101, "message": message})


def test_web_choice_and_payment_are_not_extension(portal, user, sub, admin, settings):
    before, version = sub.snapshot, sub.version
    content = portal.get("/renewal/").content.decode()
    assert all(str(price) in content for price in [99, 299, 799, 1499, 2799])
    assert portal.post("/renewal/", {"plan": "month:forged"}).status_code == 200
    assert not PurchaseOrder.objects.exists()
    plan = plans(include_long=True)[1]
    choice = f"month:{offer_key(plan)}"
    response = portal.post("/renewal/", {"plan": choice})
    order = PurchaseOrder.objects.get()
    assert response.url == f"/orders/{order.pk}/"
    assert order.source == "web" and order.contact_id is None and order.user_id == user.pk
    assert portal.post("/renewal/", {"plan": choice}).url == response.url
    assert PurchaseOrder.objects.count() == 1
    settings.TARIFF_PRICES = {**settings.TARIFF_PRICES, "month": 500}
    assert portal.post("/renewal/", {"plan": choice}).status_code == 200
    order.refresh_from_db()
    assert order.price_rub == 299
    portal.post(response.url, {"action": "paid"})
    portal.post(response.url, {"action": "paid"})
    order.refresh_from_db()
    assert order.status == "payment_review" and not order.confirmed_at
    assert not Operation.objects.exists() and not TelegramIdentity.objects.exists()
    sub.refresh_from_db()
    assert sub.snapshot == before and sub.version == version
    assert Notice.objects.filter(admin_event=True, purchase_order=order).count() == 2


def test_cross_channel_active_order_is_shared(web_order, user, sub):
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    plan = plans()[1]
    assert place_order(bot_contact(101, 101), "month", offer_key(plan)).pk == web_order.pk
    assert place_web_order(user, "month", offer_key(plan)).pk == web_order.pk
    assert PurchaseOrder.objects.count() == 1


def test_group_reply_is_correlated_and_visible_without_telegram(web_order, admin, portal, settings):
    settings.BOT_TOKEN = "synthetic-token"
    with patch("core.jobs.httpx.post", return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 501}})):
        assert deliver_notices() == 1
    notice = Notice.objects.get(admin_event=True)
    assert notice.telegram_chat_id == "-100123456" and notice.telegram_message_id == 501
    dp, bot, _session = bot_fixture()
    asyncio.run(dp.feed_update(bot, group_reply()))
    asyncio.run(dp.feed_update(bot, group_reply()))
    assert OrderMessage.objects.filter(order=web_order).count() == 1
    reply = OrderMessage.objects.get()
    assert reply.actor_id == admin.pk
    assert reply.text in portal.get(f"/orders/{web_order.pk}/").content.decode()
    updates = portal.get(f"/orders/{web_order.pk}/?updates=1").json()
    assert str(reply.pk) in updates["version"] and "Тестовые реквизиты" not in str(updates)
    assert not Notice.objects.filter(admin_event=True, text__contains="Тестовые реквизиты").exists()
    assert Notice.objects.filter(user=web_order.user, admin_event=False, text__contains=reply.text).count() == 1


@pytest.mark.parametrize("case", ["user", "chat", "quote", "anonymous"])
def test_untrusted_reply_does_not_reach_customer(web_order, admin, case):
    Notice.objects.filter(admin_event=True).update(telegram_chat_id="-100123456", telegram_message_id=501)
    event = group_reply(
        user_id=909 if case == "user" else 707,
        chat_id=-100999 if case == "chat" else -100123456,
        reply_id=999 if case == "quote" else 501,
        anonymous=case == "anonymous",
    )
    dp, bot, _session = bot_fixture()
    asyncio.run(dp.feed_update(bot, event))
    assert not OrderMessage.objects.exists()


def test_chat_manual_approval_is_one_extension(web_order, admin, sub, settings):
    before = DemoPanel().get(sub.panel_email)
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, admin_callback(f"ao:preview:{web_order.pk}")))
    approval = OrderApproval.objects.get()
    assert approval.telegram_user_id == 707 and not Operation.objects.exists()
    assert str(web_order.price_rub) in texts(session)[-1]
    event = admin_callback(f"ao:approve:{approval.pk}")
    asyncio.run(dp.feed_update(bot, event))
    asyncio.run(dp.feed_update(bot, event))
    web_order.refresh_from_db()
    assert web_order.status == "provisioning" and web_order.operation_id == web_order.pk
    assert Operation.objects.count() == 1
    apply_operation(web_order.operation_id)
    sync_order_results()
    sync_order_results()
    web_order.refresh_from_db()
    assert web_order.status == "fulfilled"
    after = DemoPanel().get(sub.panel_email)
    for field in ["id", "subId", "auth", "password", "totalGB", "enable", "limitIp", "tgId"]:
        assert after["client"][field] == before["client"][field]
    assert after["inboundIds"] == before["inboundIds"]
    assert after["client"]["expiryTime"] > before["client"]["expiryTime"]
    assert Notice.objects.filter(key=f"purchase-ready:{web_order.pk}", user=web_order.user).count() == 1


@pytest.mark.parametrize("case", ["expired", "version", "price", "channel", "actor", "order-status", "owner", "telegram-id"])
def test_stale_or_foreign_approval_cannot_extend(web_order, admin, sub, case):
    approval = preview_renewal(web_order.pk, admin, "Synthetic transfer checked", telegram_user_id=707)
    actor, telegram_id = admin, 707
    if case == "expired":
        approval.expires_at = timezone.now() - timedelta(seconds=1)
        approval.save()
    elif case == "version":
        sub.version += 1
        sub.save()
    elif case == "price":
        web_order.price_rub += 1
        web_order.save()
    elif case == "channel":
        telegram_id = None
    elif case == "actor":
        actor = User.objects.create_user(username="otheradmin", password=PASSWORD, is_staff=True)
    elif case == "owner":
        sub.owner = User.objects.create_user(username="otherowner", password=PASSWORD)
        sub.save()
    elif case == "telegram-id":
        telegram_id = 708
    else:
        report_web_payment(web_order.pk, web_order.user)
    with pytest.raises(DomainError):
        approve_renewal(approval.pk, actor, telegram_user_id=telegram_id)
    assert not Operation.objects.exists()
    web_order.refresh_from_db()
    assert not web_order.confirmed_at


@pytest.mark.parametrize("code,days,months,price", [
    ("week", 7, 0, 99), ("month", 0, 1, 299), ("quarter", 0, 3, 799),
    ("halfyear", 0, 6, 1499), ("year", 0, 12, 2799),
])
def test_web_supports_each_approved_period(user, sub, code, days, months, price):
    plan = next(plan for plan in plans(include_long=True) if plan["code"] == code)
    order = place_web_order(user, code, offer_key(plan))
    assert (order.days, order.months, order.price_rub) == (days, months, price)
    assert not Operation.objects.exists()


@pytest.mark.parametrize("guard", ["write-off", "conflict", "pending"])
def test_preparation_error_rolls_back_combined_payment(web_order, admin, sub, settings, guard):
    approval = preview_renewal(web_order.pk, admin, "Synthetic transfer checked")
    if guard == "write-off":
        settings.PANEL_MODE = "real"
    elif guard == "conflict":
        sub.sync_status = "conflict"
        sub.save()
    else:
        from core.services import prepare_operation

        prepare_operation(sub.pk, admin, kind="renew", reason="Other operation", version=sub.version, months=1)
    before_count = Operation.objects.count()
    with pytest.raises(DomainError):
        approve_renewal(approval.pk, admin)
    web_order.refresh_from_db()
    approval.refresh_from_db()
    assert web_order.status == "awaiting_payment" and not web_order.confirmed_at
    assert not approval.consumed_at and Operation.objects.count() == before_count


def test_web_reply_and_combined_confirmation(web_order, staff, portal, sub):
    path = f"/staff/orders/{web_order.pk}/"
    assert staff.get(path).status_code == 200
    payload = {"action": "reply", "reply_id": "12345678-1234-1234-1234-123456789abc", "reply": "Synthetic payment instructions"}
    staff.post(path, payload)
    staff.post(path, payload)
    assert OrderMessage.objects.count() == 1
    assert "Synthetic payment instructions" in portal.get(f"/orders/{web_order.pk}/").content.decode()
    response = staff.post(path, {"action": "preview-renewal", "checked_transfer": "on", "note": "Transfer verified manually"})
    assert response.status_code == 200 and not Operation.objects.exists()
    approval = response.context["approval"]
    staff.post(path, {"action": "approve-renewal", "approval": str(approval.pk)})
    staff.post(path, {"action": "approve-renewal", "approval": str(approval.pk)})
    assert Operation.objects.count() == 1
    assert unseal(Operation.objects.get().target)["client"]["subId"] == unseal(sub.snapshot)["client"]["subId"]


def test_customer_boundaries_and_csrf(web_order, user, sub, admin, settings):
    other = User.objects.create_user(username="othercustomer", password=PASSWORD)
    client = logged_client(other)
    path = f"/orders/{web_order.pk}/"
    assert client.get(path).status_code == 404
    assert client.get(path + "?updates=1").status_code == 404
    assert client.post(path, {"action": "paid"}).status_code == 404
    assert web_order.number not in client.get("/orders/").content.decode()
    csrf_client = logged_client(user, csrf=True)
    assert csrf_client.post(path, {"action": "paid"}).status_code == 403
    assert csrf_client.post("/renewal/", {"plan": "month:forged"}).status_code == 403
    dp, bot, _session = bot_fixture()
    asyncio.run(dp.feed_update(bot, admin_callback(f"ao:preview:{web_order.pk}", user_id=909)))
    asyncio.run(dp.feed_update(bot, admin_callback(f"ao:preview:{web_order.pk}", chat_id=-100999)))
    settings.ADMIN_TELEGRAM_USER_IDS = set()
    asyncio.run(dp.feed_update(bot, admin_callback(f"ao:preview:{web_order.pk}")))
    assert not OrderApproval.objects.exists()


def test_postgres_serializes_web_renewals(user, sub):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks require an isolated PostgreSQL")
    from concurrent.futures import ThreadPoolExecutor

    plan = plans(include_long=True)[1]

    def create(_):
        try:
            return place_web_order(user, "month", offer_key(plan)).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(create, range(2)))
    assert result[0] == result[1] and PurchaseOrder.objects.count() == 1
