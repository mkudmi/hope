from django.conf import settings


def settings_context(request):
    return {
        "demo_mode": settings.PANEL_MODE == "demo",
        "panel_writes_enabled": settings.PANEL_WRITES_ENABLED,
        "support_contact": settings.SUPPORT_CONTACT,
        "bot_username": settings.BOT_USERNAME,
        "renewal_instructions": settings.RENEWAL_INSTRUCTIONS,
    }
