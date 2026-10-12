"""Local-only browser smoke: complete flow, desktop/mobile screenshots, no real secrets in output."""

import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
import pyotp
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db.models import Q
from playwright.sync_api import sync_playwright

from core.crypto import seal, unseal
from core.jobs import apply_operation
from core.models import Audit, DemoClient, Operation, Request, Subscription, Token, User
from core.services import confirm_bot, telegram_candidate

assert settings.PANEL_MODE == "demo" and settings.DEBUG, "Only local demo mode allowed"
base = os.getenv("BROWSER_BASE_URL", "http://127.0.0.1:8001")
out = Path("artifacts/browser")
out.mkdir(parents=True, exist_ok=True)
password = secrets.token_urlsafe(20)
secret = pyotp.random_base32()
qa = User.objects.create_user(username="qaowner", password=password, is_staff=True, totp_secret=seal(secret))
sub = Subscription.objects.get(panel_email="existing-client")
assert not sub.owner_id
original = {f.name: getattr(sub, f.name) for f in sub._meta.fields}
original_demo = DemoClient.objects.get(email=sub.panel_email).data
errors = []
database_pool = ThreadPoolExecutor(max_workers=1)


def db_call(fn):
    return database_pool.submit(fn).result()


try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, permissions=["clipboard-read", "clipboard-write"]
        )
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/")
        assert page.locator("h1").inner_text().startswith("Ваш VPN.")
        page.screenshot(path=str(out / "home-desktop.png"), full_page=True)
        page.goto(base + "/login/")
        page.get_by_label("Логин", exact=True).fill(os.environ["LOCAL_DEMO_LOGIN"])
        page.get_by_label("Пароль", exact=True).fill(os.environ["LOCAL_DEMO_PASSWORD"])
        page.get_by_role("button", name="Войти", exact=True).click()
        page.wait_for_url("**/cabinet/")
        page.screenshot(path=str(out / "cabinet-desktop.png"), full_page=True)
        for width in [320, 390, 768, 1280]:
            page.set_viewport_size({"width": width, "height": 844})
            for path in ["/cabinet/", "/account/", "/connect/", "/help/", "/support/"]:
                page.goto(base + path)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    width,
                    path,
                )
            if width == 390:
                page.goto(base + "/cabinet/")
                page.screenshot(path=str(out / "cabinet-mobile.png"), full_page=True)
        page.goto(base + "/connect/")
        page.get_by_role("button", name="Скопировать", exact=True).click()
        page.get_by_text("Скопировано", exact=True).wait_for()
        page.goto(base + "/support/")
        page.get_by_label("Сообщение", exact=True).fill("Browser QA support request")
        page.get_by_role("button", name="Отправить обращение").click()
        assert page.get_by_text("Browser QA support request", exact=True).is_visible()
        page.get_by_role("button", name="Выйти", exact=True).click()
        page.goto(base + "/login/")
        page.get_by_label("Логин", exact=True).fill("qaowner")
        page.get_by_label("Пароль", exact=True).fill(password)
        page.get_by_role("button", name="Войти", exact=True).click()
        page.wait_for_url("**/staff/mfa/")
        page.get_by_label("Код из приложения-аутентификатора").fill(pyotp.TOTP(secret).now())
        page.get_by_role("button", name="Подтвердить вход").click()
        page.wait_for_url("**/staff/")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path=str(out / "staff-desktop.png"), full_page=True)
        page.goto(base + f"/staff/client/{sub.id}/")
        page.locator("input[name=verified_contact]").check()
        page.get_by_role("button", name="Выдать приглашение").click()
        invite = urlparse(page.locator("#private-link").input_value()).path
        customer = browser.new_context(viewport={"width": 390, "height": 844})
        userpage = customer.new_page()
        userpage.on("pageerror", lambda e: errors.append(str(e)))
        userpage.goto(base + invite)
        userpage.get_by_label("Придумайте логин").fill("qaclient")
        userpage.get_by_label("Придумайте пароль").fill(password)
        userpage.get_by_label("Повторите пароль").fill(password)
        userpage.get_by_role("button", name="Создать аккаунт").click()
        userpage.wait_for_url("**/codes/")
        assert userpage.locator(".code-grid code").count() == 8
        userpage.get_by_role("button", name="Коды сохранены").click()
        userpage.goto(base + "/telegram/")
        userpage.get_by_role("button", name="Создать одноразовую ссылку").click()
        cookie = next(c for c in customer.cookies() if c["name"] == settings.SESSION_COOKIE_NAME)
        session = db_call(lambda: Session.objects.get(session_key=cookie["value"]).get_decoded())
        raw = unseal(session["tg_link_raw"])
        challenge = db_call(lambda: telegram_candidate(raw, 999001, 999001, "Browser QA Telegram"))
        db_call(lambda: confirm_bot(challenge.id, 999001))
        userpage.reload()
        userpage.get_by_role("button", name="Это мой Telegram — завершить").click()
        assert userpage.get_by_text("Browser QA Telegram", exact=True).is_visible()
        page.goto(base + f"/staff/client/{sub.id}/")
        page.get_by_role("button", name="Принять под управление").click()
        page.goto(base + f"/staff/client/{sub.id}/operation/")
        page.get_by_label("Действие", exact=True).select_option("renew")
        page.get_by_label("Календарные месяцы").fill("1")
        page.get_by_label("Основание", exact=True).fill("Browser QA manual renewal")
        page.get_by_role("button", name="Предпросмотр", exact=True).click()
        page.get_by_role("button", name="Подтвердить изменение").click()
        op = db_call(lambda: Operation.objects.get(subscription=sub, status="queued"))
        db_call(lambda: apply_operation(op.id))
        db_call(op.refresh_from_db)
        assert op.status == "verified"
        userpage.goto(base + "/cabinet/")
        assert userpage.get_by_text("Изменение доступа подтверждено", exact=True).is_visible()
        assert not errors, errors
        browser.close()
    print(
        "Browser flow OK: desktop/mobile, login, support, MFA, invite, Telegram confirmation, manual renewal; no page errors."
    )
finally:
    Request.objects.filter(text="Browser QA support request").delete()
    Audit.objects.filter(Q(actor=qa) | Q(actor__username="qaclient")).delete()
    Operation.objects.filter(actor=qa).delete()
    Token.objects.filter(subscription=sub).delete()
    Subscription.objects.filter(pk=sub.pk).update(
        **{k: v for k, v in original.items() if k not in ("id", "owner")}, owner=None
    )
    DemoClient.objects.filter(email=sub.panel_email).update(data=original_demo)
    User.objects.filter(username="qaclient").delete()
    qa.delete()
    from django.db import connections

    db_call(connections.close_all)
    database_pool.shutdown()
