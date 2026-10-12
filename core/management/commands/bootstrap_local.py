import os
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.crypto import seal
from core.models import DemoClient, Subscription, User
from core.panel import new_state
from core.services import import_clients


class Command(BaseCommand):
    help = "Initialize local demo accounts; passwords are read from .env, never printed."

    def handle(self, **options):
        if settings.PANEL_MODE != "demo" or not settings.DEBUG:
            raise CommandError("bootstrap_local is allowed only in local demo mode.")
        for key, default, staff in [("ADMIN", "admin", True), ("DEMO", "demo", False)]:
            username = os.getenv(f"LOCAL_{key}_LOGIN", default)
            password = os.getenv(f"LOCAL_{key}_PASSWORD")
            if not password:
                raise CommandError(f"Set LOCAL_{key}_PASSWORD in .env")
            user, created = User.objects.get_or_create(
                username=username, defaults={"is_staff": staff, "is_superuser": staff}
            )
            if created:
                user.set_password(password)
                user.save()
        for email, days in [("demo-client", 30), ("existing-client", 12)]:
            state = new_state(email, int((timezone.now() + timedelta(days=days)).timestamp() * 1000))
            DemoClient.objects.get_or_create(email=email, defaults={"data": seal(state)})
        import_clients()
        sub = Subscription.objects.get(panel_email="demo-client")
        if not sub.owner_id:
            sub.owner = User.objects.get(username=os.getenv("LOCAL_DEMO_LOGIN", "demo"))
            sub.managed, sub.sync_status = True, "verified"
            sub.save()
        self.stdout.write("Local demo initialized. Account credentials are in .env.")
