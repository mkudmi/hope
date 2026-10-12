import time

from django.contrib.auth import logout


class SessionGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(time.time())
            limit = 3600 if request.user.is_staff else 7 * 86400
            if (
                request.session.get("auth_version") != request.user.auth_version
                or now - request.session.get("last_activity", now) > limit
                or now - request.session.get("login_at", now)
                > (3600 if request.user.is_staff else 30 * 86400)
            ):
                logout(request)
            else:
                request.session["last_activity"] = now
        return self.get_response(request)


class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path_info.startswith("/static/"):
            response["Cache-Control"] = "no-store, private"
        # Same-origin forms retain a valid Origin for CSRF; external links receive no referrer.
        response["Referrer-Policy"] = "same-origin"
        response["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response
