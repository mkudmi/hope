import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import get_script_prefix, reverse, set_script_prefix

from core.bot import menu
from tests.conftest import PASSWORD, logged_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def prefixed():
    with override_settings(
        SITE_URL="https://vpn.example.com/hope",
        FORCE_SCRIPT_NAME="/hope",
        STATIC_URL="/hope/static/",
        SESSION_COOKIE_PATH="/hope/",
        CSRF_COOKIE_PATH="/hope/",
        CSRF_TRUSTED_ORIGINS=["https://vpn.example.com"],
        ALLOWED_HOSTS=["vpn.example.com", "testserver"],
    ):
        # Django's test Client bypasses WSGIHandler.__call__, which sets the URL prefix.
        previous = get_script_prefix()
        set_script_prefix("/hope")
        try:
            yield
        finally:
            set_script_prefix(previous)


def test_prefixed_anonymous_redirect_and_static(prefixed):
    client = Client()
    response = client.get("/cabinet/", SCRIPT_NAME="/hope")
    assert response.status_code == 302
    assert response.url.startswith("/hope/login/?next=/hope/cabinet/")
    response = client.get("/login/", SCRIPT_NAME="/hope")
    html = response.content.decode()
    assert "/hope/static/app.css" in html
    assert 'href="/hope/help/"' in html
    response = client.get("/static/app.css", SCRIPT_NAME="/hope")
    assert response.status_code == 200


def test_prefixed_login_cookie_scope_and_logout(prefixed, user):
    client = Client()
    response = client.post("/login/", {"username": user.username, "password": PASSWORD}, SCRIPT_NAME="/hope")
    assert response.status_code == 302 and response.url == "/hope/cabinet/"
    assert response.cookies[settings.SESSION_COOKIE_NAME]["path"] == "/hope/"
    response = client.get("/cabinet/", SCRIPT_NAME="/hope")
    assert response.status_code == 200
    assert response.cookies[settings.CSRF_COOKIE_NAME]["path"] == "/hope/"
    assert client.post("/logout/", SCRIPT_NAME="/hope").url == "/hope/"


def test_prefixed_emergency_form_csrf_and_result(prefixed, sub, user):
    from tests.test_emergency import expire

    expire(sub)
    client = logged_client(user, csrf=True)
    page = client.get("/cabinet/", SCRIPT_NAME="/hope")
    assert 'action="/hope/emergency-hour/"' in page.content.decode()
    form = page.context["emergency_form"]
    data = {
        "operation_id": str(form.initial["operation_id"]),
        "version": sub.version,
        "csrfmiddlewaretoken": client.cookies[settings.CSRF_COOKIE_NAME].value,
    }
    response = client.post(
        "/emergency-hour/",
        data,
        SCRIPT_NAME="/hope",
        HTTP_HOST="vpn.example.com",
        HTTP_ORIGIN="https://vpn.example.com",
        secure=True,
    )
    assert response.status_code == 302 and response.url == "/hope/cabinet/"


def test_prefixed_staff_invitation_and_bot_links(prefixed, staff, sub):
    sub.owner = None
    sub.save()
    response = staff.post(
        f"/staff/client/{sub.id}/", {"action": "invite", "verified_contact": "on"}, SCRIPT_NAME="/hope"
    )
    assert response.context["invite_link"].startswith("https://vpn.example.com/hope/invite/")
    urls = [b.url for row in menu().inline_keyboard for b in row if b.url]
    assert urls == ["https://vpn.example.com/hope/login/"]
    assert reverse("connect") == "/hope/connect/"


def test_cookie_names_separate_from_panel():
    assert settings.SESSION_COOKIE_NAME == "hope_session"
    assert settings.CSRF_COOKIE_NAME == "hope_csrf"


def test_wsgi_handler_sets_prefix_without_test_client_workaround(prefixed):
    from django.core.handlers.wsgi import WSGIHandler
    from django.test import RequestFactory

    set_script_prefix("/")
    environ = RequestFactory().get("/cabinet/").environ
    result = {}

    def start_response(status, headers):
        result.update(status=status, headers=dict(headers))

    response = WSGIHandler()(environ, start_response)
    try:
        assert result["status"].startswith("302")
        assert result["headers"]["Location"].startswith("/hope/login/?next=/hope/cabinet/")
    finally:
        response.close()
