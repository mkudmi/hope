"""Durable Telegram purchase requests; only a staff member verifies transfers."""

import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import BotContact, Notice, PurchaseOrder, Request, Subscription, TelegramIdentity, User
from .services import DomainError, audit, prepare_operation


def plans():
    if not settings.TARIFFS_APPROVED:
        return []
    return [
        {"code": code, "title": title, "days": days, "months": months, "price": settings.TARIFF_PRICES[code]}
        for code, title, days, months in [
            ("week", "7 дней", 7, 0),
            ("month", "1 месяц", 0, 1),
            ("quarter", "3 месяца", 0, 3),
        ]
    ]


def offer_key(plan):
    return salted_hmac(
        "purchase-offer", f"{plan['code']}:{plan['title']}:{plan['price']}:{plan['days']}:{plan['months']}"
    ).hexdigest()[:16]


def bot_contact(telegram_id, chat_id, display_name=""):
    if telegram_id != chat_id or telegram_id <= 0:
        raise DomainError("Действие доступно только в личном чате.")
    contact, _ = BotContact.objects.get_or_create(telegram_id=telegram_id, defaults={"chat_id": chat_id})
    contact.display_name, contact.reachable = display_name[:100] or contact.display_name, True
    contact.save(update_fields=["display_name", "reachable"])
    return contact


def contact_notice(contact, key, text):
    return Notice.objects.get_or_create(key=key, defaults={"telegram_contact": contact, "text": text})[0]


def admin_notice(key, text):
    from .services import admin_event

    return admin_event(key, text)


def own_order(order_id, telegram_id):
    try:
        order_id = uuid.UUID(str(order_id))
    except ValueError:
        raise DomainError("Кнопка заказа устарела. Откройте список заказов.") from None
    order = (
        PurchaseOrder.objects.select_related("contact", "user", "operation")
        .filter(pk=order_id, contact__telegram_id=telegram_id)
        .first()
    )
    if not order:
        raise DomainError("Заказ не найден в вашем аккаунте.")
    return order


@transaction.atomic
def place_order(contact, code, signature):
    contact = BotContact.objects.select_for_update().get(pk=contact.pk)
    plan = next((p for p in plans() if p["code"] == code), None)
    if not plan or signature != offer_key(plan):
        raise DomainError("Тариф изменился или недоступен. Откройте актуальные цены.")
    active = (
        contact.orders.select_related("operation")
        .filter(status__in=["awaiting_payment", "payment_review", "paid", "provisioning"])
        .first()
    )
    if active:
        return active
    identity = (
        TelegramIdentity.objects.select_related("user", "user__subscription")
        .filter(telegram_id=contact.telegram_id)
        .first()
    )
    if identity and (not identity.user.is_active or identity.user.is_staff):
        raise DomainError("Покупка недоступна этому аккаунту.")
    user = identity.user if identity else None
    sub = getattr(user, "subscription", None)
    order = PurchaseOrder.objects.create(
        contact=contact,
        user=user,
        subscription=sub,
        kind="renewal" if sub and sub.snapshot else "new",
        plan_code=code,
        plan_title=plan["title"],
        price_rub=plan["price"],
        days=plan["days"],
        months=plan["months"],
    )
    audit(user, "purchase_created", order.number)
    admin_notice(
        f"purchase-new:{order.pk}",
        f"Новый заказ №{order.number}: {order.plan_title}, {order.price_rub} ₽. Проверка вручную: {settings.SITE_URL}/staff/orders/{order.pk}/",
    )
    return order


@transaction.atomic
def report_payment(order_id, telegram_id):
    order = PurchaseOrder.objects.select_for_update().get(pk=own_order(order_id, telegram_id).pk)
    if order.status == "payment_review":
        return order
    if order.status != "awaiting_payment":
        raise DomainError("Заказ уже обработан. Откройте его текущий статус.")
    order.status, order.payment_reported_at = "payment_review", timezone.now()
    order.save()
    audit(order.user, "purchase_payment_reported", order.number)
    admin_notice(
        f"purchase-review:{order.pk}",
        f"Клиент сообщил о переводе по заказу №{order.number}: {order.price_rub} ₽. Это не подтверждение денег. {settings.SITE_URL}/staff/orders/{order.pk}/",
    )
    return order


@transaction.atomic
def cancel_order(order_id, telegram_id):
    order = PurchaseOrder.objects.select_for_update().get(pk=own_order(order_id, telegram_id).pk)
    if order.status == "cancelled":
        return order
    if order.status != "awaiting_payment":
        raise DomainError("После сообщения о переводе отмену нужно согласовать с администратором.")
    admin_notice(
        f"purchase-cancelled:{order.pk}",
        f"Заказ №{order.number} отменён клиентом. {settings.SITE_URL}/staff/orders/",
    )
    order.status = "cancelled"
    order.save()
    audit(order.user, "purchase_cancelled", order.number)
    return order


def staff_guard(actor):
    if not actor.is_staff or not actor.is_active:
        raise DomainError("Действие доступно администратору.")


@transaction.atomic
def review_order(order_id, actor, decision, note):
    staff_guard(actor)
    order = PurchaseOrder.objects.select_for_update().select_related("contact").get(pk=order_id)
    if decision == "confirm" and order.confirmed_at:
        return order
    if order.status not in ("awaiting_payment", "payment_review") or decision not in ("confirm", "reject"):
        raise DomainError("Заказ уже обработан или действие недоступно.")
    if not note.strip():
        raise DomainError("Укажите основание ручной проверки перевода.")
    order.admin_note = note.strip()[:500]
    order.status = "paid" if decision == "confirm" else "rejected"
    if decision == "confirm":
        order.confirmed_by, order.confirmed_at = actor, timezone.now()
    order.save()
    audit(actor, "purchase_payment_" + decision, order.number)
    admin_notice(
        f"purchase-reviewed:{order.pk}:{decision}",
        f"Заказ №{order.number}: {order.status_label}. Решение: {actor.username}. {settings.SITE_URL}/staff/orders/",
    )
    contact_notice(
        order.contact,
        f"purchase-{decision}:{order.pk}",
        f"Заказ №{order.number}: {order.status_label}. "
        + (
            "Администратор подготовит доступ; проверить статус можно в боте."
            if decision == "confirm"
            else order.admin_note
        ),
    )
    return order


@transaction.atomic
def prepare_order(
    order_id, actor, *, version=None, reenable=False, total_bytes=0, limit_devices=0, exact=None
):
    staff_guard(actor)
    order = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("contact", "subscription")
        .get(pk=order_id)
    )
    if order.operation_id:
        return order.operation
    if order.status != "paid" or not order.confirmed_at:
        raise DomainError("Сначала вручную подтвердите поступление перевода.")
    if settings.PANEL_MODE == "real" and not settings.PANEL_WRITES_ENABLED:
        raise DomainError("Запись в действующую панель выключена. Оплата не означает готовность VPN.")
    # Resolve the current identity again; do not claim existing access on behalf of an unlinked buyer.
    identity = (
        TelegramIdentity.objects.select_related("user", "user__subscription")
        .filter(telegram_id=order.contact.telegram_id)
        .first()
    )
    if identity and (not identity.user.is_active or identity.user.is_staff):
        raise DomainError("Аккаунт недоступен. Требуется ручной разбор.")
    if order.kind == "renewal":
        if (
            not identity
            or identity.user_id != order.user_id
            or getattr(identity.user, "subscription", None) != order.subscription
        ):
            raise DomainError("Привязка изменилась. Требуется ручная проверка владельца.")
        sub = Subscription.objects.select_for_update().get(pk=order.subscription_id)
        if version != sub.version:
            raise DomainError("Подписка изменилась. Повторите предпросмотр.")
    else:
        if identity:
            user = identity.user
            sub = getattr(user, "subscription", None)
            if sub and sub.snapshot:
                raise DomainError("У клиента уже появился доступ. Создайте отдельный заказ продления.")
        else:
            user = User(username="tg_" + uuid.uuid4().hex[:20])
            user.set_unusable_password()
            user.save()
            TelegramIdentity.objects.create(
                user=user,
                telegram_id=order.contact.telegram_id,
                chat_id=order.contact.chat_id,
                display_name=order.contact.display_name,
            )
            sub = None
        if not sub:
            sub = Subscription.objects.create(
                owner=user,
                label=order.contact.display_name or "Клиент Telegram",
                panel_email="hope-" + uuid.uuid4().hex[:16],
            )
        order.user, order.subscription = user, sub
    op = prepare_operation(
        sub.pk,
        actor,
        kind="renew" if order.kind == "renewal" else "create",
        reason=f"Ручная выдача по оплаченному заказу №{order.number}",
        version=sub.version,
        days=order.days,
        months=order.months,
        exact=exact if order.kind == "renewal" else None,
        reenable=reenable,
        total_bytes=total_bytes,
        limit_devices=limit_devices,
        operation_id=order.pk,
    )
    order.operation, order.status = op, "provisioning"
    order.save()
    audit(actor, "purchase_provisioning", order.number)
    return op


@transaction.atomic
def sync_order_results():
    for order in (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("operation", "contact")
        .filter(status="provisioning", operation__status="verified")[:10]
    ):
        order.status = "fulfilled"
        order.save()
        contact_notice(
            order.contact,
            f"purchase-ready:{order.pk}",
            f"Заказ №{order.number}: доступ готов. Откройте «Подключить VPN» в меню бота. Срок и персональная ссылка доступны в «Моя подписка».",
        )


@transaction.atomic
def submit_bot_support(contact, text):
    contact = BotContact.objects.select_for_update().get(pk=contact.pk)
    if not contact.support_pending:
        return None
    text = text.strip()
    if not text or len(text) > 2000:
        raise DomainError("Напишите сообщение длиной до 2000 символов без персональных VPN-ссылок.")
    identity = TelegramIdentity.objects.filter(telegram_id=contact.telegram_id, user__is_active=True).first()
    item = Request.objects.create(
        user=identity.user if identity else None,
        telegram_contact=contact,
        kind="support",
        text=text,
        contact="Telegram",
    )
    contact.support_pending = False
    contact.save(update_fields=["support_pending"])
    admin_notice(
        f"bot-support:{item.pk}",
        f"Новое обращение из бота. Ответьте в кабинете: {settings.SITE_URL}/staff/requests/",
    )
    return item


@transaction.atomic
def request_existing_subscription(contact, *, username="", first_name="", last_name=""):
    """Request manual identity verification; never claim a panel subscription."""
    contact = BotContact.objects.select_for_update().get(pk=contact.pk)
    if TelegramIdentity.objects.filter(telegram_id=contact.telegram_id, user__is_active=True).exists():
        return None
    contact.support_pending = False
    contact.save(update_fields=["support_pending"])
    pending = Request.objects.filter(telegram_contact=contact, kind="binding", status="open").first()
    if pending:
        return pending
    profile = (
        f"Telegram ID: {contact.telegram_id}\n"
        f"Логин Telegram: {'@' + username if username else 'не указан'}\n"
        f"Имя: {first_name or 'не указано'}\n"
        f"Фамилия: {last_name or 'не указана'}\n"
        f"Контакт: tg://user?id={contact.telegram_id}"
    )
    item = Request.objects.create(
        telegram_contact=contact,
        kind="binding",
        contact="Telegram",
        text="Заявка на привязку существующей подписки.\n" + profile,
    )
    admin_notice(
        f"existing-subscription:{item.pk}",
        "Заявка на привязку существующей подписки.\n"
        + profile
        + "\nПроверьте владельца по прежнему контакту, затем выдайте приглашение к нужной подписке."
        + f"\nОбращения: {settings.SITE_URL}/staff/requests/",
    )
    return item
