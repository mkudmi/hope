import copy
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.crypto import seal, unseal
from core.jobs import apply_operation
from core.models import DemoClient, Operation, WorkerLock
from core.panel import DemoPanel, PanelError
from core.services import DomainError, emergency_availability, prepare_operation, record_observation

pytestmark = pytest.mark.django_db


def expire(sub, when=None):
    state = DemoPanel().get(sub.panel_email)
    state["client"]["expiryTime"] = int((when or timezone.now() - timedelta(days=1)).timestamp() * 1000)
    DemoClient.objects.filter(email=sub.panel_email).update(data=seal(state))
    record_observation(sub, state, adopt=True)
    return state


def grant(sub, user, key=None, **kwargs):
    return prepare_operation(
        sub.pk,
        user,
        kind="emergency",
        reason="",
        version=sub.version,
        operation_id=key or uuid.uuid4(),
        **kwargs,
    )


def test_hour_preserves_fields_and_does_not_trust_user_duration(sub, user):
    before = expire(sub)
    now = timezone.now()
    with patch("core.services.timezone.now", return_value=now):
        op = grant(sub, user, days=300, months=20, exact=now + timedelta(days=500), reenable=True)
    target = unseal(op.target)
    expected = copy.deepcopy(before)
    expected["client"]["expiryTime"] = int((now + timedelta(hours=1)).timestamp() * 1000)
    assert target == expected
    assert DemoPanel().get(sub.panel_email) == before
    apply_operation(op.pk)
    op.refresh_from_db()
    assert op.status == "verified"
    assert DemoPanel().get(sub.panel_email) == expected


def test_repeat_id_is_idempotent_and_new_id_waits_three_hours(sub, user):
    expire(sub)
    key = uuid.uuid4()
    op = grant(sub, user, key)
    apply_operation(op.pk)
    assert grant(sub, user, key).pk == op.pk
    with pytest.raises(DomainError, match="3 часа"):
        grant(sub, user)
    assert Operation.objects.count() == 1
    sub.refresh_from_db()
    with patch("core.services.timezone.now", return_value=op.created_at + timedelta(hours=3)):
        second = grant(sub, user)
    assert second.pk != op.pk


@pytest.mark.parametrize(
    "condition",
    ["active", "perpetual", "unmanaged", "suspended", "excluded", "disabled", "quota", "conflict"],
)
def test_ineligible_subscription_cannot_get_hour(sub, user, condition):
    state = expire(sub)
    if condition == "active":
        expire(sub, timezone.now() + timedelta(days=1))
    elif condition == "perpetual":
        sub.expiry_kind = "perpetual"
    elif condition == "unmanaged":
        sub.managed = False
    elif condition in ("suspended", "excluded"):
        setattr(sub, condition, True)
    elif condition == "conflict":
        sub.sync_status = "conflict"
    else:
        if condition == "disabled":
            state["client"]["enable"] = False
        else:
            state["client"]["totalGB"], state["usedTraffic"] = 100, 100
        DemoClient.objects.filter(email=sub.panel_email).update(data=seal(state))
        sub.snapshot = seal(state)
    sub.save()
    with pytest.raises(DomainError):
        grant(sub, user)
    assert not Operation.objects.exists()


def test_foreign_subscription_and_admin_cannot_grant(sub, admin):
    expire(sub)
    from core.models import User

    other = User.objects.create_user(username="other")
    for actor in [other, admin]:
        with pytest.raises(DomainError):
            grant(sub, actor)
    assert not Operation.objects.exists()


def test_real_write_disabled_and_global_pause(sub, user, settings):
    expire(sub)
    settings.PANEL_MODE = "real"
    with patch("core.services.panel") as gateway:
        with pytest.raises(DomainError):
            grant(sub, user)
        gateway.assert_not_called()
    settings.PANEL_MODE = "demo"
    WorkerLock.objects.create(name="write-pause", owner="manual", until=timezone.now() + timedelta(days=1))
    with pytest.raises(DomainError, match="приостановлены"):
        grant(sub, user)
    assert not Operation.objects.exists()


def historical_grant(sub, user, when):
    op = Operation.objects.create(
        subscription=sub,
        actor=user,
        kind="emergency",
        reason="test",
        before=seal(None),
        target=seal(None),
        status="verified",
    )
    Operation.objects.filter(pk=op.pk).update(created_at=when)
    return op


def test_daily_cap_moscow_and_midnight_cooldown(sub, user):
    expire(sub)
    from datetime import UTC, datetime

    now = datetime(2026, 10, 12, 19, 0, tzinfo=UTC)  # 22:00 MSK
    start = now.replace(hour=0) - timedelta(hours=3)
    for i in range(8):
        historical_grant(sub, user, start + timedelta(minutes=i))
    with (
        patch("core.services.timezone.now", return_value=now),
        pytest.raises(DomainError, match="8 запросов"),
    ):
        grant(sub, user)
    Operation.objects.all().delete()
    historical_grant(sub, user, now + timedelta(minutes=90))  # 23:30 MSK
    with (
        patch("core.services.timezone.now", return_value=now + timedelta(hours=2, minutes=5)),
        pytest.raises(DomainError, match="3 часа"),
    ):
        grant(sub, user)
    with patch("core.services.timezone.now", return_value=now + timedelta(hours=5)):
        assert emergency_availability(sub)[0] == ""


def test_current_panel_quota_checked_not_just_snapshot(sub, user):
    expire(sub)
    state = DemoPanel().get(sub.panel_email)
    state["client"]["totalGB"] = 100
    DemoClient.objects.filter(email=sub.panel_email).update(data=seal(state))
    record_observation(sub, state, adopt=True)
    state["usedTraffic"] = 100
    DemoClient.objects.filter(email=sub.panel_email).update(data=seal(state))
    with pytest.raises(DomainError, match="Состояние панели"):
        grant(sub, user)
    assert not Operation.objects.exists()


def test_expired_queue_hour_never_writes(sub, user):
    expire(sub)
    op = grant(sub, user)
    with (
        patch("core.jobs.timezone.now", return_value=op.created_at + timedelta(hours=2)),
        patch.object(DemoPanel, "update") as update,
    ):
        apply_operation(op.pk)
        update.assert_not_called()
    op.refresh_from_db()
    assert op.status == "failed"


def test_timeout_after_commit_has_no_second_write(sub, user):
    expire(sub)
    op = grant(sub, user)
    original = DemoPanel.update

    def timeout(self, email, state):
        original(self, email, state)
        raise PanelError()

    with patch.object(DemoPanel, "update", timeout):
        apply_operation(op.pk)
    op.refresh_from_db()
    assert op.status == "partial"
    with patch.object(DemoPanel, "update") as update:
        apply_operation(op.pk)
        update.assert_not_called()
    op.refresh_from_db()
    assert op.status == "verified"


def test_view_owner_post_only_and_duplicate(sub, user, portal):
    expire(sub)
    assert portal.get("/emergency-hour/").status_code == 405
    page = portal.get("/cabinet/")
    assert "Экстренный доступ на час" in page.content.decode()
    data = {"operation_id": str(uuid.uuid4()), "version": sub.version}
    assert portal.post("/emergency-hour/", data).status_code == 302
    assert portal.post("/emergency-hour/", data).status_code == 302
    assert Operation.objects.count() == 1
    # Posted subscription IDs cannot select someone else's access.
    op = Operation.objects.get()
    assert op.subscription_id == sub.pk and op.actor_id == user.pk
    from django.test import Client

    assert Client().post("/emergency-hour/", data).status_code == 302
    from tests.conftest import logged_client

    assert logged_client(user, csrf=True).post("/emergency-hour/", data).status_code == 403


def test_view_without_subscription_returns_404(user, portal):
    assert (
        portal.post("/emergency-hour/", {"operation_id": str(uuid.uuid4()), "version": 0}).status_code == 404
    )
