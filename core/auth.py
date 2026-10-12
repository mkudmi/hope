from django.contrib.auth.backends import ModelBackend


class NormalizedBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        return super().authenticate(
            request, username=(username or "").strip().lower(), password=password, **kwargs
        )
