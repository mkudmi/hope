import copy
from unittest.mock import patch

import pytest

from core.crypto import unseal
from core.services import import_clients, record_observation

pytestmark = pytest.mark.django_db


def test_import_refreshes_comment_without_replacing_local_label(sub):
    original = unseal(sub.snapshot)
    sub.managed = False
    sub.label = "Локальное имя"
    sub.save()
    current = copy.deepcopy(original)
    current["client"]["comment"] = "Тестовый клиент\nСемья"
    with patch("core.services.panel") as gateway:
        gateway.return_value.list.return_value = [current]
        gateway.return_value.get.return_value = current
        assert import_clients() == 0
        sub.refresh_from_db()
        assert sub.panel_comment == "Тестовый клиент\nСемья"
        assert sub.label == "Локальное имя"
        assert sub.owner_id is not None
        assert sub.panel_email == original["client"]["email"]
        assert current == unseal(sub.snapshot)
        gateway.return_value.update.assert_not_called()
        gateway.return_value.create.assert_not_called()
        current["client"]["comment"] = ""
        import_clients()
    sub.refresh_from_db()
    assert sub.panel_comment == ""


def test_comment_missing_null_and_unexpected_type_are_empty(sub):
    state = unseal(sub.snapshot)
    for value in [None, {}, 123, "Текст"]:
        state["client"]["comment"] = value
        record_observation(sub, state)
        sub.refresh_from_db()
        assert sub.panel_comment == (value if isinstance(value, str) else "")
    state["client"].pop("comment")
    record_observation(sub, state)
    assert sub.panel_comment == ""


def test_staff_comment_search_display_escaping_and_user_privacy(staff, portal, sub):
    sub.panel_comment = "Семья <script>alert(1)</script>"
    sub.save()
    response = staff.get("/staff/", {"q": "Семья"})
    assert sub in response.context["subs"]
    assert "Комментарий из панели" in response.content.decode()
    assert "&lt;script&gt;" in response.content.decode()
    assert "<script>alert(1)</script>" not in response.content.decode()
    detail = staff.get(f"/staff/client/{sub.pk}/")
    assert detail.status_code == 200
    assert "&lt;script&gt;" in detail.content.decode()
    assert "Семья" not in portal.get("/").content.decode()
    assert portal.get("/staff/", {"q": "Семья"}).status_code != 200
    sub.panel_comment = ""
    sub.save()
    assert "—" in staff.get("/staff/").content.decode()
