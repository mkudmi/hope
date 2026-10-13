"""Private-chat Telegram interface. All business rules stay in services.py."""

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from asgiref.sync import sync_to_async
from django.conf import settings

from .clients import PLATFORMS, client_info
from .models import PurchaseOrder, Request, TelegramIdentity
from .orders import (
    bot_contact,
    cancel_order,
    offer_key,
    own_order,
    place_order,
    plans,
    report_payment,
    request_existing_subscription,
    submit_bot_support,
)
from .services import (
    DomainError,
    audit,
    confirm_bot,
    issue_token,
    rate_limit,
    subscription_url,
    telegram_candidate,
)


def buttons(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, **kw) for text, kw in row] for row in rows]
    )


def menu():
    return buttons(
        [
            [
                ("💳 Тарифы и цены", {"callback_data": "prices"}),
                ("🚀 Подключить VPN", {"callback_data": "connect"}),
            ],
            [
                ("🔐 Моя подписка", {"callback_data": "status"}),
                ("🔄 Продлить подписку", {"callback_data": "renew"}),
            ],
            [("📦 Мои заказы", {"callback_data": "orders"}), ("💬 Помощь", {"callback_data": "help"})],
            *(
                [[("🌍 Открыть сайт ↗", {"url": settings.SITE_URL + "/login/"})]]
                if settings.SITE_URL.startswith("https://")
                else []
            ),
        ]
    )


def home_screen(identity):
    if identity:
        return "👋 С возвращением! Здесь ваша подписка и всё для подключения к HOPE VPN.", menu()
    return (
        "👋 Добро пожаловать в HOPE VPN!\n\nПодключиться просто: выберите тариф и срок, оформите заказ и следуйте подсказкам бота.\n\n✨ Уже пользуетесь VPN? Напишите администратору, чтобы получить персональное приглашение и подключить Telegram к своему аккаунту.",
        buttons(
            [
                [("💳 Тарифы и цены", {"callback_data": "prices"})],
                [("🚀 Хочу подключиться", {"callback_data": "new"})],
                [("🔗 У меня уже есть подписка", {"callback_data": "existing"})],
                [("📦 Мои заказы", {"callback_data": "orders"}), ("💬 Помощь", {"callback_data": "help"})],
                [("🌍 Открыть сайт ↗", {"url": settings.SITE_URL + "/login/"})],
            ]
        ),
    )


def price_buttons():
    return buttons(
        [
            [(f"{plan['title']} · {plan['price']} ₽", {"callback_data": f"plan:{plan['code']}"})]
            for plan in plans()
        ]
        + [[("← Главное меню", {"callback_data": "menu"})]]
    )


def order_text(order):
    text = f"Заказ №{order.number}\n{order.plan_title} — {order.price_rub} ₽\n{order.status_label}."
    if order.status == "awaiting_payment":
        text += "\n\nРеквизиты администратор пришлёт лично. Дождитесь их и переведите точную сумму, указав номер заказа. После перевода нажмите «Я оплатил»."
        if settings.PANEL_MODE == "real" and not settings.PANEL_WRITES_ENABLED:
            text += (
                "\nВыдача через приложение пока приостановлена. Дождитесь ответа администратора до перевода."
            )
    elif order.status == "payment_review":
        text += "\n\nАдминистратор проверит поступление денег вручную. Повторно переводить не нужно. Кнопка «Я оплатил» не подтверждает поступление денег."
    elif order.status == "paid":
        text += "\n\nПеревод подтверждён владельцем. Вы получите сообщение, когда доступ будет готов."
    elif order.status == "provisioning":
        text += "\n\nИзменение сохранено, готовность проверяется."
        if order.operation and order.operation.status in ("failed", "partial", "conflict"):
            text += " Требуется проверка администратора; повторно оплачивать не нужно."
    elif order.status == "fulfilled":
        text += "\n\nОткройте «Подключить VPN»."
    elif order.status == "rejected":
        text += "\nСвяжитесь с администратором для уточнения."
    return text


def order_buttons(order):
    rows = []
    if order.status == "awaiting_payment":
        rows += [
            [("Я оплатил", {"callback_data": f"paid:{order.pk}"})],
            [("Отменить заказ", {"callback_data": f"cancel-order:{order.pk}"})],
        ]
    rows += [
        [("Обновить статус", {"callback_data": f"order:{order.pk}"})],
        [("Написать администратору", {"callback_data": "support"})],
    ]
    if order.status == "fulfilled":
        rows += [[("Подключить VPN", {"callback_data": "connect"})]]
    return buttons(rows + [[("← Главное меню", {"callback_data": "menu"})]])


def identity_for(telegram_id):
    identity = (
        TelegramIdentity.objects.select_related("user", "user__subscription")
        .filter(telegram_id=telegram_id, user__is_active=True)
        .first()
    )
    if identity and not identity.reachable:
        identity.reachable = True
        identity.save()
    return identity


async def render_screen(message: Message, text, *, edit=False, **kwargs):
    """Replace a callback screen; send only on an explicit uneditable-message error."""
    kwargs.setdefault("disable_web_page_preview", True)
    if not edit:
        return await message.answer(text, **kwargs)
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        reason = exc.message.lower()
        if "message is not modified" in reason:
            return None
        if "message to edit not found" not in reason and "message can't be edited" not in reason:
            raise
        return await message.answer(text, **kwargs)


def build_dispatcher():
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message):
        if message.chat.type != "private" or not message.from_user:
            return
        if not await sync_to_async(rate_limit)("bot-start", message.from_user.id, 10):
            await message.answer("Слишком много попыток. Подождите 10 минут.")
            return
        parts = (message.text or "").split(maxsplit=1)
        display = (
            "@" + message.from_user.username + " · " if message.from_user.username else ""
        ) + message.from_user.full_name
        contact = await sync_to_async(bot_contact)(message.from_user.id, message.chat.id, display)
        await sync_to_async(type(contact).objects.filter(pk=contact.pk).update)(support_pending=False)
        if len(parts) == 2:
            try:
                name = (
                    "@" + message.from_user.username
                    if message.from_user.username
                    else message.from_user.full_name
                )
                token = await sync_to_async(telegram_candidate)(
                    parts[1], message.from_user.id, message.chat.id, name
                )
                await message.answer(
                    "Подтвердите подключение этого Telegram к вашему аккаунту HOPE. Затем завершите привязку в исходном браузере.",
                    reply_markup=buttons([[("Это мой аккаунт", {"callback_data": "bind:" + str(token.pk)})]]),
                )
            except DomainError as exc:
                await message.answer(str(exc))
            return
        identity = await sync_to_async(identity_for)(message.from_user.id)
        text, markup = home_screen(identity)
        await render_screen(message, text, reply_markup=markup)

    @dp.callback_query(F.data.startswith("bind:"))
    async def bind(callback: CallbackQuery):
        if (
            not isinstance(callback.message, Message)
            or callback.message.date.timestamp() == 0
            or callback.message.chat.type != "private"
        ):
            await callback.answer("Откройте личный чат.", show_alert=True)
            return
        try:
            await sync_to_async(confirm_bot)(callback.data.removeprefix("bind:"), callback.from_user.id)
            await callback.answer("Подтверждено")
            await render_screen(
                callback.message,
                "Теперь вернитесь в кабинет, обновите страницу и завершите привязку. До этого аккаунты ещё не связаны.",
                edit=True,
            )
        except (DomainError, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)

    async def action(message: Message, user_id, name, profile=None, *, edit=False):
        async def show(text, **kwargs):
            return await render_screen(message, text, edit=edit, **kwargs)

        identity = await sync_to_async(identity_for)(user_id)
        if name == "menu":
            text, markup = home_screen(identity)
            await show(text, reply_markup=markup)
            return
        if name in ("prices", "new", "renew"):
            available = plans()
            if not available:
                await show(
                    "Тарифы пока уточняются. Напишите администратору.",
                    reply_markup=buttons([[("Написать администратору", {"callback_data": "support"})]]),
                )
                return
            if name == "renew" and not identity:
                await show(
                    "Для продления старой подписки сначала подключите Telegram к вашему аккаунту. Не оформляйте новую вместо существующей.",
                    reply_markup=buttons([[("У меня уже есть подписка", {"callback_data": "existing"})]]),
                )
                return
            await show(
                "Тарифы HOPE VPN\n\n"
                + "\n".join(f"{p['title']} — {p['price']} ₽" for p in available)
                + "\n\nЦена за весь выбранный срок. Месяцы календарные. Оплату вручную подтверждает администратор. Выберите срок:",
                reply_markup=price_buttons(),
            )
            return
        if name.startswith("plan:"):
            plan = next((p for p in plans() if p["code"] == name.removeprefix("plan:")), None)
            if not plan:
                raise DomainError("Тариф недоступен. Откройте актуальные цены.")
            await show(
                f"Вы выбрали: {plan['title']}\nК оплате: {plan['price']} ₽.\n\nПосле оформления администратор лично пришлёт реквизиты. Доступ выдаётся после ручной проверки перевода.",
                reply_markup=buttons(
                    [
                        [("Оформить заказ", {"callback_data": f"buy:{plan['code']}:{offer_key(plan)}"})],
                        [("← Другой срок", {"callback_data": "prices"})],
                    ]
                ),
            )
            return
        if name.startswith("buy:"):
            _, code, signature = name.split(":", 2)
            contact = await sync_to_async(bot_contact)(user_id, message.chat.id)
            order = await sync_to_async(place_order)(contact, code, signature)
            await show(order_text(order), reply_markup=order_buttons(order))
            return
        if name in ("orders",) or name.startswith(("order:", "paid:", "cancel-order:")):
            if name == "orders":
                orders = await sync_to_async(list)(
                    PurchaseOrder.objects.filter(contact__telegram_id=user_id).select_related("operation")[:5]
                )
                if not orders:
                    await show("Заказов пока нет. Выберите срок подключения.", reply_markup=price_buttons())
                    return
                await show(
                    "Ваши последние заказы:",
                    reply_markup=buttons(
                        [
                            [(f"№{o.number} · {o.status_label}", {"callback_data": f"order:{o.pk}"})]
                            for o in orders
                        ]
                        + [[("← Главное меню", {"callback_data": "menu"})]]
                    ),
                )
                return
            action_name, key = name.split(":", 1)
            order = await sync_to_async(
                report_payment
                if action_name == "paid"
                else cancel_order
                if action_name == "cancel-order"
                else own_order
            )(key, user_id)
            await show(order_text(order), reply_markup=order_buttons(order))
            return
        if name == "existing":
            if identity:
                await show("Ваша подписка уже связана с Telegram.", reply_markup=menu())
                return
            contact = await sync_to_async(bot_contact)(user_id, message.chat.id)
            item = await sync_to_async(request_existing_subscription)(
                contact,
                username=profile.username or "" if profile else "",
                first_name=profile.first_name or "" if profile else "",
                last_name=profile.last_name or "" if profile else "",
            )
            await show(
                "Заявка на привязку существующей подписки передана администратору. "
                "Ожидайте ответа здесь — писать дополнительные сообщения не нужно. "
                "Администратор проверит вашу подписку и пришлёт дальнейшие инструкции."
                if item
                else "Ваша подписка уже связана с Telegram.",
                reply_markup=buttons(
                    [
                        [("Проверить привязку", {"callback_data": "existing"})],
                        [("← Главное меню", {"callback_data": "menu"})],
                    ]
                )
                if item
                else menu(),
            )
            return
        if name in ("support", "support-cancel"):

            def support_mode():
                contact = bot_contact(user_id, message.chat.id)
                contact.support_pending = name == "support"
                contact.save(update_fields=["support_pending"])

            await sync_to_async(support_mode)()
            await show(
                "Напишите следующим сообщением, чем помочь. Не отправляйте пароли, персональные VPN-ссылки и QR."
                if name == "support"
                else "Ввод обращения отменён.",
                reply_markup=buttons([[("Отменить ввод", {"callback_data": "support-cancel"})]])
                if name == "support"
                else menu(),
            )
            return
        if name == "help":
            await show(
                "Подключение: выберите тариф → оформите заказ → получите реквизиты лично от администратора → сообщите о переводе → дождитесь готовности → добавьте подписку в приложение.\n\nДля установки нажмите «Подключить VPN». Если VPN не работает, проверьте срок и обновите подписку в приложении. С вопросом можно обратиться здесь, без браузера.",
                reply_markup=buttons(
                    [
                        [("Подключить VPN", {"callback_data": "connect"})],
                        [("Написать администратору", {"callback_data": "support"})],
                        [("← Главное меню", {"callback_data": "menu"})],
                    ]
                ),
            )
            return
        if name in ("portal", "web-setup"):
            if identity and not identity.user.has_usable_password():

                def onboard():
                    from django.utils import timezone

                    from .models import Token

                    Token.objects.filter(user=identity.user, kind="onboard", consumed_at__isnull=True).update(
                        consumed_at=timezone.now()
                    )
                    raw, _ = issue_token("onboard", user=identity.user, minutes=15)
                    return raw

                raw = await sync_to_async(onboard)()
                await show(
                    "Настройте собственные логин и пароль для входа без Telegram. Ссылка действует 15 минут; никому её не передавайте.\n"
                    + settings.SITE_URL
                    + "/setup-account/?token="
                    + raw,
                    reply_markup=buttons([[("← Главное меню", {"callback_data": "menu"})]]),
                )
            else:
                await show(
                    "Кабинет доступен по вашему логину и паролю, без Telegram:\n"
                    + settings.SITE_URL
                    + "/login/",
                    reply_markup=buttons([[("← Главное меню", {"callback_data": "menu"})]]),
                )
            return
        if not identity:
            await show(
                "Доступ ещё не связан с этим Telegram. Новый клиент может оформить заказ; для существующего доступа нужна привязка.",
                reply_markup=buttons(
                    [
                        [("Выбрать тариф", {"callback_data": "prices"})],
                        [("Существующая подписка", {"callback_data": "existing"})],
                        [("Мои заказы", {"callback_data": "orders"})],
                    ]
                ),
            )
            return
        sub = getattr(identity.user, "subscription", None)
        if not sub:
            await show("Подписка ещё не назначена. Обратитесь к администратору.", reply_markup=menu())
            return
        if name == "status":
            from django.utils import timezone

            text = f"Подписка №{sub.number}\n{sub.status_label}"
            if sub.expires_at:
                text += "\nДо " + timezone.localtime(sub.expires_at).strftime("%d.%m.%Y %H:%M МСК")
            if sub.sync_status not in ("verified", "unmanaged"):
                text += "\nПрименение требует проверки: " + sub.sync_status
            await show(text, reply_markup=menu())
        elif name == "connect" or name.startswith(("device:", "client:")):
            url = subscription_url(sub)
            if not url:
                await show(
                    "Доступ ещё не выдан или требует проверки. Откройте статус заказа либо напишите администратору.",
                    reply_markup=menu(),
                )
                return
            if name == "connect":
                await show(
                    "Выберите устройство:",
                    reply_markup=buttons(
                        [
                            [
                                ("Android", {"callback_data": "device:android"}),
                                ("iPhone / iPad", {"callback_data": "device:ios"}),
                            ],
                            [
                                ("Windows", {"callback_data": "device:windows"}),
                                ("macOS", {"callback_data": "device:macos"}),
                            ],
                            [("← Главное меню", {"callback_data": "menu"})],
                        ]
                    ),
                )
            elif name.startswith("device:"):
                platform = name.removeprefix("device:")
                if platform not in PLATFORMS:
                    raise DomainError("Выберите устройство из меню.")
                text = (
                    "Для iPhone/iPad с российским App Store используйте INCY. Если Happ уже установлен, можно оставить его. Подписка та же."
                    if platform == "ios"
                    else "Выберите приложение. Одна и та же персональная ссылка используется в обоих клиентах."
                )
                applications = ["incy", "happ"] if platform == "ios" else ["happ", "incy"]
                await show(
                    text,
                    reply_markup=buttons(
                        [
                            [
                                (
                                    client_info(app, platform)["name"],
                                    {"callback_data": f"client:{app}:{platform}"},
                                )
                            ]
                            for app in applications
                        ]
                        + [[("← Устройства", {"callback_data": "connect"})]]
                    ),
                )
            else:
                _, app, platform = name.split(":", 2)
                info = client_info(app, platform)
                if not info:
                    raise DomainError("Выберите приложение из меню.")
                await show(
                    f"{info['platform']} · {info['name']}\n\n1. Установите приложение по кнопке ниже.\n2. Скопируйте персональную ссылку. Добавьте подписку в приложении из буфера и обновите список серверов.\n3. Выберите профиль и включите подключение. Если система запросит разрешение VPN — подтвердите его.\n\nВаша персональная ссылка (не пересылайте её):\n"
                    + url
                    + "\n\nЕсли импорт из Telegram не сработал, вручную вставьте эту же ссылку в приложении.",
                    disable_web_page_preview=True,
                    reply_markup=buttons(
                        [
                            [(f"Скачать {info['name']}", {"url": info["download"]})],
                            *(
                                [[("Скопировать ссылку", {"copy_text": CopyTextButton(text=url)})]]
                                if len(url) <= 256
                                else []
                            ),
                            [
                                ("Моя подписка", {"callback_data": "status"}),
                                ("Помощь", {"callback_data": "help"}),
                            ],
                            [("← Выбрать приложение", {"callback_data": f"device:{platform}"})],
                        ]
                    ),
                )

        else:
            await show("Эта кнопка устарела. Откройте главное меню.", reply_markup=menu())

    @dp.callback_query()
    async def callback(callback: CallbackQuery):
        if (
            not isinstance(callback.message, Message)
            or callback.message.date.timestamp() == 0
            or callback.message.chat.type != "private"
        ):
            await callback.answer("Откройте личный чат.", show_alert=True)
            return
        await callback.answer()
        if await sync_to_async(rate_limit)("bot-action", callback.from_user.id, 30):
            try:
                display = (
                    "@" + callback.from_user.username + " · " if callback.from_user.username else ""
                ) + callback.from_user.full_name
                await sync_to_async(bot_contact)(callback.from_user.id, callback.message.chat.id, display)
                await action(
                    callback.message,
                    callback.from_user.id,
                    callback.data or "",
                    callback.from_user,
                    edit=True,
                )
            except (DomainError, ValueError) as exc:
                await render_screen(
                    callback.message,
                    str(exc) if isinstance(exc, DomainError) else "Кнопка устарела. Откройте меню.",
                    edit=True,
                    reply_markup=menu(),
                )

    @dp.message(Command("recover"))
    async def recover(message: Message):
        if message.chat.type != "private" or not message.from_user:
            return
        if not await sync_to_async(rate_limit)("bot-recover", message.from_user.id, 3):
            await message.answer("Подождите 10 минут перед повторной попыткой.")
            return
        identity = await sync_to_async(identity_for)(message.from_user.id)
        if not identity:
            await message.answer(
                "Привязанный аккаунт не найден. Используйте код восстановления в кабинете или поддержку."
            )
            return

        if not identity.user.has_usable_password():
            await action(message, message.from_user.id, "portal")
            return

        def issue():
            from django.utils import timezone

            from .models import Token

            Token.objects.filter(user=identity.user, kind="reset", consumed_at__isnull=True).update(
                consumed_at=timezone.now()
            )
            raw, _token = issue_token("reset", user=identity.user, minutes=10)
            audit(identity.user, "telegram_recovery_requested")
            return raw

        raw = await sync_to_async(issue)()
        await message.answer(
            "Вы запросили смену пароля. Ссылка действует 10 минут. Никому её не передавайте.\nЛогин: "
            + identity.user.username
            + "\n"
            + settings.SITE_URL
            + "/recover/?token="
            + raw
        )

    @dp.message(Command("help", "support", "account", "status", "prices", "buy", "orders", "renew"))
    async def commands(message: Message):
        if message.chat.type == "private" and message.from_user:
            if not await sync_to_async(rate_limit)("bot-action", message.from_user.id, 30):
                await message.answer("Слишком много запросов. Подождите 10 минут.")
                return
            command = (message.text or "").split()[0].split("@")[0].lstrip("/")
            name = {"account": "portal", "buy": "new"}.get(command, command)
            await action(message, message.from_user.id, name)

    @dp.message(F.text)
    async def text_message(message: Message):
        if message.chat.type != "private" or not message.from_user:
            return
        if not await sync_to_async(rate_limit)("bot-message", message.from_user.id, 20):
            await message.answer("Слишком много сообщений. Подождите 10 минут.")
            return
        contact = await sync_to_async(bot_contact)(
            message.from_user.id, message.chat.id, message.from_user.full_name
        )
        try:
            item = await sync_to_async(submit_bot_support)(contact, message.text)
            await message.answer(
                "Обращение сохранено. Ответ администратора придёт сюда."
                if item
                else (
                    "Заявка на привязку уже передана администратору. Ожидайте ответа здесь."
                    if await sync_to_async(
                        Request.objects.filter(telegram_contact=contact, kind="binding", status="open").exists
                    )()
                    else "Выберите действие в меню. Для вопроса нажмите «Помощь» → «Написать администратору»."
                ),
                reply_markup=menu(),
            )
        except DomainError as exc:
            await message.answer(str(exc))

    @dp.message()
    async def unsupported_message(message: Message):
        if (
            message.chat.type == "private"
            and message.from_user
            and await sync_to_async(rate_limit)("bot-message", message.from_user.id, 20)
        ):
            await message.answer(
                "Напишите вопрос текстом. Чтобы сообщить о переводе, откройте заказ и нажмите «Я оплатил». Реквизиты администратор присылает лично.",
                reply_markup=menu(),
            )

    return dp


async def run_bot():
    if not settings.BOT_TOKEN:
        raise DomainError("Добавьте BOT_TOKEN в .env. Без него веб-кабинет и worker доступны отдельно.")
    bot = Bot(settings.BOT_TOKEN)
    try:
        me = await bot.get_me()
        if (me.username or "").lower() != settings.BOT_USERNAME.lower():
            raise DomainError("BOT_USERNAME не совпадает с именем бота по токену. Исправьте .env.")
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise DomainError(
                "Для бота уже настроен webhook. Его нужно отдельно отключить перед запуском long polling."
            )
        await bot.set_my_commands(
            [
                BotCommand(command=name, description=text)
                for name, text in [
                    ("start", "Открыть HOPE VPN"),
                    ("prices", "Тарифы и цены"),
                    ("buy", "Новое подключение"),
                    ("renew", "Продлить подписку"),
                    ("orders", "Заказы и проверка оплаты"),
                    ("status", "Состояние подписки"),
                    ("support", "Написать администратору"),
                    ("account", "Личный кабинет"),
                    ("help", "Помощь с подключением"),
                    ("recover", "Восстановить пароль кабинета"),
                ]
            ]
        )
        await build_dispatcher().start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
