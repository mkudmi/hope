from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Operation, Subscription, WorkerLock


class Command(BaseCommand):
    help = "Pause writes, or resume after all restore/partial conflicts are resolved."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["pause", "resume"])

    def handle(self, **options):
        if options["action"] == "pause":
            WorkerLock.objects.update_or_create(
                name="write-pause",
                defaults={"owner": "manual", "until": timezone.now() + timedelta(days=36500)},
            )
            self.stdout.write("Writes paused. Bot and portal remain available.")
        else:
            if (
                Operation.objects.filter(status__in=["partial", "conflict"]).exists()
                or Subscription.objects.filter(sync_status="conflict").exists()
            ):
                raise CommandError("Resolve conflicts before resuming writes.")
            WorkerLock.objects.filter(name="write-pause").delete()
            self.stdout.write("Queue resumed; .env write flag is still enforced.")
