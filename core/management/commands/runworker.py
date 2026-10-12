import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.jobs import run_tick


class Command(BaseCommand):
    help = "Apply manual operations and dispatch reminders; --once for a single tick."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, **options):
        while True:
            close_old_connections()
            run_tick()
            if options["once"]:
                return
            time.sleep(5)
