from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from core.jobs import deliver_notices, schedule_notices
from core.models import Notice, TelegramIdentity
from core.services import notify

pytestmark = pytest.mark.django_db


def test_renewal_cancels_stale_notice(sub, user, admin):
    from core.services import prepare_operation

    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    notice = notify(user, "old-reminder", "Old", is_reminder=True, subscription_version=sub.version)
    prepare_operation(sub.pk, admin, kind="renew", reason="Manual", version=sub.version, months=1)
    notice.refresh_from_db()
    assert notice.status == "cancelled"


def test_schedule_does_not_send_all_missed_thresholds(sub, user):
    now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    sub.expires_at = now + timedelta(hours=12)
    sub.save()
    schedule_notices(now)
    schedule_notices(now)
    assert Notice.objects.count() == 1
    assert Notice.objects.get().key.endswith(":1")


def test_quiet_hours(sub):
    now = datetime(2026, 10, 12, 3, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    sub.expires_at = now + timedelta(hours=12)
    sub.save()
    schedule_notices(now)
    assert not Notice.objects.exists()


def test_blocked_bot_does_not_disable_vpn(settings, user, sub):
    settings.BOT_TOKEN = "test-token"
    identity = TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    notice = notify(user, "notice", "Updated")
    with patch("core.jobs.httpx.post", return_value=Mock(json=lambda: {"ok": False, "error_code": 403})):
        deliver_notices()
    identity.refresh_from_db()
    notice.refresh_from_db()
    sub.refresh_from_db()
    assert not identity.reachable and notice.status == "portal_only"
    assert not sub.suspended


def test_retry_429_respects_retry_after(settings, user):
    settings.BOT_TOKEN = "test-token"
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    notice = notify(user, "notice", "Updated")
    with patch(
        "core.jobs.httpx.post",
        return_value=Mock(json=lambda: {"ok": False, "error_code": 429, "parameters": {"retry_after": 120}}),
    ):
        deliver_notices()
    notice.refresh_from_db()
    assert notice.status == "pending"
    assert notice.available_at > timezone.now() + timedelta(seconds=115)


def test_stale_version_checked_immediately_before_send(settings, user, sub):
    settings.BOT_TOKEN = "test-token"
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    notice = notify(user, "old", "Old", is_reminder=True, subscription_version=sub.version - 1)
    with patch("core.jobs.httpx.post") as send:
        deliver_notices()
    notice.refresh_from_db()
    assert notice.status == "cancelled"
    send.assert_not_called()
