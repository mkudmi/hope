import asyncio
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update
from asgiref.sync import sync_to_async
from django.db import connections

from core.bot import build_dispatcher
from core.models import TelegramIdentity, Token
from core.services import issue_token

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def close_bot_database_thread():
    yield
    asyncio.run(sync_to_async(connections.close_all)())


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if method.__api_method__ == "sendMessage":
            return Message(
                message_id=99,
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""


def update(text, private=True):
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "from": {"id": 101, "is_bot": False, "first_name": "Alice"},
                "chat": {
                    "id": 101 if private else -999,
                    "type": "private" if private else "group",
                    "title": "Group",
                },
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
            },
        }
    )


def test_start_offer_does_not_create_vpn():
    session = FakeSession()
    bot = Bot("123456789:synthetic_test_token_for_offline_tests", session=session)
    asyncio.run(build_dispatcher().feed_update(bot, update("/start")))
    assert any("персональное приглашение" in (getattr(m, "text", "") or "") for m in session.sent)


def test_bot_telegram_link_only_sets_candidate(user):
    raw, token = issue_token("telegram", user=user, session="browser")
    session = FakeSession()
    bot = Bot("123456789:synthetic_test_token_for_offline_tests", session=session)
    asyncio.run(build_dispatcher().feed_update(bot, update("/start " + raw)))
    token.refresh_from_db()
    assert token.candidate_id == 101 and not token.bot_confirmed
    assert not TelegramIdentity.objects.exists()


def test_group_cannot_link(user):
    raw, token = issue_token("telegram", user=user, session="browser")
    session = FakeSession()
    bot = Bot("123456789:synthetic_test_token_for_offline_tests", session=session)
    asyncio.run(build_dispatcher().feed_update(bot, update("/start " + raw, private=False)))
    token.refresh_from_db()
    assert token.candidate_id is None and not session.sent


def test_recovery_only_for_linked_telegram(user):
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    session = FakeSession()
    bot = Bot("123456789:synthetic_test_token_for_offline_tests", session=session)
    asyncio.run(build_dispatcher().feed_update(bot, update("/recover")))
    assert Token.objects.filter(kind="reset", user=user).count() == 1
    assert any("/recover/?token=" in (getattr(m, "text", "") or "") for m in session.sent)


def test_startup_checks_bot_name(settings):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from core.bot import run_bot
    from core.services import DomainError

    settings.BOT_TOKEN = "synthetic-token"
    settings.BOT_USERNAME = "expectedbot"
    fake = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="otherbot")),
        session=SimpleNamespace(close=AsyncMock()),
    )
    with patch("core.bot.Bot", return_value=fake), pytest.raises(DomainError):
        asyncio.run(run_bot())
    fake.session.close.assert_awaited_once()


def test_startup_preserves_existing_webhook(settings):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from core.bot import run_bot
    from core.services import DomainError

    settings.BOT_TOKEN = "synthetic-token"
    settings.BOT_USERNAME = "expectedbot"
    fake = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="expectedbot")),
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="https://existing.example/webhook")),
        delete_webhook=AsyncMock(),
        set_my_commands=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )
    with patch("core.bot.Bot", return_value=fake), pytest.raises(DomainError):
        asyncio.run(run_bot())
    fake.delete_webhook.assert_not_awaited()
    fake.set_my_commands.assert_not_awaited()


def test_startup_installs_commands_and_polls(settings):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from core.bot import run_bot

    settings.BOT_TOKEN = "synthetic-token"
    settings.BOT_USERNAME = "expectedbot"
    fake = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="expectedbot")),
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="")),
        set_my_commands=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )
    dp = SimpleNamespace(start_polling=AsyncMock())
    with patch("core.bot.Bot", return_value=fake), patch("core.bot.build_dispatcher", return_value=dp):
        asyncio.run(run_bot())
    fake.set_my_commands.assert_awaited_once()
    dp.start_polling.assert_awaited_once()
