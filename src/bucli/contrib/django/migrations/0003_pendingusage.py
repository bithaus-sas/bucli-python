from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bucli_billing", "0002_billingaccount_bucli_updated_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="PendingUsage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("meter", models.CharField(max_length=60, verbose_name="Medidor")),
                ("customer", models.CharField(max_length=64, verbose_name="Cliente en Bucli")),
                ("idempotency_key", models.CharField(max_length=255, verbose_name="Llave de la unidad")),
                ("quantity", models.PositiveIntegerField(default=1, verbose_name="Cantidad")),
                ("billable", models.BooleanField(default=True, verbose_name="Gasta cupo")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="Metadata")),
                ("occurred_at", models.DateTimeField(verbose_name="Ocurrió el")),
                ("attempts", models.PositiveSmallIntegerField(default=0, verbose_name="Intentos de reporte")),
                ("last_error", models.CharField(blank=True, default="", max_length=255, verbose_name="Último error")),
                (
                    "reported_at",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Reportado el"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Consumo por reportar (Bucli)",
                "verbose_name_plural": "Consumos por reportar (Bucli)",
                "ordering": ["occurred_at", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("meter", "customer", "idempotency_key"), name="bucli_pending_usage_one_per_unit"
                    )
                ],
            },
        ),
    ]
