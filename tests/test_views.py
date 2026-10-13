import pytest

from core.models import Request

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path",
    [
        "/cabinet/",
        "/account/",
        "/connect/",
        "/help/",
        "/telegram/",
        "/support/",
        "/renewal/",
        "/requests/",
        "/password/",
    ],
)
def test_portal_pages_render(portal, sub, path):
    response = portal.get(path)
    assert response.status_code == 200
    assert response.headers["Referrer-Policy"] == "same-origin"


@pytest.mark.parametrize("path", ["/staff/", "/staff/new/", "/staff/requests/", "/staff/events/"])
def test_admin_pages_render(staff, path):
    assert staff.get(path).status_code == 200


def test_other_account_cannot_read_subscription(portal, sub):
    assert portal.get(f"/staff/client/{sub.id}/").status_code == 403


def test_renewal_request_does_not_extend(portal, sub):
    from django.test import override_settings

    from core.models import PurchaseOrder
    from core.orders import offer_key, plans

    end = sub.expires_at
    with override_settings(TARIFFS_APPROVED=True):
        plan = plans()[1]
        portal.post("/renewal/", {"plan": f"month:{offer_key(plan)}"})
        portal.post("/renewal/", {"plan": f"month:{offer_key(plan)}"})
    sub.refresh_from_db()
    assert sub.expires_at == end
    assert PurchaseOrder.objects.count() == 1
    assert not Request.objects.filter(kind="renewal").exists()


def test_support_reply_visible_to_owner(staff, portal, user):
    item = Request.objects.create(user=user, kind="support", text="Need help")
    staff.post("/staff/requests/", {"id": item.id, "reply": "Update the subscription"})
    response = portal.get("/requests/")
    assert b"Update the subscription" in response.content


def test_staff_invite_requires_identity_confirmation(staff, sub):
    sub.owner = None
    sub.save()
    response = staff.post(f"/staff/client/{sub.id}/", {"action": "invite"})
    assert response.context["invite_link"] is None
    response = staff.post(f"/staff/client/{sub.id}/", {"action": "invite", "verified_contact": "on"})
    assert "/invite/" in response.context["invite_link"]


def test_qr_requires_owner(portal, sub):
    response = portal.get("/connect/qr/")
    assert response.status_code == 200 and "image/svg+xml" in response.headers["Content-Type"]


def test_mass_notice_is_previewed_and_idempotent(staff, sub):
    from core.models import Notice

    response = staff.post("/staff/broadcast/", {"text": "Maintenance announcement", "group": "active"})
    assert response.status_code == 200 and response.context["count"] == 1
    assert not Notice.objects.exists()
    preview = response.context["preview"]
    staff.post("/staff/broadcast/", {"preview": preview})
    staff.post("/staff/broadcast/", {"preview": preview})
    assert Notice.objects.count() == 1
