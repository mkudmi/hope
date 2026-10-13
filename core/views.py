import io
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

import pyotp
import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .clients import HAPP_URL, INCY_IOS_URL, import_url
from .crypto import digest, seal, unseal
from .forms import (
    EmergencyForm,
    LoginForm,
    MessageForm,
    OperationForm,
    PasswordForm,
    PublicRequestForm,
    RecoveryForm,
    RegisterForm,
    SensitiveForm,
    TOTPForm,
)
from .models import Audit, Notice, Operation, RecoveryCode, Request, Subscription, TelegramIdentity, Token
from .panel import PanelError
from .services import (
    DomainError,
    activate,
    add_duration,
    adopt_panel,
    audit,
    confirm_web,
    emergency_availability,
    import_clients,
    invitation,
    issue_token,
    live_token,
    notify,
    prepare_operation,
    rate_limit,
    recovery_codes,
    reset_password,
    setup_browser_account,
    subscription_url,
)


def bound_login(request, user):
    login(request, user, backend="core.auth.NormalizedBackend")
    request.session["auth_version"] = user.auth_version
    request.session["last_activity"] = int(time.time())
    request.session["login_at"] = int(time.time())
    request.session.set_expiry(3600 if user.is_staff else 30 * 86400)


def allowed(request, scope, key="", limit=8):
    ip = request.META.get("REMOTE_ADDR", "")  # Never trust arbitrary X-Forwarded-For.
    ip_ok = rate_limit(scope + "-ip", ip, limit=limit * 4)
    key_ok = rate_limit(scope, key or ip, limit=limit)
    return ip_ok and key_ok


def home(request):
    if request.user.is_authenticated:
        return redirect("staff" if request.user.is_staff else "dashboard")
    return render(request, "home.html")


def login_view(request):
    form = LoginForm(request.POST or None)
    if request.method == "POST":
        if not allowed(request, "login", request.POST.get("username", "").lower()):
            form.add_error(None, "Слишком много попыток. Подождите 10 минут.")
        elif form.is_valid():
            bound_login(request, form.user)
            audit(form.user, "login")
            return redirect("staff" if form.user.is_staff else "dashboard")
    return render(
        request,
        "form.html",
        {"form": form, "title": "Вход в кабинет", "button": "Войти", "recovery_link": True},
    )


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


def invite_view(request, raw):
    try:
        token = live_token(raw, "invite")
    except DomainError as exc:
        return render(
            request, "form.html", {"title": "Приглашение недоступно", "error": str(exc)}, status=400
        )
    if request.user.is_authenticated:
        return render(
            request,
            "form.html",
            {
                "title": "Сначала выйдите из текущего аккаунта",
                "error": "Каждое приглашение создаёт отдельный аккаунт.",
            },
            status=400,
        )
    form = RegisterForm(request.POST or None)
    if request.method == "POST":
        if not allowed(request, "invite", raw):
            form.add_error(None, "Слишком много попыток. Подождите 10 минут.")
        elif form.is_valid():
            try:
                user, codes = activate(raw, form.cleaned_data["username"], form.cleaned_data["password"])
                bound_login(request, user)
                request.session["new_codes"] = seal(codes)
                return redirect("codes")
            except (DomainError, ValidationError, IntegrityError) as exc:
                form.add_error(
                    None,
                    str(exc)
                    if not isinstance(exc, IntegrityError)
                    else "Логин занят или приглашение уже использовано.",
                )
    return render(
        request,
        "form.html",
        {
            "form": form,
            "title": "Ваш аккаунт HOPE",
            "button": "Создать аккаунт",
            "description": f"Подписка №{token.subscription.number}. VPN-настройки существующего доступа сохранятся.",
        },
    )


@login_required
def codes_view(request):
    codes = unseal(request.session.get("new_codes"))
    if not codes:
        return redirect("account")
    if request.method == "POST":
        request.session.pop("new_codes", None)
        return redirect("dashboard")
    return render(request, "codes.html", {"codes": codes})


def recovery_view(request):
    raw = request.GET.get("token", "")
    form = RecoveryForm(request.POST or None)
    if request.method == "POST":
        raw = request.POST.get("token", "")
        if not allowed(request, "recovery", request.POST.get("username", "").lower(), limit=5):
            form.add_error(None, "Слишком много попыток. Подождите 10 минут.")
        elif form.is_valid():
            try:
                user, codes = reset_password(
                    form.cleaned_data["username"],
                    form.cleaned_data["password"],
                    code=form.cleaned_data["code"],
                    raw=raw or None,
                )
                bound_login(request, user)
                request.session["new_codes"] = seal(codes)
                return redirect("codes")
            except (DomainError, ValidationError) as exc:
                form.add_error(None, str(exc))
    return render(
        request,
        "form.html",
        {
            "form": form,
            "title": "Восстановление доступа",
            "button": "Задать новый пароль",
            "token": raw,
            "description": "Используйте сохранённый одноразовый код. Если кодов нет, откройте /recover в привязанном боте или обратитесь в поддержку.",
        },
    )


@login_required
def dashboard(request):
    if request.user.is_staff:
        return redirect("staff")
    sub = getattr(request.user, "subscription", None)
    state = unseal(sub.snapshot) if sub and sub.snapshot else None
    emergency_error, emergency_next_at = emergency_availability(sub)
    technical = ""
    if state:
        c = state["client"]
        if c.get("totalGB", 0) and state.get("usedTraffic", 0) >= c["totalGB"]:
            technical = "Квота трафика исчерпана. Продление срока её не сбрасывает."
        elif not c.get("enable"):
            technical = "Панель показывает отключённый доступ. Обратитесь к администратору."
    return render(
        request,
        "dashboard.html",
        {
            "sub": sub,
            "technical": technical,
            "notices": request.user.notices.all()[:6],
            "history": sub.operations.filter(status="verified")[:8] if sub else [],
            "emergency_error": emergency_error,
            "emergency_next_at": emergency_next_at,
            "emergency_form": EmergencyForm(initial={"operation_id": uuid.uuid4(), "version": sub.version})
            if sub
            else None,
        },
    )


@login_required
@require_POST
def emergency_hour(request):
    sub = get_object_or_404(Subscription, owner=request.user)
    form = EmergencyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Форма устарела. Обновите страницу и повторите запрос.")
    elif not allowed(request, "emergency", request.user.pk, limit=10):
        messages.error(request, "Слишком много запросов. Повторите позже.")
    else:
        try:
            prepare_operation(
                sub.pk,
                request.user,
                kind="emergency",
                reason="",
                version=form.cleaned_data["version"],
                operation_id=form.cleaned_data["operation_id"],
            )
            messages.success(
                request,
                "Запрос экстренного часа сохранён. Дождитесь подтверждения применения в истории. "
                "Час отсчитывается от принятия запроса, не от завершения проверки.",
            )
        except (DomainError, PanelError, IntegrityError) as exc:
            messages.error(
                request, str(exc) if isinstance(exc, (DomainError, PanelError)) else "Обновите страницу."
            )
    return redirect("dashboard")


@login_required
def account(request):
    form = SensitiveForm(request.POST or None)
    if (
        request.method == "POST"
        and allowed(request, "sensitive", request.user.pk, limit=5)
        and form.is_valid()
    ):
        try:
            with transaction.atomic():
                if not request.user.check_password(form.cleaned_data["password"]):
                    raise DomainError("Проверка не пройдена.")
                code = (
                    RecoveryCode.objects.select_for_update()
                    .filter(
                        user=request.user,
                        digest=digest(form.cleaned_data["code"].strip()),
                        used_at__isnull=True,
                    )
                    .first()
                )
                if not code:
                    raise DomainError("Проверка не пройдена.")
                action = request.POST.get("action")
                if action not in ("unlink", "codes"):
                    raise DomainError("Неизвестное действие.")
                code.used_at = timezone.now()
                code.save()
                if action == "unlink":
                    TelegramIdentity.objects.filter(user=request.user).delete()
                    Token.objects.filter(user=request.user, kind="telegram").update(
                        consumed_at=timezone.now()
                    )
                    audit(request.user, "telegram_unlinked")
                    messages.success(request, "Telegram отвязан. VPN-доступ сохранён.")
                    return redirect("account")
                request.session["new_codes"] = seal(recovery_codes(request.user))
                audit(request.user, "recovery_codes_rotated")
                return redirect("codes")
        except DomainError as exc:
            form.add_error(None, str(exc))
    return render(
        request, "account.html", {"identity": getattr(request.user, "telegram", None), "form": form}
    )


@login_required
def password_view(request):
    form = PasswordForm(request.POST or None)
    if request.method == "POST":
        if not allowed(request, "password", request.user.pk, limit=5):
            form.add_error(None, "Слишком много попыток. Подождите 10 минут.")
        elif form.is_valid():
            d = form.cleaned_data
            try:
                if not request.user.check_password(d["current"]) or d["password"] != d["password_repeat"]:
                    raise DomainError("Текущий пароль неверен или новые пароли не совпадают.")
                validate_password(d["password"], request.user)
                request.user.set_password(d["password"])
                request.user.auth_version += 1
                request.user.save()
                update_session_auth_hash(request, request.user)
                request.session["auth_version"] = request.user.auth_version
                audit(request.user, "password_changed")
                notify(
                    request.user,
                    f"password:{uuid.uuid4()}",
                    "Пароль кабинета изменён; прежние сессии завершены.",
                )
                messages.success(request, "Пароль изменён.")
                return redirect("account")
            except (DomainError, ValidationError) as exc:
                form.add_error(None, str(exc))
    return render(request, "form.html", {"title": "Смена пароля", "form": form, "button": "Изменить пароль"})


@login_required
def telegram_view(request):
    if request.user.is_staff:
        return HttpResponseForbidden("Административный аккаунт нельзя связывать с клиентским ботом.")
    if request.method == "POST":
        if request.POST.get("action") == "create":
            if getattr(request.user, "telegram", None):
                messages.error(request, "Telegram уже подключён.")
            elif allowed(request, "link", request.user.pk):
                with transaction.atomic():
                    Token.objects.filter(user=request.user, kind="telegram", consumed_at__isnull=True).update(
                        consumed_at=timezone.now()
                    )
                    raw, token = issue_token(
                        "telegram", user=request.user, session=request.session.session_key
                    )
                    request.session["tg_link_raw"] = seal(raw)
                    request.session["tg_link_id"] = str(token.pk)
            return redirect("telegram")
        if request.POST.get("action") == "confirm":
            try:
                confirm_web(request.session.get("tg_link_id"), request.user, request.session.session_key)
                request.session.pop("tg_link_raw", None)
                request.session.pop("tg_link_id", None)
                messages.success(request, "Telegram подключён.")
                return redirect("account")
            except (DomainError, ValidationError) as exc:
                messages.error(request, str(exc))
    raw = unseal(request.session.get("tg_link_raw"))
    token = None
    if raw:
        try:
            token = live_token(raw, "telegram")
        except DomainError:
            raw = None
    return render(
        request,
        "telegram.html",
        {
            "token": token,
            "link": f"https://t.me/{settings.BOT_USERNAME}?start={raw}" if raw else None,
            "app_link": (
                "tg://resolve?" + urlencode({"domain": settings.BOT_USERNAME, "start": raw})
                if raw
                else None
            ),
        },
    )


@login_required
@require_POST
def reminders_view(request):
    identity = getattr(request.user, "telegram", None)
    if identity:
        identity.reminders = not identity.reminders
        identity.save()
    return redirect("account")


def setup_account(request):
    raw = request.POST.get("token", "") if request.method == "POST" else request.GET.get("token", "")
    try:
        token = live_token(raw, "onboard")
        if request.user.is_authenticated:
            raise DomainError("Сначала выйдите из текущего аккаунта, затем откройте ссылку настройки.")
        form = RegisterForm(request.POST or None)
        if (
            request.method == "POST"
            and allowed(request, "onboard", token.user_id, limit=5)
            and form.is_valid()
        ):
            user, codes = setup_browser_account(
                raw, form.cleaned_data["username"], form.cleaned_data["password"]
            )
            bound_login(request, user)
            request.session["new_codes"] = seal(codes)
            return redirect("codes")
        return render(
            request,
            "form.html",
            {
                "form": form,
                "token": raw,
                "title": "Настройте вход без Telegram",
                "button": "Создать логин и пароль",
                "description": "VPN уже принадлежит вашему Telegram-аккаунту. Выберите собственный логин и сохраните коды восстановления.",
            },
        )
    except (DomainError, ValidationError, IntegrityError) as exc:
        return render(
            request,
            "form.html",
            {
                "title": "Настройка кабинета",
                "error": str(exc)
                if isinstance(exc, DomainError)
                else "Логин занят или пароль не подходит. Повторите настройку.",
            },
            status=400,
        )


@login_required
def connect(request):
    sub = getattr(request.user, "subscription", None)
    url = subscription_url(sub)
    return render(
        request,
        "connect.html",
        {
            "sub": sub,
            "subscription_url": url,
            "happ_url": import_url("happ", url),
            "incy_url": import_url("incy", url),
            "incy_download": INCY_IOS_URL,
            "happ_download": HAPP_URL,
        },
    )


@login_required
def qr(request):
    url = subscription_url(getattr(request.user, "subscription", None))
    if not url:
        return HttpResponse(status=404)
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


def demo_subscription(request):
    return render(
        request,
        "form.html",
        {
            "title": "Демонстрационная подписка",
            "description": "Это локальная проверка интерфейса. VPN-конфигурация не выдаётся; реальный туннель требует подключения 3x-ui.",
        },
    )


def help_view(request):
    return render(request, "help.html")


def request_view(request, kind="support"):
    if kind == "renewal" and not request.user.is_authenticated:
        return redirect("login")
    form = (
        MessageForm(request.POST or None)
        if request.user.is_authenticated
        else PublicRequestForm(request.POST or None)
    )
    if request.method == "POST" and form.is_valid():
        if not allowed(request, "request", request.user.pk if request.user.is_authenticated else "", limit=5):
            form.add_error(None, "Слишком много обращений. Подождите 10 минут.")
        elif form.cleaned_data.get("website"):
            return redirect("home")
        else:
            user = request.user if request.user.is_authenticated else None
            if (
                user
                and kind == "renewal"
                and Request.objects.filter(user=user, kind=kind, status="open").exists()
            ):
                messages.info(request, "Заявка на продление уже получена. Срок изменит администратор.")
            else:
                item = Request.objects.create(
                    user=user,
                    kind=kind,
                    contact=form.cleaned_data.get("contact", ""),
                    text=form.cleaned_data["text"],
                )
                from .services import admin_event

                admin_event(
                    f"web-request:{item.pk}",
                    f"Новое обращение из веб-кабинета: {kind}, №{item.pk}. {settings.SITE_URL}/staff/requests/",
                )
                messages.success(
                    request,
                    "Обращение получено. Ответ появится в кабинете; без аккаунта — по указанному контакту.",
                )
            return redirect("requests" if user else "home")
    return render(
        request,
        "request.html",
        {
            "form": form,
            "kind": kind,
            "title": "Продление подписки" if kind == "renewal" else "Поддержка и подключение",
        },
    )


@login_required
def requests_view(request):
    return render(request, "requests.html", {"items": request.user.requests.all()[:50]})


def staff_required(view):
    @wraps(view)
    @login_required
    def inner(request, *args, **kwargs):
        if not request.user.is_staff or not request.user.is_active:
            return HttpResponseForbidden("Нет доступа.")
        if not request.user.totp_secret:
            return redirect("mfa_setup")
        if time.time() - request.session.get("mfa_at", 0) > 3600:
            return redirect("mfa")
        return view(request, *args, **kwargs)

    return inner


@login_required
@require_GET
def mfa_setup_qr(request):
    if not request.user.is_staff or not request.user.is_active:
        return HttpResponseForbidden("Нет доступа.")
    encrypted = request.session.get("totp_setup")
    if request.user.totp_secret or not encrypted:
        return HttpResponse(status=404)
    secret = unseal(encrypted)
    uri = pyotp.TOTP(secret).provisioning_uri(name=request.user.username, issuer_name="HOPE VPN")
    output = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, border=4).save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


@login_required
def mfa_view(request, setup=False):
    if not request.user.is_staff:
        return HttpResponseForbidden("Нет доступа.")
    if request.user.totp_secret and setup:
        return redirect("mfa")
    if not request.user.totp_secret and not setup:
        return redirect("mfa_setup")
    form = TOTPForm(request.POST or None)
    if request.user.totp_secret:
        secret = unseal(request.user.totp_secret)
    else:
        if not request.session.get("totp_setup"):
            request.session["totp_setup"] = seal(pyotp.random_base32())
        secret = unseal(request.session["totp_setup"])
    if request.method == "POST":
        if not allowed(request, "mfa", request.user.pk, limit=5):
            form.add_error(None, "Слишком много попыток. Подождите 10 минут.")
        elif form.is_valid():
            totp = pyotp.TOTP(secret)
            step = int(time.time()) // 30
            from .models import User

            with transaction.atomic():
                owner = User.objects.select_for_update().get(pk=request.user.pk)
                same_secret = not owner.totp_secret or unseal(owner.totp_secret) == secret
                if same_secret and totp.verify(form.cleaned_data["code"]) and step > owner.totp_last_step:
                    owner.totp_secret = seal(secret)
                    owner.totp_last_step = step
                    owner.save()
                    request.session.pop("totp_setup", None)
                    request.session["mfa_at"] = time.time()
                    audit(owner, "mfa_verified")
                    return redirect("staff")
            form.add_error("code", "Код неверен или уже использован. Дождитесь нового кода.")
    return render(request, "mfa.html", {"form": form, "setup": setup, "secret": secret if setup else None})


@staff_required
def staff(request):
    query = request.GET.get("q", "").strip()
    subs = Subscription.objects.select_related("owner").all().order_by("-created_at")
    if query:
        subs = subs.filter(
            Q(label__icontains=query)
            | Q(panel_comment__icontains=query)
            | Q(panel_email__icontains=query)
            | Q(owner__username__icontains=query)
            | Q(owner__telegram__display_name__icontains=query)
        )
    filter_value = request.GET.get("filter", "")
    if filter_value == "unlinked":
        subs = subs.filter(Q(owner__isnull=True) | Q(owner__telegram__isnull=True))
    elif filter_value == "expired":
        subs = subs.filter(expires_at__lte=timezone.now())
    elif filter_value == "soon":
        subs = subs.filter(expires_at__gt=timezone.now(), expires_at__lte=timezone.now() + timedelta(days=7))
    elif filter_value == "errors":
        subs = subs.filter(sync_status__in=["partial", "conflict", "failed"])
    return render(
        request,
        "staff/index.html",
        {
            "subs": subs[:200],
            "query": query,
            "open_requests": Request.objects.filter(status="open").count(),
            "problems": Operation.objects.filter(status__in=["partial", "conflict", "failed"]).count(),
            "queued": Operation.objects.filter(status__in=["queued", "applying"]).count(),
        },
    )


@staff_required
@require_POST
def staff_import(request):
    try:
        count = import_clients(request.user)
        messages.success(
            request, f"Импорт завершён. Новых записей: {count}. Существующие VPN-настройки не изменены."
        )
    except (PanelError, DomainError) as exc:
        messages.error(request, str(exc))
    return redirect("staff")


@staff_required
def staff_new(request):
    form = MessageForm(request.POST or None)
    form.fields["text"].label = "Имя клиента (для вас)"
    form.fields["text"].max_length = 120
    if request.method == "POST" and form.is_valid():
        sub = Subscription.objects.create(
            label=form.cleaned_data["text"][:120], panel_email="hope-" + uuid.uuid4().hex[:16]
        )
        audit(request.user, "subscription_draft", sub.number)
        return redirect("staff_detail", pk=sub.pk)
    return render(
        request,
        "form.html",
        {
            "form": form,
            "title": "Новый клиент",
            "button": "Создать черновик",
            "description": "Сначала выдайте приглашение; после активации аккаунта создайте VPN-доступ с выбранными сроком и лимитами.",
        },
    )


@staff_required
def staff_detail(request, pk):
    sub = get_object_or_404(Subscription, pk=pk)
    invite_link = reset_link = None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "invite":
                if not request.POST.get("verified_contact"):
                    raise DomainError("Подтвердите проверку владельца по прежнему контакту.")
                invite_link = settings.SITE_URL + "/invite/" + invitation(sub, request.user) + "/"
            elif action == "adopt":
                adopt_panel(sub, request.user, managed=True)
                messages.success(request, "Состояние панели принято под управление.")
                return redirect("staff_detail", pk=pk)
            elif action == "refresh":
                adopt_panel(sub, request.user)
                messages.success(request, "Состояние панели принято; незавершённые результаты закрыты.")
                return redirect("staff_detail", pk=pk)
            elif action == "exclude":
                if sub.owner_id or sub.operations.exists():
                    raise DomainError("Запись уже обслуживается. Исключение требует отдельного разбора.")
                sub.excluded = not sub.excluded
                sub.save()
                audit(request.user, "subscription_exclusion", sub.number)
                return redirect("staff_detail", pk=pk)
            elif action == "reset" and sub.owner_id and not sub.owner.is_staff:
                if not request.POST.get("verified_contact"):
                    raise DomainError("Проверьте владельца через прежний контакт.")
                Token.objects.filter(user=sub.owner, kind="reset", consumed_at__isnull=True).update(
                    consumed_at=timezone.now()
                )
                raw, _ = issue_token("reset", user=sub.owner, minutes=30)
                reset_link = settings.SITE_URL + "/recover/?token=" + raw
                audit(request.user, "manual_recovery_issued", sub.number)
            elif action == "unlink" and sub.owner_id and not sub.owner.is_staff:
                if not request.POST.get("verified_contact"):
                    raise DomainError("Проверьте владельца через прежний контакт.")
                TelegramIdentity.objects.filter(user=sub.owner).delete()
                Token.objects.filter(user=sub.owner, consumed_at__isnull=True).update(
                    consumed_at=timezone.now()
                )
                sub.owner.auth_version += 1
                sub.owner.save()
                audit(request.user, "manual_telegram_unlinked", sub.number)
                messages.success(
                    request, "Telegram отвязан после проверки владельца. Прежние сессии завершены."
                )
                return redirect("staff_detail", pk=pk)
            elif action == "lock" and sub.owner_id:
                sub.owner.is_active = not sub.owner.is_active
                sub.owner.auth_version += 1
                sub.owner.save()
                audit(request.user, "account_lock_toggled", sub.number)
                return redirect("staff_detail", pk=pk)
        except (DomainError, PanelError) as exc:
            messages.error(request, str(exc))
    state = unseal(sub.snapshot) if sub.snapshot else None
    return render(
        request,
        "staff/detail.html",
        {
            "sub": sub,
            "state": state,
            "invite_link": invite_link,
            "reset_link": reset_link,
            "operations": sub.operations.all()[:30],
        },
    )


@staff_required
def staff_operation(request, pk):
    sub = get_object_or_404(Subscription, pk=pk)
    form = OperationForm(request.POST or None, initial={"version": sub.version})
    if request.method == "POST" and request.POST.get("preview"):
        try:
            data = signing.loads(request.POST["preview"], salt="operation", max_age=600)
            if data["sub"] != str(pk) or data["actor"] != request.user.pk:
                raise DomainError("Неверное подтверждение.")
            kwargs = data["kwargs"]
            if kwargs.get("exact"):
                kwargs["exact"] = datetime.fromisoformat(kwargs["exact"])
            prepare_operation(pk, request.user, operation_id=uuid.UUID(data["id"]), **kwargs)
            messages.success(request, "Операция сохранена. Фоновый worker проверит применение в панели.")
            return redirect("staff_detail", pk=pk)
        except (signing.BadSignature, DomainError, PanelError, ValueError, IntegrityError) as exc:
            messages.error(
                request,
                str(exc)
                if isinstance(exc, (DomainError, PanelError))
                else "Подтверждение истекло или операция уже выполняется.",
            )
            return redirect("staff_operation", pk=pk)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        kind = d["kind"]
        end = d["exact"] if kind == "set_expiry" else None
        if kind in ("renew", "create"):
            base = (
                max(timezone.now(), sub.expires_at) if kind == "renew" and sub.expires_at else timezone.now()
            )
            end = add_duration(base, d.get("days") or 0, d.get("months") or 0)
        kwargs = {
            "kind": kind,
            "reason": d["reason"],
            "version": d["version"],
            "days": d.get("days") or 0,
            "months": d.get("months") or 0,
            "exact": end.isoformat() if end else None,
            "total_bytes": (d.get("total_gib") or 0) * 1024**3,
            "limit_devices": d.get("devices") or 0,
            "reenable": d["reenable"],
        }
        data = {"sub": str(pk), "actor": request.user.pk, "kwargs": kwargs, "id": str(uuid.uuid4())}
        return render(
            request,
            "staff/preview.html",
            {
                "sub": sub,
                "end": end,
                "kind": dict(form.fields["kind"].choices)[kind],
                "reason": d["reason"],
                "preview": signing.dumps(data, salt="operation"),
                "reenable": d["reenable"],
            },
        )
    return render(
        request,
        "form.html",
        {
            "title": f"Изменить подписку №{sub.number}",
            "form": form,
            "button": "Предпросмотр",
            "description": "Срок не сбрасывает трафик и не снимает блокировку автоматически. Изменения панели могут повлиять на соединения.",
        },
    )


@staff_required
def staff_requests(request):
    if request.method == "POST":
        item = get_object_or_404(Request, pk=request.POST.get("id"))
        reply = request.POST.get("reply", "").strip()[:2000]
        if reply:
            item.reply, item.status = reply, "closed"
            item.save()
            audit(request.user, "request_replied", item.pk)
            from .services import admin_event

            admin_event(
                f"request-replied:{item.pk}:{uuid.uuid4()}",
                f"Обращение №{item.pk} закрыто, ответ подготовлен. {settings.SITE_URL}/staff/requests/",
            )
            if item.telegram_contact_id:
                from .orders import contact_notice

                contact_notice(
                    item.telegram_contact, f"reply:{item.pk}:{uuid.uuid4()}", "Ответ поддержки: " + reply
                )
            elif item.user_id:
                notify(item.user, f"reply:{item.pk}:{uuid.uuid4()}", "Ответ поддержки: " + reply)
            messages.success(request, "Ответ сохранён. Для гостя свяжитесь по указанному контакту вручную.")
        else:
            messages.error(request, "Введите ответ.")
        return redirect("staff_requests")
    return render(
        request, "staff/requests.html", {"items": Request.objects.select_related("user").all()[:100]}
    )


@staff_required
def staff_orders(request):
    from .models import PurchaseOrder

    orders = PurchaseOrder.objects.select_related("contact", "user", "subscription", "operation")
    state = request.GET.get("status", "")
    if state in (
        "awaiting_payment",
        "payment_review",
        "paid",
        "provisioning",
        "fulfilled",
        "cancelled",
        "rejected",
    ):
        orders = orders.filter(status=state)
    return render(request, "staff/orders.html", {"orders": orders[:100]})


@staff_required
def staff_order(request, pk):
    from .models import PurchaseOrder
    from .orders import contact_notice, prepare_order, review_order

    order = get_object_or_404(
        PurchaseOrder.objects.select_related("contact", "user", "subscription", "operation"), pk=pk
    )
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action in ("confirm", "reject"):
                if not request.POST.get("checked_transfer"):
                    raise DomainError("Подтвердите ручную проверку перевода.")
                review_order(pk, request.user, action, request.POST.get("note", ""))
                messages.success(
                    request, "Результат ручной проверки сохранён. Готовность VPN проверяется отдельно."
                )
            elif action == "reply":
                text = request.POST.get("reply", "").strip()
                if not text or len(text) > 2000:
                    raise DomainError("Введите сообщение длиной до 2000 символов.")
                reply_id = uuid.UUID(request.POST.get("reply_id", ""))
                contact_notice(
                    order.contact,
                    f"purchase-reply:{pk}:{reply_id}",
                    f"Заказ №{order.number}. Сообщение администратора:\n" + text,
                )
                audit(request.user, "purchase_reply", order.number)
                messages.success(request, "Сообщение поставлено в очередь Telegram.")
            elif action == "preview":
                if order.status != "paid":
                    raise DomainError("Сначала подтвердите оплату.")
                data = {
                    "order": str(pk),
                    "actor": request.user.pk,
                    "version": order.subscription.version if order.subscription else None,
                    "reenable": bool(request.POST.get("reenable")),
                    "total_bytes": int(request.POST.get("total_gib", "0")) * 1024**3,
                    "limit_devices": int(request.POST.get("devices", "0")),
                    "exact": (
                        add_duration(
                            max(timezone.now(), order.subscription.expires_at),
                            days=order.days,
                            months=order.months,
                        ).isoformat()
                        if order.kind == "renewal" and order.subscription and order.subscription.expires_at
                        else None
                    ),
                }
                if not 0 <= data["total_bytes"] <= 100000 * 1024**3 or not 0 <= data["limit_devices"] <= 100:
                    raise DomainError("Некорректные лимиты.")
                return render(
                    request,
                    "staff/order-preview.html",
                    {
                        "order": order,
                        "data": data,
                        "end": datetime.fromisoformat(data["exact"]) if data["exact"] else None,
                        "preview": signing.dumps(data, salt="purchase-provision"),
                    },
                )
            elif action == "provision":
                data = signing.loads(request.POST.get("preview", ""), salt="purchase-provision", max_age=600)
                if data.pop("order") != str(pk) or data.pop("actor") != request.user.pk:
                    raise DomainError("Неверное подтверждение.")
                if data.get("exact"):
                    data["exact"] = datetime.fromisoformat(data["exact"])
                prepare_order(pk, request.user, **data)
                messages.success(
                    request, "Выдача сохранена. О готовности клиенту сообщим после проверки панели."
                )
            else:
                raise DomainError("Неизвестное действие.")
        except (DomainError, PanelError, ValueError, IntegrityError, signing.BadSignature) as exc:
            messages.error(
                request,
                str(exc)
                if isinstance(exc, (DomainError, PanelError))
                else "Подтверждение устарело или данные некорректны. Обновите карточку.",
            )
        return redirect("staff_order", pk=pk)
    return render(request, "staff/order.html", {"order": order, "reply_id": uuid.uuid4()})


@staff_required
def staff_events(request):
    return render(
        request,
        "staff/events.html",
        {
            "operations": Operation.objects.select_related("subscription").all()[:100],
            "audits": Audit.objects.select_related("actor").order_by("-created_at")[:100],
            "notices": Notice.objects.select_related("user").all()[:100],
        },
    )


@staff_required
@require_POST
def retry_verify(request, pk):
    op = get_object_or_404(Operation, pk=pk, status__in=["partial", "conflict", "failed"])
    # Existing write intent is kept; retry only verifies, never blindly repeats a write.
    op.status = "queued"
    op.lease_until = None
    op.available_at = timezone.now()
    op.save()
    op.subscription.sync_status = "queued"
    op.subscription.save()
    audit(request.user, "operation_recheck", op.id)
    return redirect("staff_detail", pk=op.subscription_id)


def health(request):
    return JsonResponse({"status": "ok"})


@staff_required
def staff_broadcast(request):
    form = MessageForm(request.POST or None)
    if request.method == "POST" and request.POST.get("preview"):
        try:
            data = signing.loads(request.POST["preview"], salt="broadcast", max_age=600)
            if data["actor"] != request.user.pk:
                raise DomainError("Подтверждение принадлежит другому администратору.")
            from .models import User

            users = User.objects.filter(pk__in=data["users"], is_active=True, is_staff=False)
            for user in users:
                notify(user, f"broadcast:{data['id']}:{user.pk}", data["text"])
            audit(request.user, "broadcast_queued", data["id"])
            messages.success(
                request,
                f"Сообщение сохранено для {users.count()} аккаунтов. Telegram-доставка зависит от доступности бота.",
            )
            return redirect("staff_events")
        except (signing.BadSignature, DomainError) as exc:
            form.add_error(None, str(exc) if isinstance(exc, DomainError) else "Подтверждение истекло.")
    elif request.method == "POST" and form.is_valid():
        from .models import User

        users = User.objects.filter(is_active=True, is_staff=False, subscription__excluded=False)
        group = request.POST.get("group", "active")
        if group == "active":
            users = users.filter(
                Q(subscription__expires_at__gt=timezone.now()) | Q(subscription__expiry_kind="perpetual")
            )
        elif group == "soon":
            users = users.filter(
                subscription__expires_at__gt=timezone.now(),
                subscription__expires_at__lte=timezone.now() + timedelta(days=7),
            )
        elif group != "all":
            form.add_error(None, "Неизвестная группа.")
            return render(request, "staff/broadcast.html", {"form": form})
        ids = list(users.values_list("pk", flat=True))[:500]
        data = {
            "actor": request.user.pk,
            "users": ids,
            "text": form.cleaned_data["text"],
            "id": str(uuid.uuid4()),
        }
        return render(
            request,
            "staff/broadcast.html",
            {
                "preview": signing.dumps(data, salt="broadcast"),
                "text": data["text"],
                "recipients": users,
                "count": len(ids),
                "telegram_count": users.filter(telegram__reachable=True).count(),
            },
        )
    return render(request, "staff/broadcast.html", {"form": form})
