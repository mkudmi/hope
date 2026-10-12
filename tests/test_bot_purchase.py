import asyncio
import uuid

import pytest
from aiogram import Bot
from aiogram.types import Update
from asgiref.sync import sync_to_async
from django.db import connections

from core.bot import build_dispatcher
from core.jobs import apply_operation
from core.models import BotContact, Operation, PurchaseOrder, Request, TelegramIdentity, Token
from core.orders import offer_key, plans, prepare_order, review_order, sync_order_results
from tests.test_bot import FakeSession, update

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def approved_and_close(settings):
    settings.TARIFFS_APPROVED = True
    settings.TARIFF_PRICES = {"week": 99, "month": 299, "quarter": 799, "halfyear": 1499, "year": 2799}
    yield
    asyncio.run(sync_to_async(connections.close_all)())


def callback(data, user_id=101, private=True):
    message = update("/start", private).message.model_dump(mode="json")
    if private:
        message["chat"]["id"] = user_id
    return Update.model_validate(
        {
            "update_id": 99,
            "callback_query": {
                "id": str(uuid.uuid4()),
                "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
                "chat_instance": "test",
                "message": message,
                "data": data,
            },
        }
    )


def bot_fixture():
    session = FakeSession()
    bot = Bot("123456789:synthetic_test_token_for_offline_tests", session=session)
    return build_dispatcher(), bot, session


def texts(session):
    return [m.text for m in session.sent if getattr(m, "text", None)]


def test_bot_new_user_can_choose_price_report_payment_and_connect(admin):
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("/start")))
    asyncio.run(dp.feed_update(bot, callback("prices")))
    assert any("299 ₽" in t for t in texts(session))
    asyncio.run(dp.feed_update(bot, callback("plan:month")))
    plan = plans()[1]
    buy = f"buy:month:{offer_key(plan)}"
    asyncio.run(dp.feed_update(bot, callback(buy)))
    asyncio.run(dp.feed_update(bot, callback(buy)))
    order = PurchaseOrder.objects.get()
    assert order.price_rub == 299 and not Operation.objects.exists()
    assert any("Реквизиты администратор пришлёт лично" in t for t in texts(session))
    asyncio.run(dp.feed_update(bot, callback(f"paid:{order.pk}")))
    order.refresh_from_db()
    assert order.status == "payment_review" and not Operation.objects.exists()
    review_order(order.pk, admin, "confirm", "Manually verified transfer")
    op = prepare_order(order.pk, admin)
    apply_operation(op.pk)
    sync_order_results()
    asyncio.run(dp.feed_update(bot, callback("connect")))
    asyncio.run(dp.feed_update(bot, callback("device:ios")))
    assert any("российским App Store используйте INCY" in t for t in texts(session))
    asyncio.run(dp.feed_update(bot, callback("client:incy:ios")))
    assert any("/demo-subscription/" in t for t in texts(session))
    sent = session.sent[-1]
    urls = [button.url for row in sent.reply_markup.inline_keyboard for button in row if button.url]
    assert urls == ["https://apps.apple.com/ru/app/incy/id6756943388"]
    asyncio.run(dp.feed_update(bot, callback("portal")))
    assert Token.objects.filter(kind="onboard").count() == 1
    assert any("/setup-account/?token=" in t for t in texts(session))


def test_group_cannot_buy_or_receive_configuration(sub, user):
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    dp, bot, session = bot_fixture()
    plan = plans()[1]
    for data in [f"buy:month:{offer_key(plan)}", "device:ios", "client:incy:ios"]:
        asyncio.run(dp.feed_update(bot, callback(data, private=False)))
    assert not PurchaseOrder.objects.exists()
    assert set(texts(session)) == {"Откройте личный чат."}


def test_foreign_callback_and_invalid_uuid_do_not_leak_order(admin):
    dp, bot, session = bot_fixture()
    plan = plans()[1]
    asyncio.run(dp.feed_update(bot, callback(f"buy:month:{offer_key(plan)}")))
    order = PurchaseOrder.objects.get()
    session.sent.clear()
    asyncio.run(dp.feed_update(bot, callback(f"order:{order.pk}", user_id=202)))
    asyncio.run(dp.feed_update(bot, callback("order:bad-id")))
    assert not any(order.number in t for t in texts(session))
    assert any("Заказ не найден" in t for t in texts(session))


def test_bot_support_survives_dispatcher_restart():
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, callback("support")))
    assert BotContact.objects.get(telegram_id=101).support_pending
    dp = build_dispatcher()
    message = update("How do I connect?")
    message = message.model_copy(update={"message": message.message.model_copy(update={"entities": []})})
    asyncio.run(dp.feed_update(bot, message))
    assert Request.objects.get().text == "How do I connect?"
    assert not BotContact.objects.get(telegram_id=101).support_pending
    assert any("Обращение сохранено" in text for text in texts(session))
