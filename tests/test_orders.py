from unittest.mock import patch

import pytest

from core.crypto import unseal
from core.jobs import apply_operation, deliver_notices
from core.models import (
    Notice,
    Operation,
    Request,
    Subscription,
    TelegramIdentity,
    User,
)
from core.orders import (
    bot_contact,
    cancel_order,
    offer_key,
    own_order,
    place_order,
    plans,
    prepare_order,
    report_payment,
    review_order,
    submit_bot_support,
    sync_order_results,
)
from core.panel import DemoPanel
from core.services import DomainError, issue_token, setup_browser_account
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def approved_prices(settings):
    settings.TARIFFS_APPROVED = True
    settings.TARIFF_PRICES = {"week": 99, "month": 299, "quarter": 799, "halfyear": 1499, "year": 2799}


@pytest.fixture
def order():
    contact = bot_contact(101, 101, "Alice")
    plan = plans()[1]
    return place_order(contact, plan["code"], offer_key(plan))


def test_order_snapshot_duplicate_and_catalog_change(order, settings):
    assert order.status == "awaiting_payment" and order.price_rub == 299 and order.months == 1
    plan = plans()[1]
    assert place_order(order.contact, plan["code"], offer_key(plan)).pk == order.pk
    settings.TARIFF_PRICES = {**settings.TARIFF_PRICES, "month": 500}
    with pytest.raises(DomainError):
        place_order(order.contact, plan["code"], offer_key(plan))
    order.refresh_from_db()
    assert order.price_rub == 299 and not Operation.objects.exists()


def test_disabled_catalog_not_published(settings):
    settings.TARIFFS_APPROVED = False
    assert not plans()
    with pytest.raises(DomainError):
        place_order(bot_contact(101, 101), "month", "forged")


def test_unlinked_order_cannot_claim_imported_access(order, sub, admin):
    report_payment(order.pk, 101)
    review_order(order.pk, admin, "confirm", "Transfer verified manually")
    op = prepare_order(order.pk, admin)
    order.refresh_from_db()
    assert op.subscription_id != sub.pk
    assert order.user_id != sub.owner_id
    assert not order.user.has_usable_password()
    assert TelegramIdentity.objects.get(telegram_id=101).user_id == order.user_id
    assert Subscription.objects.count() == 2
    assert sub.owner_id is not None


def test_payment_report_is_not_approval_and_is_idempotent(order, admin):
    report_payment(order.pk, 101)
    report_payment(order.pk, 101)
    order.refresh_from_db()
    assert order.status == "payment_review" and not order.confirmed_at
    assert not Operation.objects.exists()
    with pytest.raises(DomainError):
        prepare_order(order.pk, admin)
    assert not User.objects.filter(username__startswith="tg_").exists()


def test_new_manual_payment_and_provision_are_idempotent(order, admin):
    review_order(order.pk, admin, "confirm", "Funds received")
    review_order(order.pk, admin, "confirm", "Duplicate click")
    op = prepare_order(order.pk, admin)
    assert prepare_order(order.pk, admin).pk == op.pk
    order.refresh_from_db()
    assert order.status == "provisioning"
    assert not Notice.objects.filter(key=f"purchase-ready:{order.pk}").exists()
    apply_operation(op.pk)
    sync_order_results()
    sync_order_results()
    order.refresh_from_db()
    assert order.status == "fulfilled"
    assert Notice.objects.filter(key=f"purchase-ready:{order.pk}").count() == 1
    assert User.objects.filter(username__startswith="tg_").count() == 1


def test_foreign_order_and_malformed_id_cannot_read_or_mutate(order):
    for method in [own_order, report_payment, cancel_order]:
        with pytest.raises(DomainError):
            method(order.pk, 202)
        with pytest.raises(DomainError):
            method("not-a-uuid", 101)
    order.refresh_from_db()
    assert order.status == "awaiting_payment"


def test_cancel_only_before_report_and_cannot_reopen(order, admin):
    report_payment(order.pk, 101)
    with pytest.raises(DomainError):
        cancel_order(order.pk, 101)
    review_order(order.pk, admin, "reject", "Transfer not found, contact support")
    with pytest.raises(DomainError):
        report_payment(order.pk, 101)
    plan = plans()[1]
    new = place_order(order.contact, plan["code"], offer_key(plan))
    assert new.pk != order.pk
    cancel_order(new.pk, 101)
    cancel_order(new.pk, 101)
    with pytest.raises(DomainError):
        review_order(new.pk, admin, "confirm", "Late action")


def test_customer_cannot_confirm_transfer(order, user):
    with pytest.raises(DomainError):
        review_order(order.pk, user, "confirm", "Attempt")
    with pytest.raises(DomainError):
        prepare_order(order.pk, user)


def test_readonly_provision_has_no_account_or_panel_changes(order, admin, settings):
    review_order(order.pk, admin, "confirm", "Manual check")
    settings.PANEL_MODE = "real"
    with patch("core.services.panel") as gateway, pytest.raises(DomainError):
        prepare_order(order.pk, admin)
    gateway.assert_not_called()
    assert not TelegramIdentity.objects.exists()
    assert not Subscription.objects.exists()
    assert not Operation.objects.exists()
    order.refresh_from_db()
    assert order.status == "paid"


def test_renewal_preserves_fields_and_requires_current_binding(sub, user, admin):
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    contact = bot_contact(101, 101)
    plan = plans()[2]
    order = place_order(contact, plan["code"], offer_key(plan))
    before = unseal(sub.snapshot)
    review_order(order.pk, admin, "confirm", "Manual transfer")
    with pytest.raises(DomainError):
        prepare_order(order.pk, admin, version=sub.version + 1)
    op = prepare_order(order.pk, admin, version=sub.version)
    apply_operation(op.pk)
    sync_order_results()
    after = DemoPanel().get(sub.panel_email)
    for key in ["id", "subId", "auth", "password", "totalGB", "tgId", "limitIp"]:
        assert after["client"][key] == before["client"][key]
    assert after["inboundIds"] == before["inboundIds"]
    assert after["client"]["expiryTime"] > before["client"]["expiryTime"]


def test_binding_removed_before_manual_provision_is_rejected(sub, user, admin):
    identity = TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    plan = plans()[1]
    order = place_order(bot_contact(101, 101), plan["code"], offer_key(plan))
    review_order(order.pk, admin, "confirm", "Verified")
    identity.delete()
    with pytest.raises(DomainError):
        prepare_order(order.pk, admin, version=sub.version)
    assert not Operation.objects.exists()


def test_support_guest_and_durable_reply(order, staff):
    contact = order.contact
    contact.support_pending = True
    contact.save()
    item = submit_bot_support(contact, "How do I connect?")
    assert item.user_id is None
    assert submit_bot_support(contact, "Duplicate") is None
    assert Request.objects.count() == 1
    staff.post("/staff/requests/", {"id": item.pk, "reply": "Install Happ"})
    assert Notice.objects.filter(telegram_contact=contact, text__contains="Install Happ").exists()


def test_order_notification_403_marks_contact_unreachable(order, settings):
    from types import SimpleNamespace

    from core.orders import contact_notice

    settings.BOT_TOKEN = "synthetic"
    Notice.objects.all().delete()
    notice = contact_notice(order.contact, "guest-message", "Waiting")
    with patch(
        "core.jobs.httpx.post", return_value=SimpleNamespace(json=lambda: {"error_code": 403})
    ) as post:
        deliver_notices()
    assert post.call_args.kwargs["json"]["chat_id"] == 101
    order.contact.refresh_from_db()
    notice.refresh_from_db()
    assert not order.contact.reachable and notice.status == "portal_only"


def test_bot_only_account_setup_chooses_login_and_consumes_token(order, admin, portal):
    review_order(order.pk, admin, "confirm", "Verified")
    prepare_order(order.pk, admin)
    order.refresh_from_db()
    raw, _token = issue_token("onboard", user=order.user, minutes=15)
    user, codes = setup_browser_account(raw, "chosenlogin", PASSWORD)
    assert user.username == "chosenlogin" and user.check_password(PASSWORD) and len(codes) == 8
    assert TelegramIdentity.objects.get(telegram_id=101).user_id == user.pk
    with pytest.raises(DomainError):
        setup_browser_account(raw, "anotherlogin", PASSWORD)


def test_setup_get_does_not_consume_and_post_has_csrf(order, admin):
    from django.test import Client

    review_order(order.pk, admin, "confirm", "Verified")
    prepare_order(order.pk, admin)
    order.refresh_from_db()
    raw, token = issue_token("onboard", user=order.user, minutes=15)
    client = Client(enforce_csrf_checks=True)
    assert client.get("/setup-account/", {"token": raw}).status_code == 200
    token.refresh_from_db()
    assert not token.consumed_at
    assert (
        client.post(
            "/setup-account/",
            {"token": raw, "username": "chosenlogin", "password": PASSWORD, "password_repeat": PASSWORD},
        ).status_code
        == 403
    )


def test_staff_preview_tamper_and_duplicate_provision(order, staff):
    staff.post(
        f"/staff/orders/{order.pk}/",
        {"action": "confirm", "note": "Manual transfer", "checked_transfer": "on"},
    )
    response = staff.post(f"/staff/orders/{order.pk}/", {"action": "preview", "total_gib": 10, "devices": 2})
    assert response.status_code == 200
    preview = response.context["preview"]
    staff.post(f"/staff/orders/{order.pk}/", {"action": "provision", "preview": preview + "tamper"})
    assert not Operation.objects.exists()
    for _ in range(2):
        staff.post(f"/staff/orders/{order.pk}/", {"action": "provision", "preview": preview})
    assert Operation.objects.count() == 1
    target = unseal(Operation.objects.get().target)
    assert target["client"]["totalGB"] == 10 * 1024**3
    assert target["client"]["limitHwid"] == 2


def test_order_backup_contains_contacts_and_snapshot(tmp_path, order, admin):
    from django.core.management import call_command

    from core.crypto import unseal

    path = tmp_path / "orders.enc"
    call_command("backup_hope", output=str(path))
    fixture = unseal(path.read_text())["fixture"]
    assert "core.purchaseorder" in fixture and "core.botcontact" in fixture
    assert "awaiting_payment" in fixture and "299" in fixture


def test_renewal_preview_freezes_target_and_preserves_paid_price(sub, user, staff):
    TelegramIdentity.objects.create(user=user, telegram_id=101, chat_id=101)
    plan = plans()[1]
    order = place_order(bot_contact(101, 101), plan["code"], offer_key(plan))
    staff.post(
        f"/staff/orders/{order.pk}/",
        {"action": "confirm", "note": "Confirmed manually", "checked_transfer": "on"},
    )
    response = staff.post(f"/staff/orders/{order.pk}/", {"action": "preview", "total_gib": 0, "devices": 0})
    end = response.context["end"]
    assert end > sub.expires_at
    staff.post(f"/staff/orders/{order.pk}/", {"action": "provision", "preview": response.context["preview"]})
    assert unseal(Operation.objects.get().target)["client"]["expiryTime"] == int(end.timestamp() * 1000)


def test_restore_purchase_and_guest_outbox_round_trip(tmp_path, order, admin):
    from django.core.management import call_command

    from core.models import Audit, BotContact, PurchaseOrder, WorkerLock

    review_order(order.pk, admin, "confirm", "Verified manually")
    path = tmp_path / "purchase-round-trip.enc"
    call_command("backup_hope", output=str(path))
    key = order.pk
    Notice.objects.all().delete()
    PurchaseOrder.objects.all().delete()
    BotContact.objects.all().delete()
    Audit.objects.all().delete()
    User.objects.all().delete()
    call_command("restore_hope", input=str(path), confirm_restoration=True)
    restored = PurchaseOrder.objects.get(pk=key)
    assert restored.status == "paid" and restored.price_rub == 299
    assert restored.contact.telegram_id == 101
    assert Notice.objects.filter(telegram_contact=restored.contact).exists()
    assert WorkerLock.objects.filter(name="write-pause").exists()
