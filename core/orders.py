"""Durable Telegram purchase requests; only a staff member verifies transfers."""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .crypto import digest
from .models import (
    BotContact,
    Notice,
    OrderApproval,
    OrderMessage,
    PurchaseOrder,
    Request,
    Subscription,
    TelegramIdentity,
    User,
)
from .services import DomainError, add_duration, audit, notify, prepare_operation

ACTIVE_ORDER_STATES = ["awaiting_payment", "payment_review", "paid", "provisioning"]


def plans(*, include_long=False):
    if not settings.TARIFFS_APPROVED:
        return []
    return [
        {"code": code, "title": title, "days": days, "months": months, "price": settings.TARIFF_PRICES[code]}
        for code, title, days, months in [
            ("week", "7 дней", 7, 0),
            ("month", "1 месяц", 0, 1),
            ("quarter", "3 месяца", 0, 3),
        ] + ([("halfyear", "6 месяцев", 0, 6), ("year", "12 месяцев", 0, 12)] if include_long else [])
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


def contact_notice(contact, key, text, **kwargs):
    return Notice.objects.get_or_create(
        key=key, defaults={"telegram_contact": contact, "text": text, **kwargs}
    )[0]


def order_notice(order, key, text):
    if order.source == "web":
        return notify(order.user, key, text, purchase_order=order)
    return contact_notice(order.contact, key, text, purchase_order=order)


def admin_notice(key, text, *, telegram_entities=None, purchase_order=None):
    from .services import admin_event

    return admin_event(key, text, telegram_entities=telegram_entities, purchase_order=purchase_order)


def own_order(order_id, telegram_id):
    try:
        order_id = uuid.UUID(str(order_id))
    except ValueError:
        raise DomainError("Кнопка заказа устарела. Откройте список заказов.") from None
    order = (
        PurchaseOrder.objects.select_related("contact", "user", "operation")
        .filter(
            Q(contact__telegram_id=telegram_id)
            | Q(user__telegram__telegram_id=telegram_id, user__is_active=True), pk=order_id,
        )
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
    if user:
        User.objects.select_for_update().get(pk=user.pk)
        active = PurchaseOrder.objects.filter(user=user, status__in=ACTIVE_ORDER_STATES).first()
        if active:
            return active
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
        purchase_order=order,
    )
    return order


@transaction.atomic
def place_web_order(user, code, signature):
    user = User.objects.select_for_update().get(pk=user.pk)
    if not user.is_active or user.is_staff:
        raise DomainError("Продление доступно владельцу клиентского аккаунта.")
    plan = next((p for p in plans(include_long=True) if p["code"] == code), None)
    if not plan or signature != offer_key(plan):
        raise DomainError("Тариф изменился или недоступен. Выберите срок заново.")
    sub = getattr(user, "subscription", None)
    if not sub or not sub.snapshot or sub.excluded:
        raise DomainError("Для продления нужна ваша действующая запись подписки. Обратитесь в поддержку.")
    active = PurchaseOrder.objects.filter(user=user, status__in=ACTIVE_ORDER_STATES).first()
    if active:
        return active
    order = PurchaseOrder.objects.create(
        source="web", user=user, subscription=sub, kind="renewal",
        plan_code=code, plan_title=plan["title"], price_rub=plan["price"],
        days=plan["days"], months=plan["months"],
    )
    audit(user, "purchase_created", order.number)
    admin_notice(
        f"purchase-new:{order.pk}",
        f"Заказ продления №{order.number} из кабинета {user.username}. Подписка №{sub.number}.\n"
        f"{order.plan_title} · {order.price_rub} ₽. Ответьте на это сообщение, чтобы отправить клиенту текст.\n"
        f"{settings.SITE_URL}/staff/orders/{order.pk}/",
        purchase_order=order,
    )
    return order


def own_web_order(order_id, user):
    order = PurchaseOrder.objects.select_related("subscription", "operation").filter(pk=order_id, user=user).first()
    if not order or not user.is_active or user.is_staff:
        raise DomainError("Заказ не найден в вашем аккаунте.")
    return order


@transaction.atomic
def report_web_payment(order_id, user):
    return _report_payment(own_web_order(order_id, user).pk)


@transaction.atomic
def report_payment(order_id, telegram_id):
    return _report_payment(own_order(order_id, telegram_id).pk)


def _report_payment(order_id):
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
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
        purchase_order=order,
    )
    return order


@transaction.atomic
def cancel_order(order_id, telegram_id):
    return _cancel_order(own_order(order_id, telegram_id).pk)


@transaction.atomic
def cancel_web_order(order_id, user):
    return _cancel_order(own_web_order(order_id, user).pk)


def _cancel_order(order_id):
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    if order.status == "cancelled":
        return order
    if order.status != "awaiting_payment":
        raise DomainError("После сообщения о переводе отмену нужно согласовать с администратором.")
    admin_notice(
        f"purchase-cancelled:{order.pk}",
        f"Заказ №{order.number} отменён клиентом. {settings.SITE_URL}/staff/orders/",
        purchase_order=order,
    )
    order.status = "cancelled"
    order.save()
    audit(order.user, "purchase_cancelled", order.number)
    return order


def staff_guard(actor):
    if not actor.is_staff or not actor.is_active:
        raise DomainError("Действие доступно администратору.")


def order_terms_digest(order):
    return digest(
        f"{order.pk}:{order.user_id}:{order.subscription_id}:{order.contact_id}:{order.source}:"
        f"{order.plan_code}:{order.plan_title}:{order.price_rub}:{order.days}:{order.months}"
    )


@transaction.atomic
def reply_to_order(order_id, actor, text, key, *, origin="web"):
    staff_guard(actor)
    text = text.strip()
    if not text or len(text) > 2000:
        raise DomainError("Введите текст длиной до 2000 символов.")
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    message, created = OrderMessage.objects.get_or_create(
        key=f"order-reply:{order.pk}:{key}",
        defaults={"order": order, "actor": actor, "text": text},
    )
    if created:
        order_notice(order, message.key, f"Заказ №{order.number}. Сообщение администратора:\n" + text)
        audit(actor, "purchase_reply", f"{order.number}:{origin}")
    return message


@transaction.atomic
def preview_renewal(order_id, actor, note, *, telegram_user_id=None, reenable=False):
    staff_guard(actor)
    order = PurchaseOrder.objects.select_for_update().get(pk=order_id)
    if order.kind != "renewal" or order.status not in ("awaiting_payment", "payment_review", "paid"):
        raise DomainError("Продление уже обрабатывается или заказ недоступен.")
    if not note.strip() or len(note) > 500:
        raise DomainError("Укажите основание ручной проверки длиной до 500 символов.")
    sub = Subscription.objects.select_for_update().get(pk=order.subscription_id)
    if not sub.expires_at or sub.expiry_kind != "fixed":
        raise DomainError("Срок бессрочный или не определён. Требуется отдельное решение в карточке доступа.")
    if sub.owner_id != order.user_id or not sub.managed or sub.excluded or sub.sync_status == "conflict":
        raise DomainError("Проверьте владельца и состояние подписки в админке.")
    return OrderApproval.objects.create(
        order=order, actor=actor, telegram_user_id=telegram_user_id,
        order_status=order.status, terms_digest=order_terms_digest(order), subscription_version=sub.version,
        target_expires_at=add_duration(max(timezone.now(), sub.expires_at), order.days, order.months),
        note=note.strip(), reenable=reenable, expires_at=timezone.now() + timedelta(minutes=10),
    )


@transaction.atomic
def approve_renewal(approval_id, actor, *, telegram_user_id=None):
    staff_guard(actor)
    approval = OrderApproval.objects.select_for_update().filter(
        pk=approval_id, actor=actor, telegram_user_id=telegram_user_id,
    ).first()
    if not approval:
        raise DomainError("Неверное подтверждение или другой администратор/канал.")
    order = PurchaseOrder.objects.select_for_update().get(pk=approval.order_id)
    if approval.consumed_at:
        if order.operation_id:
            return order.operation
        raise DomainError("Подтверждение уже использовано.")
    if (
        approval.expires_at <= timezone.now() or order.status != approval.order_status
        or order_terms_digest(order) != approval.terms_digest
    ):
        raise DomainError("Предпросмотр устарел. Проверьте заказ заново.")
    if order.status != "paid":
        review_order(order.pk, actor, "confirm", approval.note)
    operation = prepare_order(
        order.pk, actor, version=approval.subscription_version,
        exact=approval.target_expires_at, reenable=approval.reenable,
    )
    approval.consumed_at = timezone.now()
    approval.save(update_fields=["consumed_at"])
    audit(actor, "purchase_approved", f"{order.number}:telegram={telegram_user_id or 'web'}")
    return operation


@transaction.atomic
def review_order(order_id, actor, decision, note):
    staff_guard(actor)
    order = PurchaseOrder.objects.select_for_update(of=("self",)).select_related("contact").get(pk=order_id)
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
        purchase_order=order,
    )
    order_notice(
        order,
        f"purchase-{decision}:{order.pk}",
        f"Заказ №{order.number}: {order.status_label}. "
        + (
            "Администратор подготовит доступ; проверить статус можно в заказе."
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
        .filter(telegram_id=order.contact.telegram_id if order.contact else None)
        .first()
    )
    if identity and (not identity.user.is_active or identity.user.is_staff):
        raise DomainError("Аккаунт недоступен. Требуется ручной разбор.")
    if order.kind == "renewal":
        owner = order.user if order.source == "web" else (identity.user if identity else None)
        if not owner or not owner.is_active or owner.is_staff or (
            owner.pk != order.user_id or getattr(owner, "subscription", None) != order.subscription
        ):
            raise DomainError("Привязка изменилась. Требуется ручная проверка владельца.")
        sub = Subscription.objects.select_for_update().get(pk=order.subscription_id)
        if sub.owner_id != order.user_id:
            raise DomainError("Владелец подписки изменился. Требуется ручная проверка.")
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
        order_notice(
            order,
            f"purchase-ready:{order.pk}",
            f"Заказ №{order.number}: изменение доступа подтверждено. Срок и подключение доступны в вашем кабинете или меню бота.",
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
        f"Фамилия: {last_name or 'не указана'}"
    )
    intro = "Заявка на привязку существующей подписки.\n" + profile
    contact_url = f"https://t.me/{username}" if username else f"tg://user?id={contact.telegram_id}"
    browser_contact = contact_url if username else "без username; ответьте через обращение в кабинете"
    item = Request.objects.create(
        telegram_contact=contact,
        kind="binding",
        contact="Telegram",
        text=intro + "\nКонтакт: " + browser_contact,
    )
    contact_prefix = intro + "\nКонтакт: "
    contact_label = "Открыть профиль"
    requests_url = f"{settings.SITE_URL}/staff/requests/"
    notice_text = (
        contact_prefix
        + contact_label
        + "\nПроверьте владельца по прежнему контакту, затем выдайте приглашение к нужной подписке."
        + f"\nОбращения: {requests_url}"
    )
    admin_notice(
        f"existing-subscription:{item.pk}",
        notice_text,
        telegram_entities=[
            {
                "type": "text_link",
                "offset": len(contact_prefix.encode("utf-16-le")) // 2,
                "length": len(contact_label.encode("utf-16-le")) // 2,
                "url": contact_url,
            },
            {
                "type": "url",
                "offset": len(notice_text[: -len(requests_url)].encode("utf-16-le")) // 2,
                "length": len(requests_url.encode("utf-16-le")) // 2,
            },
        ],
    )
    return item
