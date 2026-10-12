import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import User


class Command(BaseCommand):
    help = "Create the local owner in a fresh application DB; does not create or modify panel clients."

    def handle(self, **options):
        if not settings.DEBUG:
            raise CommandError("Use createsuperuser for production provisioning.")
        username = os.getenv("LOCAL_ADMIN_LOGIN", "admin").lower()
        password = os.getenv("LOCAL_ADMIN_PASSWORD", "")
        if not password:
            raise CommandError("Set LOCAL_ADMIN_PASSWORD in .env")
        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username, password=password)
        self.stdout.write("Local owner ready. Credentials are in .env; first login requires TOTP setup.")
