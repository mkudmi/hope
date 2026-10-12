"""Verified download destinations and documented import schemes; no tracking redirects."""

INCY_IOS_URL = "https://apps.apple.com/ru/app/incy/id6756943388"
INCY_URL = "https://incy.cc/"
HAPP_URL = "https://happ.info/"
PLATFORMS = {"android": "Android", "ios": "iPhone / iPad", "windows": "Windows", "macos": "macOS"}


def client_info(app, platform):
    if app not in ("incy", "happ") or platform not in PLATFORMS:
        return None
    return {
        "name": "INCY" if app == "incy" else "Happ",
        "download": INCY_IOS_URL
        if app == "incy" and platform == "ios"
        else INCY_URL
        if app == "incy"
        else HAPP_URL,
        "platform": PLATFORMS[platform],
    }


def import_url(app, subscription_url):
    if not subscription_url or app not in ("incy", "happ"):
        return None
    return ("incy://import/" if app == "incy" else "happ://add/") + subscription_url
