import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.crypto import unseal
from core.jobs import apply_operation, run_tick
from core.models import Operation, Subscription, WorkerLock
from core.panel import DemoPanel, PanelError
from core.services import DomainError, add_duration, adopt_panel, import_clients, prepare_operation

pytestmark = pytest.mark.django_db


def renew(sub, admin, **kwargs):
    return prepare_operation(
        sub.pk,
        admin,
        kind="renew",
        reason="Manual verified transfer",
        version=sub.version,
        months=1,
        **kwargs,
    )


def test_manual_renewal_preserves_secrets_and_quota(sub, admin):
    before = unseal(sub.snapshot)
    op = renew(sub, admin)
    assert op.status == "queued"
    assert DemoPanel().get(sub.panel_email)["client"]["expiryTime"] == before["client"]["expiryTime"]
    apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "verified"
    after = DemoPanel().get(sub.panel_email)
    for field in ("id", "auth", "password", "subId", "totalGB", "tgId", "limitIp"):
        assert after["client"][field] == before["client"][field]
    assert after["inboundIds"] == before["inboundIds"]
    assert after["client"]["expiryTime"] > before["client"]["expiryTime"]


def test_duplicate_operation_never_adds_twice(sub, admin):
    key = uuid.uuid4()
    op = renew(sub, admin, operation_id=key)
    apply_operation(op.id)
    end = DemoPanel().get(sub.panel_email)["client"]["expiryTime"]
    same = renew(sub, admin, operation_id=key)
    assert same.id == op.id
    apply_operation(op.id)
    assert DemoPanel().get(sub.panel_email)["client"]["expiryTime"] == end


def test_second_concurrent_intent_rejected(sub, admin):
    renew(sub, admin)
    with pytest.raises(DomainError):
        renew(sub, admin)


def test_panel_changed_after_preview_is_not_overwritten(sub, admin):
    op = renew(sub, admin)
    state = DemoPanel().get(sub.panel_email)
    state["client"]["expiryTime"] += 5 * 86400000
    DemoPanel().update(sub.panel_email, state)
    apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "conflict"
    assert DemoPanel().get(sub.panel_email)["client"]["expiryTime"] == state["client"]["expiryTime"]


def test_timeout_after_success_does_not_replay_write(sub, admin):
    op = renew(sub, admin)
    original = DemoPanel.update

    def lost_response(self, email, state):
        original(self, email, state)
        raise PanelError(ambiguous=True)

    with patch.object(DemoPanel, "update", lost_response):
        apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "partial" and op.stage == "write_started"
    with patch.object(DemoPanel, "update", side_effect=AssertionError("must not replay")):
        apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "verified"


def test_ambiguous_write_without_success_needs_manual_review(sub, admin):
    op = renew(sub, admin)
    with patch.object(DemoPanel, "update", side_effect=PanelError(ambiguous=True)):
        apply_operation(op.id)
    with patch.object(DemoPanel, "update", side_effect=AssertionError("must not replay")):
        apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "partial"


def test_worker_crash_after_api_write_recovers(sub, admin):
    op = renew(sub, admin)
    DemoPanel().update(sub.panel_email, unseal(op.target))
    Operation.objects.filter(pk=op.pk).update(
        status="applying", stage="write_started", lease_until=timezone.now() - timedelta(seconds=1)
    )
    with patch.object(DemoPanel, "update", side_effect=AssertionError("already applied")):
        apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "verified"


def test_node_failure_does_not_claim_success(sub, admin):
    op = renew(sub, admin)
    with patch.object(DemoPanel, "healthy", return_value=False):
        apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "partial"


def test_initial_create_unique_credentials_and_separate_sub(user, admin):
    sub = Subscription.objects.create(owner=user, label="New", panel_email="new-person")
    op = prepare_operation(sub.id, admin, kind="create", reason="Initial access", version=0, days=7)
    apply_operation(op.id)
    op.refresh_from_db()
    sub.refresh_from_db()
    assert op.status == "verified" and sub.managed and sub.snapshot
    assert DemoPanel().get("new-person")["client"]["expiryTime"] > int(timezone.now().timestamp() * 1000)


def test_quota_never_reset_and_reenable_rejected(sub, admin):
    state = DemoPanel().get(sub.panel_email)
    state["client"]["totalGB"] = 100
    state["client"]["enable"] = False
    state["usedTraffic"] = 100
    from core.crypto import seal
    from core.models import DemoClient

    DemoClient.objects.filter(email=sub.panel_email).update(data=seal(state))
    adopt_panel(sub, admin, managed=True)
    sub.refresh_from_db()
    with pytest.raises(DomainError, match="Квота"):
        renew(sub, admin, reenable=True)
    op = renew(sub, admin)
    apply_operation(op.id)
    state = DemoPanel().get(sub.panel_email)
    assert not state["client"]["enable"] and state["usedTraffic"] == 100


def test_calendar_leap_months():
    base = datetime(2028, 1, 31, 9, 15, tzinfo=UTC)
    assert add_duration(base, months=1) == datetime(2028, 2, 29, 9, 15, tzinfo=UTC)
    assert add_duration(datetime(2027, 1, 31, 9, 15, tzinfo=UTC), months=1).day == 28


def test_unlimited_not_silently_converted(sub, admin):
    state = DemoPanel().get(sub.panel_email)
    state["client"]["expiryTime"] = 0
    DemoPanel().update(sub.panel_email, state)
    adopt_panel(sub, admin, managed=True)
    sub.refresh_from_db()
    with pytest.raises(DomainError):
        renew(sub, admin)


def test_import_idempotent_and_never_changes_panel(sub):
    state = DemoPanel().get(sub.panel_email)
    initial_version = sub.version
    assert import_clients() == 0
    assert import_clients() == 0
    sub.refresh_from_db()
    assert sub.version == initial_version
    assert DemoPanel().get(sub.panel_email) == state


def test_read_failure_retries_without_writing(sub, admin):
    op = renew(sub, admin)
    with patch.object(DemoPanel, "get", side_effect=PanelError()):
        apply_operation(op.id)
    op.refresh_from_db()
    assert op.status == "queued" and op.stage == "prepared"


def test_write_pause_keeps_queued_commands(sub, admin):
    op = renew(sub, admin)
    WorkerLock.objects.create(name="write-pause", owner="test", until=timezone.now() + timedelta(days=1))
    run_tick()
    op.refresh_from_db()
    assert op.status == "queued"


def test_staff_preview_tamper_and_repeat(staff, sub):
    response = staff.post(
        f"/staff/client/{sub.id}/operation/",
        {"kind": "renew", "months": 1, "days": 0, "reason": "Verified", "version": sub.version},
    )
    assert response.status_code == 200
    signed = response.context["preview"]
    assert staff.post(f"/staff/client/{sub.id}/operation/", {"preview": signed + "tamper"}).status_code == 302
    assert not Operation.objects.exists()
    staff.post(f"/staff/client/{sub.id}/operation/", {"preview": signed})
    staff.post(f"/staff/client/{sub.id}/operation/", {"preview": signed})
    assert Operation.objects.count() == 1


def test_real_readonly_rejects_command_before_any_write(settings, sub, admin):
    settings.PANEL_MODE = "real"
    settings.PANEL_WRITES_ENABLED = False
    with (
        patch("core.services.panel", side_effect=AssertionError("no panel access needed")),
        pytest.raises(DomainError, match="выключены"),
    ):
        renew(sub, admin)
    assert not Operation.objects.exists()
