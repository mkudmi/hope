import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from asgiref.sync import sync_to_async
from django.db import connections

from core.models import PurchaseOrder, TelegramIdentity, Token
from core.orders import offer_key, plans
from core.services import issue_token
from tests.test_bot import FakeSession, update
from tests.test_bot_purchase import bot_fixture, callback

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def navigation_environment(settings):
    settings.TARIFFS_APPROVED = True
    settings.TARIFF_PRICES = {"week": 99, "month": 299, "quarter": 799, "halfyear": 1499, "year": 2799}
    yield
    asyncio.run(sync_to_async(connections.close_all)())


def methods(session, name):
    return [method for method in session.sent if method.__api_method__ == name]


def test_navigation_replaces_same_screen():
    dp, bot, session = bot_fixture()
    for route in ["menu", "prices", "plan:month", "prices", "menu", "portal", "help"]:
        asyncio.run(dp.feed_update(bot, callback(route)))
    edits = methods(session, "editMessageText")
    assert len(edits) == 7 and not methods(session, "sendMessage")
    assert {(edit.chat_id, edit.message_id) for edit in edits} == {(101, 1)}
    assert all(edit.reply_markup for edit in edits)
    assert "Главное меню" in edits[5].reply_markup.inline_keyboard[0][0].text


@pytest.mark.parametrize("linked", [False, True])
def test_return_home_matches_start(user, linked):
    if linked:
        TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("/start")))
    start = methods(session, "sendMessage")[0]
    asyncio.run(dp.feed_update(bot, callback("prices")))
    asyncio.run(dp.feed_update(bot, callback("menu")))
    home = methods(session, "editMessageText")[-1]
    assert home.text == start.text
    assert home.reply_markup == start.reply_markup
    assert len(methods(session, "sendMessage")) == 1
    routes = {button.callback_data for row in home.reply_markup.inline_keyboard for button in row}
    assert ("existing" in routes) is not linked
    assert ("status" in routes) is linked


def test_return_home_uses_current_link(user):
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("/start")))
    welcome = methods(session, "sendMessage")[0]
    identity = TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    asyncio.run(dp.feed_update(bot, callback("menu")))
    assert "ваша подписка" in methods(session, "editMessageText")[-1].text.lower()
    identity.delete()
    asyncio.run(dp.feed_update(bot, callback("menu")))
    home = methods(session, "editMessageText")[-1]
    assert home.text == welcome.text and home.reply_markup == welcome.reply_markup
    assert len(methods(session, "sendMessage")) == 1


def test_order_status_and_cancel_replace_screen():
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, callback(f"buy:month:{offer_key(plans()[1])}")))
    order = PurchaseOrder.objects.get()
    for route in ["orders", f"order:{order.pk}", f"cancel-order:{order.pk}"]:
        asyncio.run(dp.feed_update(bot, callback(route)))
    order.refresh_from_db()
    assert order.status == "cancelled"
    assert len(methods(session, "editMessageText")) == 4
    assert not methods(session, "sendMessage")


def test_bind_confirmation_replaces_screen(user):
    raw, token = issue_token("telegram", user=user, session="browser")
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("/start " + raw)))
    session.sent.clear()
    asyncio.run(dp.feed_update(bot, callback(f"bind:{token.pk}")))
    token.refresh_from_db()
    assert token.bot_confirmed
    assert len(methods(session, "editMessageText")) == 1
    assert not methods(session, "sendMessage")


def test_commands_still_send_and_errors_edit():
    dp, bot, session = bot_fixture()
    asyncio.run(dp.feed_update(bot, update("/prices")))
    asyncio.run(dp.feed_update(bot, callback("plan:missing")))
    assert len(methods(session, "sendMessage")) == 1
    assert len(methods(session, "editMessageText")) == 1
    assert "Тариф недоступен" in methods(session, "editMessageText")[0].text


class EditFailureSession(FakeSession):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def make_request(self, bot, method, timeout=None):
        if method.__api_method__ == "editMessageText":
            self.sent.append(method)
            if self.error == "retry":
                raise TelegramRetryAfter(method=method, message="Flood control", retry_after=3)
            if self.error == "network":
                raise TelegramNetworkError(method=method, message="Connection lost")
            raise TelegramBadRequest(method=method, message=self.error)
        return await super().make_request(bot, method, timeout)


@pytest.mark.parametrize(
    "error,new_messages",
    [("message is not modified", 0), ("message to edit not found", 1), ("message can't be edited", 1)],
)
def test_known_edit_failures(error, new_messages):
    dp, bot, _ = bot_fixture()
    session = EditFailureSession(error)
    bot.session = session
    asyncio.run(dp.feed_update(bot, callback("menu")))
    assert len(methods(session, "editMessageText")) == 1
    assert len(methods(session, "sendMessage")) == new_messages


@pytest.mark.parametrize(
    "error,exception",
    [("network", TelegramNetworkError), ("retry", TelegramRetryAfter), ("bad markup", TelegramBadRequest)],
)
def test_other_errors_do_not_send_duplicates(error, exception):
    dp, bot, _ = bot_fixture()
    session = EditFailureSession(error)
    bot.session = session
    with pytest.raises(exception):
        asyncio.run(dp.feed_update(bot, callback("menu")))
    assert not methods(session, "sendMessage")


def test_inaccessible_callback_only_answers_alert():
    dp, bot, session = bot_fixture()
    incoming = callback("portal").model_dump(mode="json")
    incoming["callback_query"]["message"]["date"] = 0
    from aiogram.types import Update

    asyncio.run(dp.feed_update(bot, Update.model_validate(incoming)))
    assert not methods(session, "sendMessage") and not methods(session, "editMessageText")
    assert methods(session, "answerCallbackQuery")[0].show_alert
    assert not Token.objects.exists()
