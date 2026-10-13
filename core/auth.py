from django.contrib.auth.backends import ModelBackend

TRUSTED_BROWSER_COOKIE = "hope_trusted_browser"


class NormalizedBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        return super().authenticate(
            request, username=(username or "").strip().lower(), password=password, **kwargs
        )
