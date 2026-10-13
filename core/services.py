import copy
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from .crypto import digest, random_token, seal, unseal
from .models import (
    Audit,
    Notice,
    Operation,
    RateBucket,
    RecoveryCode,
    Subscription,
    TelegramIdentity,
    Token,
    TrustedBrowser,
    User,
    WorkerLock,
)
from .panel import identity_fields, new_state, panel, same_config


class DomainError(Exception):
    pass


def audit(actor, action, subject=""):
    Audit.objects.create(actor=actor, action=action, subject=str(subject))


def rate_limit(scope, key, limit=8, seconds=600):
    now = timezone.now()
    with transaction.atomic():
        bucket, _ = RateBucket.objects.select_for_update().get_or_create(key=digest(scope + ":" + str(key)))
        if now - bucket.window > timedelta(seconds=seconds):
            bucket.window, bucket.count = now, 0
        bucket.count += 1
        bucket.save()
        return bucket.count <= limit


def issue_token(kind, *, user=None, subscription=None, minutes=10, session=""):
    raw = random_token()
    token = Token.objects.create(
        digest=digest(raw),
        kind=kind,
        user=user,
        subscription=subscription,
        expires_at=timezone.now() + timedelta(minutes=minutes),
        session_digest=digest(session) if session else "",
    )
    return raw, token


def live_token(raw, kind, lock=False):
    qs = Token.objects.select_for_update() if lock else Token.objects
    token = qs.filter(
        digest=digest(raw), kind=kind, consumed_at__isnull=True, expires_at__gt=timezone.now()
    ).first()
    if not token:
        raise DomainError("Ссылка недействительна, истекла или уже использована.")
    return token


def recovery_codes(user):
    codes = [secrets.token_hex(10) for _ in range(8)]
    RecoveryCode.objects.filter(user=user).delete()
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, digest=digest(x)) for x in codes])
    return codes


def create_trusted_browser(user):
    now = timezone.now()
    TrustedBrowser.objects.filter(user=user, expires_at__lte=now).delete()
    raw = random_token()
    TrustedBrowser.objects.create(
        user=user,
        token_digest=digest(raw),
        auth_version=user.auth_version,
        password_fingerprint=digest(user.password),
        expires_at=now + timedelta(days=15),
    )
    audit(user, "trusted_browser_added")
    return raw


def trusted_browser_valid(user, raw):
    if not raw or not user.is_staff or not user.is_active or not user.totp_secret:
        return False
    now = timezone.now()
    browser = TrustedBrowser.objects.filter(
        user=user, token_digest=digest(raw), expires_at__gt=now
    ).first()
    if not browser:
        return False
    password_fingerprint = digest(user.password)
    if (
        browser.auth_version != user.auth_version
        or not secrets.compare_digest(browser.password_fingerprint, password_fingerprint)
    ):
        browser.delete()
        return False
    browser.last_used_at = now
    browser.save(update_fields=["last_used_at"])
    return True


@transaction.atomic
def activate(raw, username, password):
    token = live_token(raw, "invite", lock=True)
    sub = Subscription.objects.select_for_update().get(pk=token.subscription_id)
    if sub.owner_id or sub.excluded:
        raise DomainError("Приглашение больше не доступно. Обратитесь к администратору.")
    user = User(username=username.lower().strip())
    user.full_clean(exclude=["password"])
    validate_password(password, user)
    user.set_password(password)
    user.save()
    sub.owner = user
    sub.save(update_fields=["owner"])
    Token.objects.filter(subscription=sub, kind="invite", consumed_at__isnull=True).update(
        consumed_at=timezone.now()
    )
    audit(user, "account_activated", sub.number)
    admin_event(
        f"account-activated:{sub.pk}",
        f"Аккаунт активирован для подписки №{sub.number}. {settings.SITE_URL}/staff/",
    )
    return user, recovery_codes(user)


def invitation(sub, actor):
    if sub.owner_id or sub.excluded:
        raise DomainError("Подписка уже активирована или исключена из обслуживания.")
    with transaction.atomic():
        Subscription.objects.select_for_update().get(pk=sub.pk)
        Token.objects.filter(subscription=sub, kind="invite", consumed_at__isnull=True).update(
            consumed_at=timezone.now()
        )
        raw, _token = issue_token("invite", subscription=sub, minutes=72 * 60)
        audit(actor, "invitation_created", sub.number)
        admin_event(
            f"invitation-created:{_token.pk}",
            f"Приглашение создано для подписки №{sub.number}. Передайте его лично проверенному владельцу. {settings.SITE_URL}/staff/",
        )
        return raw


@transaction.atomic
def telegram_candidate(raw, telegram_id, chat_id, name):
    token = live_token(raw, "telegram", lock=True)
    if telegram_id != chat_id or token.user.is_staff or not token.user.is_active:
        raise DomainError("Привязка доступна только в личном чате обычного пользователя.")
    if token.candidate_id and token.candidate_id != telegram_id:
        raise DomainError("Эта ссылка уже открыта другим Telegram. Создайте новую в кабинете.")
    if TelegramIdentity.objects.filter(telegram_id=telegram_id).exclude(user=token.user).exists():
        raise DomainError("Этот Telegram уже привязан к другому аккаунту.")
    token.candidate_id, token.candidate_chat, token.candidate_name = telegram_id, chat_id, name[:100]
    token.save()
    return token


@transaction.atomic
def confirm_bot(token_id, telegram_id):
    token = (
        Token.objects.select_for_update()
        .filter(
            pk=token_id,
            kind="telegram",
            candidate_id=telegram_id,
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not token:
        raise DomainError("Подтверждение недействительно.")
    token.bot_confirmed = True
    token.save(update_fields=["bot_confirmed"])


@transaction.atomic
def confirm_web(token_id, user, session):
    token = (
        Token.objects.select_for_update()
        .filter(
            pk=token_id, kind="telegram", user=user, consumed_at__isnull=True, expires_at__gt=timezone.now()
        )
        .first()
    )
    if not token or token.session_digest != digest(session) or not token.bot_confirmed:
        raise DomainError("Подтвердите привязку в боте и вернитесь в исходный браузер.")
    if TelegramIdentity.objects.filter(user=user).exists():
        raise DomainError("Сначала отвяжите прежний Telegram с повторной проверкой.")
    try:
        with transaction.atomic():
            TelegramIdentity.objects.create(
                user=user,
                telegram_id=token.candidate_id,
                chat_id=token.candidate_chat,
                display_name=token.candidate_name,
            )
    except IntegrityError:
        raise DomainError("Этот Telegram уже занят. Привязка не изменена.") from None
    token.consumed_at = timezone.now()
    token.save(update_fields=["consumed_at"])
    audit(user, "telegram_linked")
    admin_event(
        f"telegram-linked:{token.id}",
        f"Telegram привязан к аккаунту {user.username}. Подробнее: {settings.SITE_URL}/staff/",
    )
    notify(
        user,
        f"tg-linked:{token.id}",
        "✅ Telegram подключён к вашему аккаунту HOPE VPN.\n\n"
        "Ваша подписка, подключение и продление — в меню ниже.",
        telegram_account_menu=True,
    )


def notify(user, key, text, **kwargs):
    return Notice.objects.get_or_create(key=key, defaults={"user": user, "text": text, **kwargs})[0]


def admin_event(key, text, *, telegram_entities=None, purchase_order=None):
    owner = User.objects.filter(is_staff=True, is_active=True).order_by("pk").first()
    if owner:
        return notify(
            owner, key, text, admin_event=True,
            telegram_entities=telegram_entities or [], purchase_order=purchase_order,
        )


def subscription_url(sub):
    state = unseal(sub.snapshot) if sub and sub.snapshot else None
    if not state or not state["client"].get("subId") or sub.excluded:
        return None
    if settings.PANEL_MODE == "demo":
        return settings.SITE_URL + "/demo-subscription/"
    if urlparse(settings.PANEL_SUB_URL).scheme != "https":
        return None
    return settings.PANEL_SUB_URL + quote(state["client"]["subId"], safe="")


@transaction.atomic
def setup_browser_account(raw, username, password):
    token = live_token(raw, "onboard", lock=True)
    user = User.objects.select_for_update().get(pk=token.user_id)
    if not user.is_active or user.is_staff or user.has_usable_password():
        raise DomainError("Настройка уже завершена или недоступна. Используйте обычный вход/восстановление.")
    user.username = username.lower().strip()
    user.full_clean(exclude=["password"])
    validate_password(password, user)
    user.set_password(password)
    user.auth_version += 1
    user.save()
    Token.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=timezone.now())
    audit(user, "browser_account_configured")
    notify(
        user,
        f"browser-setup:{token.pk}",
        "Логин и пароль независимого кабинета настроены. Сохраните коды восстановления.",
    )
    return user, recovery_codes(user)


@transaction.atomic
def reset_password(username, password, code=None, raw=None):
    user = (
        User.objects.select_for_update()
        .filter(username=username.lower().strip(), is_active=True, is_staff=False)
        .first()
    )
    if not user:
        raise DomainError("Не удалось восстановить доступ. Проверьте данные.")
    if raw:
        token = live_token(raw, "reset", lock=True)
        if token.user_id != user.id:
            raise DomainError("Не удалось восстановить доступ. Проверьте данные.")
        token.consumed_at = timezone.now()
        token.save()
    else:
        record = (
            RecoveryCode.objects.select_for_update()
            .filter(user=user, digest=digest((code or "").strip()), used_at__isnull=True)
            .first()
        )
        if not record:
            raise DomainError("Не удалось восстановить доступ. Проверьте данные.")
        record.used_at = timezone.now()
        record.save()
    validate_password(password, user)
    user.set_password(password)
    user.auth_version += 1
    user.save()
    Token.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=timezone.now())
    audit(user, "password_reset")
    notify(user, f"reset:{uuid.uuid4()}", "Пароль кабинета изменён. Все прежние сессии завершены.")
    return user, recovery_codes(user)


def expiry_from(state):
    value = state["client"].get("expiryTime", 0)
    if value < 0:
        return None, "relative"
    if value == 0:
        return None, "perpetual"
    return datetime.fromtimestamp(value / 1000, UTC), "fixed"


def record_observation(sub, state, adopt=False):
    if sub.panel_id is not None and state.get("record_id") != sub.panel_id:
        sub.sync_status = "conflict"
        sub.save(update_fields=["sync_status"])
        raise DomainError("Идентичность клиента изменилась. Требуется ручная проверка.")
    comment = state["client"].get("comment")
    sub.panel_comment = comment if isinstance(comment, str) else ""
    sub.snapshot, sub.observed_at = seal(state), timezone.now()
    sub.panel_id = state.get("record_id")
    if adopt:
        sub.expires_at, sub.expiry_kind = expiry_from(state)
        sub.version += 1
    sub.save()


def import_clients(actor=None):
    gateway = panel()
    count = 0
    listed = gateway.list()
    groups = {}
    for row in listed:
        sid = row["client"].get("subId")
        if sid:
            groups[sid] = groups.get(sid, 0) + 1
    for row in listed:
        email = row["client"]["email"]
        if email in settings.PANEL_EXCLUDED_CLIENTS:
            continue
        with transaction.atomic():
            sub, new = Subscription.objects.select_for_update().get_or_create(
                panel_email=email, defaults={"label": email}
            )
            if sub.excluded:
                continue
            if groups.get(row["client"].get("subId"), 0) > 1:
                sub.sync_status = "conflict"
                sub.save()
                continue
            current = gateway.get(email)
            if sub.managed and sub.snapshot and not observation_matches(unseal(sub.snapshot), current):
                if not sub.operations.filter(status__in=["queued", "applying", "partial"]).exists():
                    sub.sync_status = "conflict"
                    sub.save()
                continue
            changed = not sub.snapshot or not same_config(unseal(sub.snapshot), current)
            record_observation(sub, current, adopt=not sub.managed and changed)
            count += int(new)
    audit(actor, "clients_imported", str(count))
    admin_event(
        f"panel-import:{uuid.uuid4()}",
        f"Импорт панели завершён. Новых записей: {count}. Конфликтных записей: {Subscription.objects.filter(sync_status='conflict').count()}. {settings.SITE_URL}/staff/",
    )
    return count


def add_duration(base, days=0, months=0):
    return (
        base.astimezone(ZoneInfo("Europe/Moscow")) + relativedelta(months=months) + timedelta(days=days)
    ).astimezone(UTC)


def emergency_availability(sub, now=None):
    """Read-only UI hint; rechecked under the subscription lock on POST."""
    now = now or timezone.now()
    if not sub or sub.excluded or sub.suspended:
        return "Для этого доступа экстренный час недоступен.", None
    if settings.PANEL_MODE == "real" and not settings.PANEL_WRITES_ENABLED:
        return "Экстренный доступ пока не включён. Обратитесь к администратору.", None
    if (
        WorkerLock.objects.filter(name="write-pause", until__gt=now).exists()
        or Operation.objects.filter(status__in=["partial", "conflict"]).exists()
    ):
        return "Изменения доступа временно приостановлены. Обратитесь к администратору.", None
    if not sub.managed or not sub.snapshot or sub.sync_status != "verified":
        return "Доступ требует проверки администратора.", None
    if sub.expiry_kind != "fixed" or not sub.expires_at:
        return "Экстренный час доступен только после истечения подписки.", None
    if sub.operations.filter(status__in=["queued", "applying", "partial", "conflict", "failed"]).exists():
        return "Предыдущее изменение доступа ещё не завершено.", None
    grants = sub.operations.filter(kind="emergency")
    last = grants.order_by("-created_at").first()
    if last and last.created_at + timedelta(hours=3) > now:
        return "Экстренный час можно запрашивать раз в 3 часа.", last.created_at + timedelta(hours=3)
    start = now.astimezone(ZoneInfo("Europe/Moscow")).replace(hour=0, minute=0, second=0, microsecond=0)
    if grants.filter(created_at__gte=start, created_at__lt=start + timedelta(days=1)).count() >= 8:
        return "Лимит 8 запросов за сутки исчерпан.", start + timedelta(days=1)
    if sub.expires_at > now:
        return "Подписка ещё действует. Экстренный час не требуется.", None
    state = unseal(sub.snapshot)
    client = state["client"]
    if not client.get("enable"):
        return "Доступ отключён в панели. Обратитесь к администратору.", None
    if client.get("totalGB", 0) and state.get("usedTraffic", 0) >= client["totalGB"]:
        return "Квота трафика исчерпана. Экстренный час её не сбрасывает.", None
    return "", None


@transaction.atomic
def prepare_operation(
    sub_id,
    actor,
    *,
    kind,
    reason,
    version,
    days=0,
    months=0,
    exact=None,
    total_bytes=0,
    limit_devices=0,
    reenable=False,
    operation_id=None,
):
    emergency = kind == "emergency"
    if not actor.is_active or (not emergency and not actor.is_staff) or (emergency and actor.is_staff):
        raise DomainError("Действие доступно администратору.")
    if (
        operation_id
        and Operation.objects.filter(pk=operation_id, actor=actor, subscription_id=sub_id, kind=kind).exists()
    ):
        return Operation.objects.get(pk=operation_id)
    if settings.PANEL_MODE == "real" and not settings.PANEL_WRITES_ENABLED:
        raise DomainError(
            "Изменения действующей панели выключены. Приглашения и привязка доступны без записи в VPN."
        )
    sub = Subscription.objects.select_for_update().get(pk=sub_id)
    if emergency:
        if actor.is_staff or sub.owner_id != actor.pk:
            raise DomainError("Действие доступно только владельцу подписки.")
        error, _ = emergency_availability(sub)
        if error:
            raise DomainError(error)
        # User-supplied durations, exact dates and reenable must never affect this grant.
        days, months, exact, reenable = 0, 0, None, False
        reason = "Экстренный доступ на час по запросу владельца подписки"
    if (
        sub.excluded
        or sub.version != version
        or sub.operations.filter(status__in=["queued", "applying", "partial", "conflict", "failed"]).exists()
    ):
        raise DomainError("Карточка изменилась или уже есть незавершённая операция. Обновите страницу.")
    if not reason.strip():
        raise DomainError("Укажите основание действия.")
    if kind not in ("create", "renew", "set_expiry", "disable", "enable", "emergency"):
        raise DomainError("Неизвестная операция.")
    gateway = panel()
    now = timezone.now()
    if kind == "create":
        if sub.snapshot or not sub.owner_id or days + months <= 0:
            raise DomainError("Сначала активируйте аккаунт; доступ должен быть новым, срок — положительным.")
        base = now
        before = None
        end = exact or add_duration(base, days, months)
        if end <= now:
            raise DomainError("Подтверждение срока устарело. Выполните новый предпросмотр.")
        target = new_state(sub.panel_email, int(end.timestamp() * 1000), total_bytes, limit_devices)
    else:
        if not sub.managed or not sub.snapshot or sub.sync_status == "conflict":
            raise DomainError("Примите подписку под управление и разрешите конфликты.")
        before = gateway.get(sub.panel_email)
        saved = unseal(sub.snapshot)
        if identity_fields(before) != identity_fields(saved) or not same_config(before, saved):
            raise DomainError("Панель изменилась. Сначала выполните сверку карточки.")
        if any(before["client"].get(k, 0) for k in ("reset", "resetDay", "resetWeekday")):
            raise DomainError(
                "В панели включён автоматический перенос срока. Отключите его отдельно перед обслуживанием."
            )
        target = copy.deepcopy(before)
        if emergency:
            c = before["client"]
            if (
                not c.get("enable")
                or c.get("expiryTime", 0) <= 0
                or c["expiryTime"] > int(now.timestamp() * 1000)
                or (c.get("totalGB", 0) and before.get("usedTraffic", 0) >= c["totalGB"])
            ):
                raise DomainError("Состояние панели не позволяет выдать экстренный час.")
            target["client"]["expiryTime"] = int((now + timedelta(hours=1)).timestamp() * 1000)
        elif kind == "renew":
            if sub.expiry_kind != "fixed" or days + months <= 0 or days < 0 or months < 0:
                raise DomainError(
                    "Для бессрочного/относительного доступа используйте точную дату. Срок должен быть положительным."
                )
            base = max(now, sub.expires_at)
            end = exact or add_duration(base, days, months)
            if end <= now:
                raise DomainError("Подтверждение срока устарело. Выполните новый предпросмотр.")
            target["client"]["expiryTime"] = int(end.timestamp() * 1000)
        elif kind == "set_expiry":
            if not exact or exact <= now:
                raise DomainError("Укажите будущую точную дату.")
            target["client"]["expiryTime"] = int(exact.timestamp() * 1000)
        elif kind == "disable":
            target["client"]["enable"] = False
        if kind == "enable" or reenable:
            c = target["client"]
            if c.get("totalGB", 0) and before.get("usedTraffic", 0) >= c["totalGB"]:
                raise DomainError("Квота исчерпана. Продление не сбрасывает трафик.")
            if c["expiryTime"] < 0 or (c["expiryTime"] and c["expiryTime"] <= int(now.timestamp() * 1000)):
                raise DomainError("Сначала назначьте действующий срок.")
            target["client"]["enable"] = True
    op = Operation.objects.create(
        id=operation_id or uuid.uuid4(),
        subscription=sub,
        actor=actor,
        kind=kind,
        reason=reason.strip(),
        before=seal(before),
        target=seal(target),
        duration_days=days if kind == "create" else 0,
        duration_months=months if kind == "create" else 0,
    )
    sub.expires_at, sub.expiry_kind = expiry_from(target)
    sub.version += 1
    admin_event(
        f"operation-queued:{op.pk}",
        f"Операция {kind} для подписки №{sub.number} поставлена в очередь. Подробнее: {settings.SITE_URL}/staff/",
    )
    sub.sync_status = "queued"
    if kind == "disable":
        sub.suspended = True
    elif kind == "enable" or reenable:
        sub.suspended = False
    sub.save()
    if sub.owner_id:
        Notice.objects.filter(user=sub.owner, is_reminder=True, status="pending").update(status="cancelled")
    audit(actor, "operation_prepared:" + kind, op.id)
    return op


@transaction.atomic
def adopt_panel(sub, actor, managed=False):
    sub = Subscription.objects.select_for_update().get(pk=sub.pk)
    if sub.operations.filter(status__in=["queued", "applying"]).exists():
        raise DomainError("Дождитесь завершения выполняемой операции.")
    state = panel().get(sub.panel_email)
    if sub.snapshot and identity_fields(unseal(sub.snapshot)) != identity_fields(state):
        raise DomainError(
            "Изменились секреты или идентификатор клиента. Импорт нового владельца выполняется отдельно."
        )
    if managed and any(state["client"].get(k, 0) for k in ("reset", "resetDay", "resetWeekday")):
        raise DomainError("Сначала отключите панельное автопродление. HOPE не меняет его незаметно.")
    record_observation(sub, state, adopt=True)
    sub.managed = managed or sub.managed
    sub.sync_status = "verified" if sub.managed else "unmanaged"
    sub.save()
    sub.operations.filter(status__in=["partial", "conflict", "failed"]).update(
        status="cancelled", completed_at=timezone.now()
    )
    audit(actor, "panel_adopted", sub.number)


def observation_matches(saved, current):
    if same_config(saved, current):
        return True
    adjusted = copy.deepcopy(current)
    c = current["client"]
    depleted = (c.get("expiryTime", 0) > 0 and c["expiryTime"] <= int(timezone.now().timestamp() * 1000)) or (
        c.get("totalGB", 0) > 0 and current.get("usedTraffic", 0) >= c["totalGB"]
    )
    if saved["client"].get("enable") and not c.get("enable") and depleted:
        adjusted["client"]["enable"] = True
        return same_config(saved, adjusted)
    return False


def sync_oldest_observation():
    """One bounded read per tick, no remote writes or silent commercial adoption."""
    cutoff = timezone.now() - timedelta(minutes=5)
    sub = (
        Subscription.objects.filter(
            managed=True, excluded=False, sync_status="verified", observed_at__lt=cutoff
        )
        .order_by("observed_at")
        .first()
    )
    if not sub:
        return
    state = panel().get(sub.panel_email)
    with transaction.atomic():
        sub = Subscription.objects.select_for_update().get(pk=sub.pk)
        if sub.sync_status != "verified":
            return
        if not observation_matches(unseal(sub.snapshot), state) or identity_fields(
            unseal(sub.snapshot)
        ) != identity_fields(state):
            sub.sync_status = "conflict"
            sub.save()
            audit(None, "panel_drift_detected", sub.number)
        else:
            record_observation(sub, state)
