"""``manage.py bucli_sync <pk> [<pk> ...]``, ``--all`` or ``--discover``.

Reads each account's subscription again from Bucli and stores it: for missed
webhooks or a support case. Accounts are the SaaS's (``BUCLI_ACCOUNT_MODEL``)
primary keys. ``--discover`` walks every Bucli subscription and links those
whose ``metadata.bucli_account`` names an account (after ``import_subscriptions``).
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from bucli.contrib.django import conf, services
from bucli.contrib.django.models import BillingAccount
from bucli.errors import BucliError


class Command(BaseCommand):
    help = "Vuelve a copiar desde Bucli la suscripción de una o varias cuentas."

    def add_arguments(self, parser):
        parser.add_argument("accounts", nargs="*", help="pk de las cuentas (BUCLI_ACCOUNT_MODEL).")
        parser.add_argument("--all", action="store_true", help="Todas las cuentas con cliente en Bucli.")
        parser.add_argument(
            "--discover",
            action="store_true",
            help="Recorre las suscripciones de Bucli y vincula las que traen metadata.bucli_account.",
        )

    def handle(self, *args, **options):
        if options["discover"]:
            try:
                counts = services.discover()
            except BucliError as error:
                raise CommandError(f"No se pudo leer Bucli: {error}") from error
            self.stdout.write(
                f"Suscripciones vistas: {counts['seen']} · vinculadas: {counts['linked']} · "
                f"saltadas: {counts['skipped']}"
            )
            return
        if options["all"] == bool(options["accounts"]):
            raise CommandError("Indica las cuentas, --all o --discover.")
        if options["all"]:
            targets = list(BillingAccount.objects.exclude(bucli_customer_id="").select_related("account"))
        else:
            model = apps.get_model(conf.account_model_label())
            targets = []
            for pk in options["accounts"]:
                account = model._default_manager.filter(pk=pk).first()
                if account is None:
                    self.stderr.write(f"! {pk}: no existe esa cuenta.")
                    continue
                targets.append(account)
        synced = failed = 0
        for target in targets:
            label = getattr(target, "account", target)
            try:
                billing = services.sync(target)
            except BucliError as error:
                failed += 1
                self.stderr.write(f"! {label}: {error}")
                continue
            synced += 1
            self.stdout.write(f"+ {label}: {billing.plan or '-'} · {billing.status or 'sin suscripción'}")
        self.stdout.write(f"Sincronizadas: {synced} · errores: {failed}")
        if failed:
            raise CommandError(f"{failed} cuenta(s) no se pudieron sincronizar.")
