from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from django.db import connections

from core.models import PurchaseOrder
from core.orders import reply_to_order
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db(transaction=True)


def database_call(function):
    def run():
        try:
            return function()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


@pytest.fixture
def browser_demo(settings):
    settings.DEBUG = True
    settings.PANEL_MODE = "demo"
    settings.PANEL_WRITES_ENABLED = False
    settings.BOT_TOKEN = ""
    settings.TARIFFS_APPROVED = True


def test_web_customer_receives_admin_message_without_telegram(browser_demo, live_server, user, sub, admin):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).exists():
            pytest.skip("Chromium is not installed for the isolated browser check")
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(live_server.url + "/login/")
            page.get_by_label("Логин", exact=True).fill(user.username)
            page.get_by_label("Пароль", exact=True).fill(PASSWORD)
            page.get_by_role("button", name="Войти", exact=True).click()
            page.get_by_role("link", name="Продление", exact=True).click()
            option = page.get_by_label("1 месяц · 299 ₽", exact=True)
            option.check()
            assert option.bounding_box()["width"] < 30
            page.get_by_role("button", name="Отправить запрос на продление").click()
            order_id, contact_id = database_call(
                lambda: PurchaseOrder.objects.filter(user_id=user.pk).values_list("pk", "contact_id").get()
            )
            assert contact_id is None
            database_call(lambda: reply_to_order(
                order_id, admin, "Тестовый ручной ответ без реальных реквизитов.", "browser-smoke"
            ))
            playwright.expect(page.get_by_text("Тестовый ручной ответ без реальных реквизитов.", exact=True)).to_be_visible(timeout=15000)
            page.get_by_role("button", name="Я оплатил", exact=True).click()
            status, operation_id = database_call(
                lambda: PurchaseOrder.objects.filter(pk=order_id).values_list("status", "operation_id").get()
            )
            assert status == "payment_review" and not operation_id
        finally:
            browser.close()
