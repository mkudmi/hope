import uuid

from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    username = models.CharField(
        max_length=32,
        unique=True,
        validators=[
            RegexValidator(
                r"^[a-z0-9][a-z0-9_.-]{3,31}$", "Логин: 4–32 символа латиницей, цифры, точка, _ или -."
            )
        ],
    )
    auth_version = models.PositiveIntegerField(default=1)
    totp_secret = models.TextField(blank=True)
    totp_last_step = models.BigIntegerField(default=-1)

    def save(self, *args, **kwargs):
        self.username = self.username.lower().strip()
        super().save(*args, **kwargs)


class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="subscription"
    )
    label = models.CharField(max_length=120)
    panel_email = models.CharField(max_length=255, unique=True)
    panel_comment = models.TextField(blank=True)  # Private administrative note imported from 3x-ui
    panel_id = models.BigIntegerField(null=True, blank=True)
    snapshot = models.TextField(blank=True)  # Fernet encrypted, includes protocol secrets
    expires_at = models.DateTimeField(null=True, blank=True)
    expiry_kind = models.CharField(max_length=20, default="pending")
    managed = models.BooleanField(default=False)
    excluded = models.BooleanField(default=False)
    suspended = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=0)
    sync_status = models.CharField(max_length=20, default="unmanaged")
    observed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def number(self):
        return str(self.id).split("-")[0].upper()

    @property
    def status_label(self):
        if self.suspended:
            return "Приостановлен администратором"
        if self.expiry_kind == "perpetual":
            return "Бессрочный доступ"
        if self.expires_at:
            return "Срок истёк" if self.expires_at <= timezone.now() else "Подписка активна"
        return "Ожидает выдачи доступа"


class TelegramIdentity(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="telegram")
    telegram_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField(unique=True)
    display_name = models.CharField(max_length=100, blank=True)
    reachable = models.BooleanField(default=True)
    reminders = models.BooleanField(default=True)
    confirmed_at = models.DateTimeField(auto_now_add=True)


class Token(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    digest = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=24)
    user = models.ForeignKey(User, null=True, on_delete=models.CASCADE)
    subscription = models.ForeignKey(Subscription, null=True, on_delete=models.CASCADE)
    session_digest = models.CharField(max_length=64, blank=True)
    candidate_id = models.BigIntegerField(null=True)
    candidate_chat = models.BigIntegerField(null=True)
    candidate_name = models.CharField(max_length=100, blank=True)
    bot_confirmed = models.BooleanField(default=False)
    consumed_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class RecoveryCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True)


class TrustedBrowser(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trusted_browsers")
    token_digest = models.CharField(max_length=64, unique=True)
    auth_version = models.PositiveIntegerField()
    password_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"], name="trusted_user_exp_idx")]


class Operation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="operations")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    reason = models.CharField(max_length=300)
    before = models.TextField(blank=True)
    target = models.TextField()
    status = models.CharField(max_length=20, default="queued")
    stage = models.CharField(max_length=30, default="prepared")
    error = models.CharField(max_length=300, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    duration_days = models.PositiveIntegerField(default=0)
    duration_months = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    details = models.JSONField(default=list)  # safe events, no raw panel error or secrets

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription"],
                condition=Q(status__in=["queued", "applying", "partial", "conflict", "failed"]),
                name="one_pending_operation",
            )
        ]
        ordering = ["-created_at"]


class Notice(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="notices")
    telegram_contact = models.ForeignKey("BotContact", null=True, blank=True, on_delete=models.PROTECT)
    key = models.CharField(max_length=150, unique=True)
    text = models.TextField()
    telegram_entities = models.JSONField(default=list, blank=True)
    telegram_account_menu = models.BooleanField(default=False)
    purchase_order = models.ForeignKey("PurchaseOrder", null=True, blank=True, on_delete=models.CASCADE)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_chat_id = models.CharField(max_length=40, blank=True)
    admin_event = models.BooleanField(default=False)
    is_reminder = models.BooleanField(default=False)
    subscription_version = models.PositiveIntegerField(null=True)
    status = models.CharField(max_length=20, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False, telegram_contact__isnull=True)
                | Q(user__isnull=True, telegram_contact__isnull=False),
                name="notice_one_recipient",
            )
        ]


class Request(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="requests")
    telegram_contact = models.ForeignKey("BotContact", null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    contact = models.CharField(max_length=150, blank=True)
    text = models.TextField(max_length=2000)
    status = models.CharField(max_length=20, default="open")
    reply = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class Audit(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    subject = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RateBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    window = models.DateTimeField(default=timezone.now)


class DemoClient(models.Model):
    email = models.CharField(max_length=255, unique=True)
    data = models.TextField()


class WorkerLock(models.Model):
    name = models.CharField(max_length=30, primary_key=True)
    owner = models.CharField(max_length=40)
    until = models.DateTimeField()


class BotContact(models.Model):
    telegram_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField(unique=True)
    display_name = models.CharField(max_length=100, blank=True)
    reachable = models.BooleanField(default=True)
    support_pending = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class PurchaseOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(BotContact, null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    source = models.CharField(max_length=12, default="telegram")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    subscription = models.ForeignKey(Subscription, null=True, blank=True, on_delete=models.PROTECT)
    operation = models.OneToOneField(Operation, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    plan_code = models.CharField(max_length=20)
    plan_title = models.CharField(max_length=80)
    price_rub = models.PositiveIntegerField()
    days = models.PositiveIntegerField(default=0)
    months = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=24, default="awaiting_payment")
    payment_reported_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="confirmed_orders"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def number(self):
        return str(self.pk).split("-")[0].upper()

    @property
    def status_label(self):
        return {
            "awaiting_payment": "Ожидает оплаты",
            "payment_review": "Перевод проверяется администратором",
            "paid": "Оплата подтверждена, ожидает выдачи",
            "provisioning": "Доступ подготавливается",
            "fulfilled": "Доступ готов",
            "cancelled": "Отменён",
            "rejected": "Не подтверждён администратором",
        }.get(self.status, self.status)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact"],
                condition=Q(status__in=["awaiting_payment", "payment_review", "paid", "provisioning"]),
                name="one_active_purchase_per_contact",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(user__isnull=False, status__in=["awaiting_payment", "payment_review", "paid", "provisioning"]),
                name="one_active_purchase_per_user",
            ),
            models.CheckConstraint(
                condition=Q(source="telegram", contact__isnull=False)
                | Q(source="web", user__isnull=False, contact__isnull=True, kind="renewal"),
                name="purchase_source_recipient",
            ),
        ]


class OrderMessage(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="messages")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    text = models.TextField(max_length=2000)
    key = models.CharField(max_length=150, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class OrderApproval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    order_status = models.CharField(max_length=24)
    terms_digest = models.CharField(max_length=64)
    subscription_version = models.PositiveIntegerField()
    target_expires_at = models.DateTimeField()
    note = models.CharField(max_length=500)
    reenable = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
