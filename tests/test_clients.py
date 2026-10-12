import pytest

from core.clients import INCY_IOS_URL, client_info, import_url
from core.crypto import unseal

pytestmark = pytest.mark.django_db


def test_ios_incy_and_shared_subscription_links(portal, sub):
    before = unseal(sub.snapshot)
    response = portal.get("/connect/")
    assert response.status_code == 200
    url = response.context["subscription_url"]
    assert response.context["incy_url"] == "incy://import/" + url
    assert response.context["happ_url"] == "happ://add/" + url
    assert INCY_IOS_URL in response.content.decode()
    assert "российского App Store" in response.content.decode()
    sub.refresh_from_db()
    assert unseal(sub.snapshot) == before


def test_excluded_access_does_not_expose_link(portal, sub):
    sub.excluded = True
    sub.save()
    assert portal.get("/connect/").context["subscription_url"] is None


def test_unknown_client_or_platform_has_no_import():
    assert client_info("fake", "ios") is None
    assert client_info("incy", "fake") is None
    assert import_url("fake", "https://example.com/sub") is None
    assert import_url("incy", None) is None
    assert client_info("incy", "ios")["download"] == INCY_IOS_URL
