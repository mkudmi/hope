from datetime import timedelta
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from core.crypto import unseal
from core.models import Operation, OrderApproval, Subscription, Token, User, WorkerLock


class Command(BaseCommand):
    help = "Restore only into an empty migrated database; panel writes remain paused."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)
        parser.add_argument("--confirm-restoration", action="store_true")

    def handle(self, **options):
        if not options["confirm_restoration"] or any(
            model.objects.exists() for model in apps.get_app_config("core").get_models()
        ):
            raise CommandError("An empty database and --confirm-restoration are required.")
        data = unseal(Path(options["input"]).read_text())
        if data["format"] != 1:
            raise CommandError("Unknown backup format.")
        with transaction.atomic():
            for item in serializers.deserialize("json", data["fixture"]):
                item.save()
            with connection.cursor() as cursor:
                for sql in connection.ops.sequence_reset_sql(
                    no_style(), apps.get_app_config("core").get_models()
                ):
                    cursor.execute(sql)
            User.objects.update(auth_version=F("auth_version") + 1)
            Token.objects.filter(consumed_at__isnull=True).update(consumed_at=timezone.now())
            OrderApproval.objects.filter(consumed_at__isnull=True).update(expires_at=timezone.now())
            # Restoration must not replay old queued commands against newer real subscriptions.
            Operation.objects.filter(status__in=["queued", "applying"]).update(
                status="conflict",
                lease_until=None,
                error="После восстановления требуется ручная сверка панели.",
            )
            Subscription.objects.filter(managed=True).update(sync_status="conflict")
            WorkerLock.objects.create(
                name="write-pause", owner="restore", until=timezone.now() + timedelta(days=36500)
            )
        self.stdout.write("Restored. Panel writes paused: reconcile each account before resuming.")
