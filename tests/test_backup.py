from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.crypto import unseal

pytestmark = pytest.mark.django_db


def test_backup_is_encrypted_and_never_overwrites(tmp_path, user, sub):
    path = tmp_path / "backup.enc"
    call_command("backup_hope", output=str(path), stdout=StringIO())
    assert user.username not in path.read_text()
    assert str(sub.id) in unseal(path.read_text())["fixture"]
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(CommandError):
        call_command("backup_hope", output=str(path), stdout=StringIO())


def test_restore_refuses_nonempty_database(tmp_path, user):
    with pytest.raises(CommandError):
        call_command("restore_hope", input=str(tmp_path / "missing.enc"), confirm_restoration=True)


def test_restore_round_trip_pauses_writes(tmp_path, user, sub):
    from core.models import DemoClient, Subscription, User, WorkerLock

    path = tmp_path / "backup.enc"
    before_id, before_version = user.id, user.auth_version
    call_command("backup_hope", output=str(path), stdout=StringIO())
    Subscription.objects.all().delete()
    User.objects.all().delete()
    DemoClient.objects.all().delete()
    call_command("restore_hope", input=str(path), confirm_restoration=True, stdout=StringIO())
    restored = User.objects.get(pk=before_id)
    assert restored.auth_version > before_version
    assert restored.subscription.sync_status == "conflict"
    assert WorkerLock.objects.filter(name="write-pause").exists()
    # Restoring explicit IDs must also restore PostgreSQL sequences.
    next_user = User.objects.create_user(username="after-restore", password="Synthetic-test-password")
    assert next_user.pk > restored.pk


def test_restore_expires_unconsumed_order_approvals(tmp_path, user, sub, admin):
    from django.utils import timezone

    from core.models import DemoClient, OrderApproval, PurchaseOrder, Subscription, User
    from core.orders import preview_renewal

    order = PurchaseOrder.objects.create(
        source="web", user=user, subscription=sub, kind="renewal",
        plan_code="month", plan_title="1 месяц", price_rub=299, months=1,
    )
    approval = preview_renewal(order.pk, admin, "Synthetic manual confirmation")
    path = tmp_path / "orders-backup.enc"
    call_command("backup_hope", output=str(path), stdout=StringIO())
    OrderApproval.objects.all().delete()
    PurchaseOrder.objects.all().delete()
    Subscription.objects.all().delete()
    User.objects.all().delete()
    DemoClient.objects.all().delete()
    call_command("restore_hope", input=str(path), confirm_restoration=True, stdout=StringIO())
    assert OrderApproval.objects.get(pk=approval.pk).expires_at <= timezone.now()
