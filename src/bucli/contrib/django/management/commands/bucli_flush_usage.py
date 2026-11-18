"""``manage.py bucli_flush_usage`` — report to Bucli the metered units let
through while it did not answer (``on_unavailable: allow``). Run it every
minute (cron or your task queue); what fails stays queued."""

from django.core.management.base import BaseCommand

from bucli.contrib.django.metering import guard


class Command(BaseCommand):
    help = "Reporta a Bucli los consumos que pasaron mientras no respondía."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500, help="Cuántos reportar en esta corrida.")

    def handle(self, *args, **options):
        counts = guard().flush(limit=options["limit"])
        self.stdout.write(f"Reportados: {counts['reported']} · fallidos: {counts['failed']}")
