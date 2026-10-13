"""Explicitly trusted Telegram operators; no client identity grants staff access."""

import uuid

from aiogram import F
from aiogram.types import Message
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from .models import Notice, User
from .orders import approve_renewal, preview_renewal, reply_to_order
from .panel import PanelError
from .services import DomainError, rate_limit


def telegram_admin(user_id, chat_id):
    expected_chat = settings.ADMIN_GROUP_CHAT_ID or settings.ADMIN_CHAT_ID
    if user_id not in settings.ADMIN_TELEGRAM_USER_IDS or str(chat_id) != str(expected_chat):
        raise DomainError("Нет права обрабатывать заказы из этого Telegram или чата.")
    actor = User.objects.filter(
        username=settings.ADMIN_TELEGRAM_LOGIN, is_active=True, is_staff=True,
    ).first()
    if not actor:
        raise DomainError("Административный аккаунт Telegram не настроен.")
    return actor


def install_admin_handlers(dp):
    @dp.callback_query(F.data.startswith("ao:"))
    async def order_callback(callback):
        from .bot import buttons, render_screen

        answered = False
        try:
            if not isinstance(callback.message, Message) or callback.from_user.is_bot:
                raise DomainError("Откройте сообщение заказа в административном чате.")
            actor = await sync_to_async(telegram_admin)(callback.from_user.id, callback.message.chat.id)
            if not await sync_to_async(rate_limit)("admin-order", callback.from_user.id, 20):
                raise DomainError("Слишком много действий. Подождите 10 минут.")
            _, action, raw_id = callback.data.split(":", 2)
            key = uuid.UUID(raw_id)
            await callback.answer()
            answered = True
            if action == "preview":
                approval = await sync_to_async(preview_renewal)(
                    key, actor, f"Ручная проверка перевода администратором Telegram ID {callback.from_user.id}",
                    telegram_user_id=callback.from_user.id,
                )
                order = await sync_to_async(lambda: approval.order)()
                sub = await sync_to_async(lambda: order.subscription)()
                current = timezone.localtime(sub.expires_at).strftime("%d.%m.%Y %H:%M МСК")
                target = timezone.localtime(approval.target_expires_at).strftime("%d.%m.%Y %H:%M МСК")
                await render_screen(
                    callback.message,
                    f"Заказ №{order.number}: {order.plan_title}, {order.price_rub} ₽.\n"
                    f"Подписка №{sub.number}.\nТекущий срок: {current}.\nНовый срок: {target}.\n\n"
                    "Сначала проверьте поступление перевода вручную. Подтверждение ниже продлит только срок: "
                    "квота и блокировки сохраняются. Предпросмотр действует 10 минут.",
                    edit=True,
                    reply_markup=buttons([
                        [("Перевод проверен — продлить", {"callback_data": f"ao:approve:{approval.pk}"})],
                        [("Обновить предпросмотр", {"callback_data": f"ao:preview:{order.pk}"})],
                    ]),
                )
            elif action == "approve":
                await sync_to_async(approve_renewal)(key, actor, telegram_user_id=callback.from_user.id)
                await render_screen(
                    callback.message,
                    "Продление принято. Применение и срок проверит worker; готовность отразится в заказе клиента.",
                    edit=True,
                )
            else:
                raise DomainError("Кнопка устарела. Откройте заказ в админке.")
        except (DomainError, PanelError, ValueError, ValidationError, IntegrityError) as exc:
            text = str(exc) if isinstance(exc, (DomainError, PanelError)) else "Подтверждение некорректно."
            if isinstance(callback.message, Message):
                await callback.message.answer(text)
            if not answered:
                await callback.answer("Действие не выполнено", show_alert=True)

    @dp.message(F.reply_to_message & F.text)
    async def admin_reply(message):
        # Registered only before the catch-all handler; private client replies
        # fall through to the regular support flow through SkipHandler.
        from aiogram.dispatcher.event.bases import SkipHandler

        expected_chat = settings.ADMIN_GROUP_CHAT_ID or settings.ADMIN_CHAT_ID
        if str(message.chat.id) != str(expected_chat):
            raise SkipHandler
        try:
            if not message.from_user or message.from_user.is_bot or message.sender_chat:
                raise DomainError("Отправьте ответ от своего личного Telegram аккаунта.")
            actor = await sync_to_async(telegram_admin)(message.from_user.id, message.chat.id)
            if not await sync_to_async(rate_limit)("admin-reply", message.from_user.id, 20):
                raise DomainError("Слишком много ответов. Подождите 10 минут.")
            notice = await sync_to_async(lambda: Notice.objects.filter(
                admin_event=True, purchase_order__isnull=False,
                telegram_chat_id=str(message.chat.id),
                telegram_message_id=message.reply_to_message.message_id,
            ).first())()
            if not notice:
                raise DomainError("Ответьте на последнее уведомление бота об этом заказе.")
            await sync_to_async(reply_to_order)(
                notice.purchase_order_id, actor, message.text,
                f"telegram:{message.chat.id}:{message.message_id}",
                origin=f"telegram:{message.from_user.id}",
            )
            await message.answer("Сообщение сохранено в заказе клиента и поставлено в личную очередь.")
        except DomainError as exc:
            await message.answer(str(exc))
