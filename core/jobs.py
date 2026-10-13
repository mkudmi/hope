import time
import uuid
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .crypto import unseal
from .models import Notice, Operation, Subscription, WorkerLock
from .panel import PanelError, PanelMissing, identity_fields, panel, same_config
from .services import (
    DomainError,
    add_duration,
    notify,
    record_observation,
    sync_oldest_observation,
)


def matches(actual, target, creating=False):
    if creating:
        return actual["inboundIds"] == target["inboundIds"] and all(
            actual["client"].get(k) == v for k, v in target["client"].items()
        )
    return same_config(actual, target)


def finish_error(op, status, message):
    Operation.objects.filter(pk=op.pk).update(status=status, error=message, lease_until=None)
    Subscription.objects.filter(pk=op.subscription_id).update(sync_status=status)
    from .services import admin_event

    admin_event(
        f"admin-op-error:{op.pk}:{status}",
        f"Операция {op.kind} для подписки №{op.subscription.number}: {status}. {message} Подробнее: {settings.SITE_URL}/staff/",
    )
    if not op.actor.is_staff:
        destination = "cabinet/" if op.kind == "emergency" else "staff/"
        notify(
            op.actor,
            f"op-error:{op.pk}:{status}",
            f"Подписка №{op.subscription.number}: {message} Подробнее: {settings.SITE_URL}/{destination}",
        )


def apply_operation(op_id):
    now = timezone.now()
    with transaction.atomic():
        op = Operation.objects.select_for_update().select_related("subscription").get(pk=op_id)
        if op.status not in ("queued", "applying", "partial"):
            return
        if op.lease_until and op.lease_until > now:
            return
        op.status, op.lease_until = "applying", now + timedelta(minutes=3)
        op.attempts += 1
        op.save()
    sub = op.subscription
    if settings.PANEL_MODE == "real" and not settings.PANEL_WRITES_ENABLED and op.stage == "prepared":
        finish_error(op, "failed", "Запись в панель выключена. Включите её только после проверки API.")
        return
    target, before = unseal(op.target), unseal(op.before)
    creating = op.kind == "create"
    try:
        gateway = panel()
        try:
            actual = gateway.get(sub.panel_email)
        except PanelMissing:
            actual = None
        if actual is not None and before is not None and identity_fields(actual) != identity_fields(before):
            finish_error(op, "conflict", "Идентичность клиента изменилась. Запись остановлена.")
            return
        already = actual is not None and matches(actual, target, creating)
        if (
            op.kind == "emergency"
            and op.stage == "prepared"
            and not already
            and target["client"]["expiryTime"] <= int(timezone.now().timestamp() * 1000)
        ):
            finish_error(op, "failed", "Экстренный час истёк до применения. Обратитесь к администратору.")
            return
        if op.stage != "prepared" and not already:
            finish_error(
                op, "partial", "После попытки записи результат неоднозначен. Проверьте панель вручную."
            )
            return
        if not already:
            if creating:
                if actual is not None:
                    finish_error(op, "conflict", "Клиент с таким идентификатором уже существует.")
                    return
                # Registration/queue delays must not consume the initial subscription period.
                end = add_duration(timezone.now(), days=op.duration_days, months=op.duration_months)
                target["client"]["expiryTime"] = int(end.timestamp() * 1000)
                from .crypto import seal

                Operation.objects.filter(pk=op.pk).update(target=seal(target))
                Subscription.objects.filter(pk=sub.pk).update(expires_at=end, expiry_kind="fixed")
            elif actual is None or not same_config(actual, before):
                finish_error(op, "conflict", "Панель изменилась после подтверждения. Запись остановлена.")
                return
            # Persist write intent BEFORE contacting the remote server: crash/timeout won't replay blindly.
            Operation.objects.filter(pk=op.pk).update(stage="write_started")
            op.stage = "write_started"
            try:
                if creating:
                    gateway.create(target)
                else:
                    gateway.update(sub.panel_email, target)
            except PanelError:
                finish_error(op, "partial", "Панель не подтвердила полное применение. Требуется проверка.")
                return
            Operation.objects.filter(pk=op.pk).update(stage="written")
            op.stage = "written"
            actual = gateway.get(sub.panel_email)
        if not matches(actual, target, creating) or not gateway.healthy(actual):
            finish_error(op, "partial", "Срок или состояние нод не подтверждены. Доступ требует проверки.")
            return
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().get(pk=sub.pk)
            record_observation(sub, actual)
            sub.sync_status, sub.managed = "verified", True
            sub.save()
            Operation.objects.filter(pk=op.pk).update(
                status="verified", stage="verified", error="", lease_until=None, completed_at=timezone.now()
            )
            from .services import admin_event

            admin_event(
                f"admin-op-verified:{op.pk}",
                f"Операция {op.kind} для подписки №{sub.number}: применение подтверждено. {settings.SITE_URL}/staff/",
            )
            if sub.owner_id:
                text = f"Изменение подписки №{sub.number} применено. {sub.status_label}."
                if sub.expires_at:
                    text += " До " + timezone.localtime(sub.expires_at).strftime("%d.%m.%Y, %H:%M МСК") + "."
                notify(sub.owner, f"op:{op.pk}", text)
    except PanelError:
        if op.stage == "prepared" and op.attempts < 5:
            Operation.objects.filter(pk=op.pk).update(
                status="queued",
                lease_until=None,
                available_at=timezone.now() + timedelta(seconds=min(300, 15 * 2**op.attempts)),
                error="Панель временно недоступна. Чтение будет повторено.",
            )
        else:
            finish_error(
                op, "partial" if op.stage != "prepared" else "failed", "Панель недоступна. Требуется сверка."
            )


def schedule_notices(now=None):
    now = now or timezone.now()
    local = timezone.localtime(now)
    if not 10 <= local.hour < 20:
        return
    for sub in Subscription.objects.filter(
        owner__isnull=False,
        owner__is_active=True,
        excluded=False,
        expiry_kind="fixed",
        sync_status__in=["verified", "unmanaged"],
        suspended=False,
    ).select_related("owner"):
        remaining = (sub.expires_at - now).total_seconds()
        threshold = (
            0
            if remaining <= 0
            else 1
            if remaining <= 86400
            else 3
            if remaining <= 3 * 86400
            else 7
            if remaining <= 7 * 86400
            else None
        )
        if threshold is None:
            continue
        text = (
            f"Подписка №{sub.number} закончилась."
            if threshold == 0
            else f"Подписка №{sub.number} заканчивается "
            + timezone.localtime(sub.expires_at).strftime("%d.%m.%Y, %H:%M МСК")
            + "."
        )
        notify(
            sub.owner,
            f"expiry:{sub.pk}:{sub.version}:{threshold}",
            text + f" Продление выполняет администратор. Кабинет: {settings.SITE_URL}/",
            is_reminder=True,
            subscription_version=sub.version,
        )


def deliver_notices():
    if not settings.BOT_TOKEN:
        return 0
    count = 0
    for notice in Notice.objects.filter(status="pending", available_at__lte=timezone.now()).select_related(
        "user", "telegram_contact", "purchase_order", "purchase_order__subscription"
    )[:10]:
        identity = None if notice.admin_event else getattr(notice.user, "telegram", None)
        if notice.telegram_account_menu and not identity:
            notice.status = "cancelled"
            notice.save(update_fields=["status"])
            continue
        sub = getattr(notice.user, "subscription", None)
        if notice.is_reminder and (
            not sub or sub.version != notice.subscription_version or not identity or not identity.reminders
        ):
            notice.status = "cancelled"
            notice.save()
            continue
        contact = notice.telegram_contact
        admin_chat = (
            (settings.ADMIN_GROUP_CHAT_ID or settings.ADMIN_CHAT_ID)
            if notice.admin_event
            else (settings.ADMIN_CHAT_ID if notice.user and notice.user.is_staff else "")
        )
        if (
            (not identity and not admin_chat and not contact)
            or (identity and not identity.reachable)
            or (notice.user and not notice.user.is_active)
            or (contact and not contact.reachable)
        ):
            notice.status = "portal_only"
            notice.save()
            continue
        try:
            payload = {
                "chat_id": contact.chat_id if contact else identity.chat_id if identity else admin_chat,
                "text": notice.text,
                "link_preview_options": {"is_disabled": True},
            }
            if notice.telegram_entities:
                payload["entities"] = notice.telegram_entities
            if notice.telegram_account_menu:
                from .bot import menu

                payload["reply_markup"] = menu().model_dump(mode="json", exclude_none=True)
            if notice.admin_event and notice.purchase_order_id:
                from .bot import buttons

                order = notice.purchase_order
                rows = []
                if order.kind == "renewal" and order.status in ("awaiting_payment", "payment_review", "paid"):
                    rows.append([("Проверить перевод и продлить", {"callback_data": f"ao:preview:{order.pk}"})])
                if settings.SITE_URL.startswith("https://"):
                    rows.append([("Открыть заказ", {"url": f"{settings.SITE_URL}/staff/orders/{order.pk}/"})])
                if rows:
                    payload["reply_markup"] = buttons(rows).model_dump(mode="json", exclude_none=True)
            response = httpx.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=10,
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            data = {}
        notice.attempts += 1
        if data.get("ok"):
            notice.status = "sent"
            result = data.get("result") or {}
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if isinstance(message_id, int) and message_id > 0:
                notice.telegram_message_id = message_id
                notice.telegram_chat_id = str(payload["chat_id"])
            count += 1
        elif data.get("error_code") == 403:
            if identity:
                identity.reachable = False
                identity.save()
            if contact:
                contact.reachable = False
                contact.save(update_fields=["reachable"])
            notice.status = "portal_only"
        elif notice.attempts >= 5:
            notice.status = "failed"
        else:
            retry = data.get("parameters", {}).get("retry_after", min(3600, 30 * 2**notice.attempts))
            notice.available_at = timezone.now() + timedelta(seconds=max(5, min(86400, retry)))
        notice.save()
        time.sleep(0.05)
    return count


def run_tick():
    """Single global lease serializes panel writes + Telegram sends across workers."""
    now, owner = timezone.now(), str(uuid.uuid4())
    with transaction.atomic():
        lock, _ = WorkerLock.objects.select_for_update().get_or_create(
            name="worker", defaults={"owner": owner, "until": now}
        )
        if lock.until > now:
            return False
        lock.owner, lock.until = owner, now + timedelta(minutes=10)
        lock.save()
    try:
        schedule_notices()
        # At most one panel write per tick; stop writes globally on ambiguous state.
        unresolved = (
            Operation.objects.filter(status__in=["partial", "conflict"]).exists()
            or WorkerLock.objects.filter(name="write-pause", until__gt=now).exists()
        )
        pending = (
            Operation.objects.filter(status__in=["queued", "applying"], available_at__lte=now)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
            .order_by("created_at")
            .first()
        )
        if pending and not unresolved:
            apply_operation(pending.pk)
        from .orders import sync_order_results

        sync_order_results()
        try:
            sync_oldest_observation()
        except (PanelError, DomainError):
            # Cached state retains its original timestamp; no false fresh status.
            pass
        deliver_notices()
    finally:
        WorkerLock.objects.filter(name="worker", owner=owner).update(until=timezone.now())
    return True
