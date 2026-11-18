from django.db import migrations, models
from django.db.models import deletion

from bucli.contrib.django.conf import account_model_label

#: ``BUCLI_ACCOUNT_MODEL`` (the SaaS model that owns a subscription), read when
#: the migration loads, as ``AUTH_USER_MODEL`` is. Do not change it once migrated.
ACCOUNT_MODEL = account_model_label()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(ACCOUNT_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "bucli_customer_id",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64, verbose_name="Cliente en Bucli"
                    ),
                ),
                (
                    "bucli_subscription_id",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64, verbose_name="Suscripción en Bucli"
                    ),
                ),
                ("price_id", models.CharField(blank=True, default="", max_length=64, verbose_name="Precio del plan")),
                ("plan", models.CharField(blank=True, default="", max_length=64, verbose_name="Plan")),
                (
                    "pending_price_id",
                    models.CharField(
                        blank=True, default="", max_length=64, verbose_name="Precio desde el próximo ciclo"
                    ),
                ),
                ("status", models.CharField(blank=True, default="", max_length=32, verbose_name="Estado")),
                ("current_period_end", models.DateTimeField(blank=True, null=True, verbose_name="Fin del periodo")),
                ("trial_end", models.DateTimeField(blank=True, null=True, verbose_name="Fin de la prueba")),
                (
                    "cancel_at_period_end",
                    models.BooleanField(default=False, verbose_name="Se cancela al fin del periodo"),
                ),
                ("items", models.JSONField(blank=True, default=list, verbose_name="Ítems")),
                ("livemode", models.BooleanField(default=False, verbose_name="Producción")),
                ("last_event_sequence", models.BigIntegerField(default=0, verbose_name="Último sequence aplicado")),
                ("last_event_at", models.DateTimeField(blank=True, null=True, verbose_name="Último evento aplicado")),
                (
                    "last_synced_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Última copia desde Bucli"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "account",
                    models.OneToOneField(
                        on_delete=deletion.CASCADE,
                        related_name="bucli_billing",
                        to=ACCOUNT_MODEL,
                        verbose_name="Cuenta",
                    ),
                ),
            ],
            options={
                "verbose_name": "Cuenta de cobro (Bucli)",
                "verbose_name_plural": "Cuentas de cobro (Bucli)",
            },
        ),
        migrations.CreateModel(
            name="ProcessedEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=64, unique=True, verbose_name="Evento")),
                ("type", models.CharField(max_length=100, verbose_name="Tipo")),
                ("sequence", models.BigIntegerField(blank=True, null=True, verbose_name="Sequence")),
                ("livemode", models.BooleanField(default=False, verbose_name="Producción")),
                ("outcome", models.CharField(blank=True, default="", max_length=32, verbose_name="Resultado")),
                ("received_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Recibido")),
                (
                    "billing_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=deletion.SET_NULL,
                        related_name="processed_events",
                        to="bucli_billing.billingaccount",
                    ),
                ),
            ],
            options={
                "verbose_name": "Evento procesado (Bucli)",
                "verbose_name_plural": "Eventos procesados (Bucli)",
                "ordering": ["-received_at"],
            },
        ),
    ]
