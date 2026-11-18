"""The local copy of Bucli's state: one ``BillingAccount`` per account of the SaaS.

It is a COPY, to decide access without calling Bucli on every request. Only
``services.store_subscription`` writes it, from an object Bucli just answered
(a webhook followed by a ``GET``, or a sync) — never from a redirect to
``success_url``.
"""

from __future__ import annotations

from django.db import models

from bucli.constants import ENDED_STATUSES, RUNNING_STATUSES
from bucli.contrib.django import conf

ACCOUNT_MODEL = conf.account_model_label()


class BillingAccount(models.Model):
    account = models.OneToOneField(
        ACCOUNT_MODEL,
        on_delete=models.CASCADE,
        related_name="bucli_billing",
        verbose_name="Cuenta",
    )
    bucli_customer_id = models.CharField("Cliente en Bucli", max_length=64, blank=True, default="", db_index=True)
    bucli_subscription_id = models.CharField(
        "Suscripción en Bucli", max_length=64, blank=True, default="", db_index=True
    )
    price_id = models.CharField("Precio del plan", max_length=64, blank=True, default="")
    plan = models.CharField("Plan", max_length=64, blank=True, default="")
    pending_price_id = models.CharField("Precio desde el próximo ciclo", max_length=64, blank=True, default="")
    status = models.CharField("Estado", max_length=32, blank=True, default="")
    current_period_end = models.DateTimeField("Fin del periodo", null=True, blank=True)
    trial_end = models.DateTimeField("Fin de la prueba", null=True, blank=True)
    cancel_at_period_end = models.BooleanField("Se cancela al fin del periodo", default=False)
    items = models.JSONField("Ítems", default=list, blank=True)
    livemode = models.BooleanField("Producción", default=False)
    #: The subscription's ``updated`` in Bucli when it was copied: a copy
    #: older than this one never overwrites it (``store_subscription``).
    bucli_updated_at = models.DateTimeField("Último cambio en Bucli", null=True, blank=True)
    last_event_sequence = models.BigIntegerField("Último sequence aplicado", default=0)
    last_event_at = models.DateTimeField("Último evento aplicado", null=True, blank=True)
    last_synced_at = models.DateTimeField("Última copia desde Bucli", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "bucli_billing"
        verbose_name = "Cuenta de cobro (Bucli)"
        verbose_name_plural = "Cuentas de cobro (Bucli)"

    def __str__(self) -> str:
        return f"{self.account} · {self.plan or '-'} · {self.status or 'sin suscripción'}"

    @property
    def is_running(self) -> bool:
        """The subscription still bills (trialing, active, past_due)."""
        return self.status in RUNNING_STATUSES

    @property
    def is_ended(self) -> bool:
        return self.status in ENDED_STATUSES


class ProcessedEvent(models.Model):
    """Every Bucli event already handled: a redelivery (same ``id``) answers 200 and does nothing."""

    event_id = models.CharField("Evento", max_length=64, unique=True)
    type = models.CharField("Tipo", max_length=100)
    sequence = models.BigIntegerField("Sequence", null=True, blank=True)
    livemode = models.BooleanField("Producción", default=False)
    billing_account = models.ForeignKey(
        BillingAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_events",
    )
    outcome = models.CharField("Resultado", max_length=32, blank=True, default="")
    received_at = models.DateTimeField("Recibido", auto_now_add=True, db_index=True)

    class Meta:
        app_label = "bucli_billing"
        verbose_name = "Evento procesado (Bucli)"
        verbose_name_plural = "Eventos procesados (Bucli)"
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.event_id} {self.type} ({self.outcome})"


class PendingUsage(models.Model):
    """A metered unit let through while Bucli did not answer (``on_unavailable:
    allow``), waiting for ``bucli_flush_usage`` to report it
    (``bucli.contrib.django.metering.DatabaseUsageStore``)."""

    meter = models.CharField("Medidor", max_length=60)
    customer = models.CharField("Cliente en Bucli", max_length=64)
    idempotency_key = models.CharField("Llave de la unidad", max_length=255)
    quantity = models.PositiveIntegerField("Cantidad", default=1)
    billable = models.BooleanField("Gasta cupo", default=True)
    metadata = models.JSONField("Metadata", default=dict, blank=True)
    occurred_at = models.DateTimeField("Ocurrió el")
    attempts = models.PositiveSmallIntegerField("Intentos de reporte", default=0)
    last_error = models.CharField("Último error", max_length=255, blank=True, default="")
    reported_at = models.DateTimeField("Reportado el", null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "bucli_billing"
        verbose_name = "Consumo por reportar (Bucli)"
        verbose_name_plural = "Consumos por reportar (Bucli)"
        ordering = ["occurred_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["meter", "customer", "idempotency_key"], name="bucli_pending_usage_one_per_unit"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.meter} · {self.customer} · {self.idempotency_key}"
