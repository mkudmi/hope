from django.core.management.base import BaseCommand, CommandError

from core.models import User
from core.services import audit


class Command(BaseCommand):
    help = "Administrative recovery from trusted server console; revokes all owner sessions."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--confirm-reset", action="store_true")

    def handle(self, **options):
        user = User.objects.filter(username=options["username"].lower(), is_staff=True).first()
        if not user or not options["confirm_reset"]:
            raise CommandError("A staff username and --confirm-reset are required.")
        user.totp_secret = ""
        user.totp_last_step = -1
        user.auth_version += 1
        user.save()
        audit(user, "console_mfa_reset")
        self.stdout.write("MFA reset. Log in with the password and enroll a new authenticator.")
