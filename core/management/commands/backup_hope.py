import io
import os
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.crypto import seal


class Command(BaseCommand):
    help = "Write an encrypted backup; retain DATA_ENCRYPTION_KEY separately."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, **options):
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = io.StringIO()
        call_command(
            "dumpdata", "core", indent=None, stdout=data, exclude=["core.ratebucket", "core.workerlock"]
        )
        try:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise CommandError("Refusing to overwrite a backup.") from None
        with os.fdopen(fd, "w") as file:
            file.write(seal({"format": 1, "fixture": data.getvalue()}))
        self.stdout.write("Encrypted backup saved.")
