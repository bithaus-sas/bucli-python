"""The metering guard for a Django SaaS: the queue in the database
(``PendingUsage``), the balances in Django's cache.

    from bucli.contrib.django.metering import guard

    reservation = guard().reserve("documents", customer=account.bucli_customer_id,
                                  idempotency_key=f"doc-{document.pk}")
    if not reservation.allowed:
        raise QuotaExhausted(reservation.refusal)

Call ``reserve`` OUTSIDE your business transaction (or with ``ATOMIC_REQUESTS``
off for that view): a unit let through is queued in the database, and a
rollback of the caller's transaction would take the queued unit with it.

Settings: ``BUCLI_METER_ON_UNAVAILABLE`` (``"allow"``, ``"deny"`` or
``{"documents": "allow"}``; default ``deny``), ``BUCLI_METER_TIMEOUT``
(seconds, default 2), ``BUCLI_METER_CACHE_SECONDS`` (default 300) and
``BUCLI_METER_MAX_REPORT_ATTEMPTS`` (default 20). Report the queue with
``manage.py bucli_flush_usage`` (a cron every minute, or your task queue).
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from bucli.contrib.django import conf
from bucli.contrib.django.models import PendingUsage
from bucli.metering import DENY, MAX_REPORT_ATTEMPTS, PendingUnit, UsageGuard, aware

CACHE_PREFIX = "bucli:meter:"
INDEX_PREFIX = "bucli:meter-index:"


def _cache_key(meter: str, customer: str) -> str:
    return f"{CACHE_PREFIX}{meter}:{customer}"


def _index_key(customer: str) -> str:
    return f"{INDEX_PREFIX}{customer}"


def _occurred(value):
    """A stored moment with its zone (``USE_TZ = False`` stores it naive, in local time)."""
    if timezone.is_naive(value):
        return aware(timezone.make_aware(value))
    return value


class DatabaseUsageStore:
    """``UsageStore`` on the database (queue) and Django's cache (balances)."""

    def __init__(self, max_attempts: int = MAX_REPORT_ATTEMPTS) -> None:
        self.max_attempts = max_attempts

    def enqueue(self, unit: PendingUnit) -> None:
        try:
            with transaction.atomic():
                PendingUsage.objects.create(
                    meter=unit.meter,
                    customer=unit.customer,
                    idempotency_key=unit.idempotency_key,
                    quantity=unit.quantity,
                    billable=unit.billable,
                    metadata=unit.metadata,
                    occurred_at=unit.occurred_at,
                )
        except IntegrityError:
            pass  # already queued: the same unit

    def pending(self, limit: int) -> list[PendingUnit]:
        rows = PendingUsage.objects.filter(reported_at__isnull=True, attempts__lt=self.max_attempts)[:limit]
        return [
            PendingUnit(
                meter=row.meter,
                customer=row.customer,
                idempotency_key=row.idempotency_key,
                quantity=row.quantity,
                occurred_at=_occurred(row.occurred_at),
                billable=row.billable,
                metadata=row.metadata or {},
                attempts=row.attempts,
            )
            for row in rows
        ]

    def _row(self, unit: PendingUnit):
        return PendingUsage.objects.filter(
            meter=unit.meter, customer=unit.customer, idempotency_key=unit.idempotency_key
        )

    def done(self, unit: PendingUnit) -> None:
        self._row(unit).update(reported_at=timezone.now(), last_error="")

    def failed(self, unit: PendingUnit, error: str) -> None:
        self._row(unit).update(attempts=F("attempts") + 1, last_error=error[:255])
        unit.attempts += 1

    def discard(self, meter: str, customer: str, idempotency_key: str) -> bool:
        deleted, _ = PendingUsage.objects.filter(
            meter=meter, customer=customer, idempotency_key=idempotency_key, reported_at__isnull=True
        ).delete()
        return bool(deleted)

    def remember(self, meter: str, customer: str, remaining: int | None) -> None:
        if remaining is None:
            cache.delete(_cache_key(meter, customer))
            return
        seconds = conf.meter_cache_seconds()
        cache.set(_cache_key(meter, customer), remaining, timeout=seconds)
        meters = set(cache.get(_index_key(customer)) or ())
        if meter not in meters:
            cache.set(_index_key(customer), sorted({*meters, meter}), timeout=seconds)

    def remaining(self, meter: str, customer: str, max_age: float) -> int | None:
        return cache.get(_cache_key(meter, customer))

    def forget(self, meter: str | None, customer: str) -> None:
        if meter is not None:
            cache.delete(_cache_key(meter, customer))
            return
        for key in {*(cache.get(_index_key(customer)) or ()), *conf.meter_keys()}:
            cache.delete(_cache_key(key, customer))
        cache.delete(_index_key(customer))


def guard() -> UsageGuard:
    """The guard configured from the settings."""
    return UsageGuard(
        conf.get_client(),
        on_unavailable=conf.meter_on_unavailable() or DENY,
        store=DatabaseUsageStore(max_attempts=conf.meter_max_report_attempts()),
        timeout=conf.meter_timeout(),
        cache_seconds=conf.meter_cache_seconds(),
    )
