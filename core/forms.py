from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import User


class BaseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class EmergencyForm(BaseForm):
    operation_id = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class LoginForm(BaseForm):
    username = forms.CharField(
        label="Логин", max_length=32, widget=forms.TextInput(attrs={"autocomplete": "username"})
    )
    password = forms.CharField(
        label="Пароль",
        strip=False,
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def clean(self):
        data = super().clean()
        self.user = authenticate(username=data.get("username"), password=data.get("password"))
        if not self.user:
            raise ValidationError("Неверный логин или пароль.")
        return data


class RegisterForm(BaseForm):
    username = forms.RegexField(
        r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{3,31}$",
        label="Придумайте логин",
        max_length=32,
        help_text="4–32 символа: латиница, цифры, точка, _ или -.",
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Придумайте пароль",
        strip=False,
        min_length=12,
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password_repeat = forms.CharField(
        label="Повторите пароль", strip=False, max_length=128, widget=forms.PasswordInput
    )

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_repeat"):
            self.add_error("password_repeat", "Пароли не совпадают.")
        if data.get("password"):
            validate_password(data["password"], User(username=data.get("username", "").lower()))
        return data

    def clean_username(self):
        value = self.cleaned_data["username"].lower()
        if User.objects.filter(username=value).exists():
            raise ValidationError("Этот логин занят.")
        return value


class RecoveryForm(BaseForm):
    username = forms.CharField(label="Логин", max_length=32)
    code = forms.CharField(label="Код восстановления", max_length=100, required=False)
    password = forms.CharField(
        label="Новый пароль", strip=False, min_length=12, max_length=128, widget=forms.PasswordInput
    )
    password_repeat = forms.CharField(
        label="Повторите пароль", strip=False, max_length=128, widget=forms.PasswordInput
    )

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_repeat"):
            self.add_error("password_repeat", "Пароли не совпадают.")
        return data


class PasswordForm(BaseForm):
    current = forms.CharField(label="Текущий пароль", strip=False, max_length=128, widget=forms.PasswordInput)
    password = forms.CharField(
        label="Новый пароль", strip=False, min_length=12, max_length=128, widget=forms.PasswordInput
    )
    password_repeat = forms.CharField(
        label="Повторите пароль", strip=False, max_length=128, widget=forms.PasswordInput
    )


class SensitiveForm(BaseForm):
    password = forms.CharField(
        label="Пароль кабинета", strip=False, max_length=128, widget=forms.PasswordInput
    )
    code = forms.CharField(label="Одноразовый код восстановления", max_length=100)


class MessageForm(BaseForm):
    text = forms.CharField(label="Сообщение", max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))


class PublicRequestForm(MessageForm):
    contact = forms.CharField(
        label="Как с вами связаться вне Telegram",
        max_length=150,
        help_text="Например, email или известный администратору контакт.",
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)  # honeypot


class OperationForm(BaseForm):
    kind = forms.ChoiceField(
        label="Действие",
        choices=[
            ("renew", "Продлить"),
            ("set_expiry", "Установить точную дату"),
            ("create", "Создать VPN-доступ"),
            ("disable", "Заблокировать VPN"),
            ("enable", "Разблокировать VPN"),
        ],
    )
    days = forms.IntegerField(label="Дни", min_value=0, max_value=3650, initial=0, required=False)
    months = forms.IntegerField(
        label="Календарные месяцы", min_value=0, max_value=120, initial=1, required=False
    )
    exact = forms.DateTimeField(
        label="Точная дата и время (МСК)",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    total_gib = forms.IntegerField(
        label="Квота GiB (только для нового доступа; 0 — без лимита)",
        min_value=0,
        max_value=100000,
        initial=0,
        required=False,
    )
    devices = forms.IntegerField(
        label="Лимит HWID (только новый доступ; 0 — без лимита)",
        min_value=0,
        max_value=100,
        initial=0,
        required=False,
    )
    reenable = forms.BooleanField(
        label="Явно включить доступ при продлении (квота проверяется)", required=False
    )
    reason = forms.CharField(label="Основание", max_length=300, widget=forms.Textarea(attrs={"rows": 2}))
    version = forms.IntegerField(widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind in ("renew", "create") and not (data.get("days") or data.get("months")):
            raise ValidationError("Выберите положительную длительность.")
        if kind == "set_expiry" and not data.get("exact"):
            self.add_error("exact", "Укажите точную дату.")
        return data


class TOTPForm(BaseForm):
    code = forms.RegexField(
        r"^\d{6}$",
        label="Код из приложения-аутентификатора",
        help_text="Введите 6 цифр из приложения. Код обновляется каждые 30 секунд; он не приходит в Telegram или SMS.",
        error_messages={
            "invalid": "Введите шестизначный код из приложения-аутентификатора.",
            "required": "Введите код, который сейчас показывает приложение-аутентификатор.",
        },
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "maxlength": "6",
                "pattern": "[0-9]{6}",
                "placeholder": "123456",
            }
        ),
    )
