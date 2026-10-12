from django.core.management.base import BaseCommand, CommandError

from core.panel import PanelError
from core.services import DomainError, import_clients


class Command(BaseCommand):
    help = "Read-only import of panel clients; never writes to 3x-ui."

    def handle(self, **options):
        try:
            self.stdout.write(f"New subscription records: {import_clients()}")
        except (DomainError, PanelError) as exc:
            raise CommandError(str(exc)) from None
