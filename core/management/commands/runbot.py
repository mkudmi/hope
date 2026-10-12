import asyncio

from aiogram.exceptions import TelegramAPIError
from django.core.management.base import BaseCommand, CommandError

from core.bot import run_bot
from core.services import DomainError


class Command(BaseCommand):
    help = "Run Telegram private-chat bot with long polling."

    def handle(self, **options):
        try:
            asyncio.run(run_bot())
        except DomainError as exc:
            raise CommandError(str(exc)) from None
        except (TelegramAPIError, ValueError):
            raise CommandError(
                "Telegram отклонил подключение. Проверьте токен и настройки бота в .env."
            ) from None
