"""Reserving metered units without letting Bucli stop your business (spec §2.6).

``UsageGuard.reserve`` asks Bucli for a unit right before the irreversible
act (sending a document). What happens when Bucli does not answer is the
meter's ``on_unavailable``:

- ``allow``: if Bucli does not answer within ``timeout`` (2 s by default) —
  a connection error, a timeout, a 5xx — the unit is let through, queued
  locally with its ``idempotency_key`` and reported later
  (``POST /v1/usage`` with ``occurred_at``) by ``flush()``. Bucli records it
  even beyond the quota (``overage``) and never refuses it.
- ``deny``: the error is raised; nothing is queued.

A refusal of Bucli (``402 quota_exhausted``, ``402 entitlement_access_denied``)
is an answer, never «unavailable»: it is returned as ``allowed=False``.

Each answer of Bucli carries the customer's ``remaining`` (allowance +
grants; ``-1`` unlimited) and the guard keeps it (``store``), so a customer
already at zero is refused locally — no call — until ``cache_seconds`` pass
or ``forget()`` is called (a grant bought, a new plan: on ``grant.created``
or ``entitlements.updated``).

``store`` is where the queue and the balances live: ``MemoryUsageStore`` (one
process; tests), or ``bucli.contrib.django.metering.DatabaseUsageStore``
(the database: survives a restart, shared by every worker).
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from bucli.constants import UNLIMITED
from bucli.errors import APIConnectionError, APIError, BillingError, BucliError, RateLimitError

if TYPE_CHECKING:
    from bucli.client import Bucli
    from bucli.objects import BucliObject

logger = logging.getLogger("bucli.metering")

ALLOW = "allow"
DENY = "deny"
#: Codes of Bucli that refuse a unit (an answer, not an outage).
REFUSALS = ("quota_exhausted", "entitlement_access_denied")
#: A conflict that only means «the same request is still running»: wait, as for an outage.
IN_FLIGHT_CODES = ("idempotency_key_in_use",)


@dataclass
class Reservation:
    """What ``UsageGuard.reserve`` decided.

    ``allowed``: go ahead. ``record``: Bucli's ``usage_record`` (``None`` when
    decided without Bucli). ``offline``: let through while Bucli did not
    answer (queued). ``refusal``: Bucli's code when refused (or
    ``"quota_exhausted"`` when refused locally). ``remaining``: what the
    customer has left, if known (``-1`` unlimited)."""

    allowed: bool
    record: BucliObject | None = None
    offline: bool = False
    refusal: str | None = None
    remaining: int | None = None
    error: BucliError | None = None

    @property
    def usage_id(self) -> str | None:
        return getattr(self.record, "id", None) if self.record is not None else None


@dataclass
class PendingUnit:
    """A unit let through while Bucli did not answer, waiting to be reported."""

    meter: str
    customer: str
    idempotency_key: str
    quantity: int
    occurred_at: datetime.datetime
    billable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0


class UsageStore(Protocol):
    def enqueue(self, unit: PendingUnit) -> None: ...

    def pending(self, limit: int) -> list[PendingUnit]: ...

    def done(self, unit: PendingUnit) -> None: ...

    def failed(self, unit: PendingUnit, error: str) -> None: ...

    def remember(self, meter: str, customer: str, remaining: int | None) -> None: ...

    def remaining(self, meter: str, customer: str, max_age: float) -> int | None: ...

    def forget(self, meter: str | None, customer: str) -> None: ...

    def discard(self, meter: str, customer: str, idempotency_key: str) -> bool: ...


#: A queued unit Bucli refused this many times is left out of the flush
#: (kept, for a person to look at): a refusal is not an outage.
MAX_REPORT_ATTEMPTS = 20


class MemoryUsageStore:
    """The queue and the balances in this process (tests, scripts)."""

    def __init__(self, max_attempts: int = MAX_REPORT_ATTEMPTS) -> None:
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._queue: dict[tuple[str, str, str], PendingUnit] = {}
        self._balances: dict[tuple[str, str], tuple[int, float]] = {}

    def enqueue(self, unit: PendingUnit) -> None:
        with self._lock:
            self._queue.setdefault((unit.meter, unit.customer, unit.idempotency_key), unit)

    def pending(self, limit: int) -> list[PendingUnit]:
        with self._lock:
            return [unit for unit in self._queue.values() if unit.attempts < self.max_attempts][:limit]

    def done(self, unit: PendingUnit) -> None:
        with self._lock:
            self._queue.pop((unit.meter, unit.customer, unit.idempotency_key), None)

    def failed(self, unit: PendingUnit, error: str) -> None:
        with self._lock:
            unit.attempts += 1

    def remember(self, meter: str, customer: str, remaining: int | None) -> None:
        with self._lock:
            if remaining is None:
                self._balances.pop((meter, customer), None)
            else:
                self._balances[(meter, customer)] = (remaining, time.monotonic())

    def remaining(self, meter: str, customer: str, max_age: float) -> int | None:
        with self._lock:
            found = self._balances.get((meter, customer))
        if found is None or time.monotonic() - found[1] > max_age:
            return None
        return found[0]

    def forget(self, meter: str | None, customer: str) -> None:
        with self._lock:
            for key in [key for key in self._balances if key[1] == customer and meter in (None, key[0])]:
                self._balances.pop(key, None)

    def discard(self, meter: str, customer: str, idempotency_key: str) -> bool:
        with self._lock:
            return self._queue.pop((meter, customer, idempotency_key), None) is not None


def _unavailable(error: BucliError) -> bool:
    """Bucli did not answer (or answered that it cannot now): never the
    unit's fault, so it never spends a report attempt."""
    if isinstance(error, APIConnectionError | RateLimitError):
        return True
    if getattr(error, "code", None) in IN_FLIGHT_CODES:
        return True
    return isinstance(error, APIError) and (error.http_status or 500) >= 500


def aware(moment: datetime.datetime) -> datetime.datetime:
    """A moment with its zone (a naive one is taken as UTC)."""
    return (
        moment if moment.tzinfo is not None and moment.utcoffset() is not None else moment.replace(tzinfo=datetime.UTC)
    )


def _remaining_of_refusal(error: BucliError) -> int | None:
    """What a ``402 quota_exhausted`` says is left (cupo + grants), if it says."""
    payload = (error.json_body or {}).get("error") if isinstance(error.json_body, dict) else None
    if not isinstance(payload, dict):
        return None
    allowance, used, grants = payload.get("allowance"), payload.get("used"), payload.get("grants_remaining")
    if not all(isinstance(value, int) for value in (allowance, used, grants)) or allowance == UNLIMITED:
        return None
    return max(allowance - used, 0) + grants


class UsageGuard:
    """``UsageGuard(client, on_unavailable="allow", store=...)``; one per meter
    policy. ``on_unavailable`` may also be a dict ``{meter_key: "allow"|"deny"}``
    (default ``deny`` for a meter not named)."""

    def __init__(
        self,
        client: Bucli,
        *,
        on_unavailable: str | dict[str, str] = DENY,
        store: UsageStore | None = None,
        timeout: float = 2.0,
        cache_seconds: float = 300.0,
        deny_when_exhausted: bool = True,
        clock: Any = None,
    ) -> None:
        self.client = client
        self.on_unavailable = on_unavailable
        self.store = store or MemoryUsageStore()
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.deny_when_exhausted = deny_when_exhausted
        self._now = clock or (lambda: datetime.datetime.now(datetime.UTC))

    def policy(self, meter: str) -> str:
        if isinstance(self.on_unavailable, dict):
            return self.on_unavailable.get(meter, DENY)
        return self.on_unavailable

    def reserve(
        self,
        meter: str,
        *,
        customer: str,
        idempotency_key: str,
        quantity: int = 1,
        billable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Reservation:
        """Take ``quantity`` units of ``meter`` for ``customer``, or say why not."""
        if billable and self.deny_when_exhausted:
            cached = self.store.remaining(meter, customer, self.cache_seconds)
            if cached is not None and cached != UNLIMITED and cached < quantity:
                return Reservation(allowed=False, refusal="quota_exhausted", remaining=cached)
        try:
            record = self.client.meters.reserve(
                meter,
                customer=customer,
                idempotency_key=idempotency_key,
                quantity=quantity,
                billable=billable,
                metadata=metadata,
                timeout=self.timeout,
                max_retries=0,
            )
        except BillingError as error:
            if error.code in REFUSALS:
                left = _remaining_of_refusal(error) if error.code == "quota_exhausted" else None
                self.store.remember(meter, customer, left)
                return Reservation(allowed=False, refusal=error.code, remaining=left, error=error)
            raise
        except BucliError as error:
            if not _unavailable(error) or self.policy(meter) != ALLOW:
                raise
            self.store.enqueue(
                PendingUnit(
                    meter=meter,
                    customer=customer,
                    idempotency_key=idempotency_key,
                    quantity=quantity,
                    occurred_at=aware(self._now()),
                    billable=billable,
                    metadata=dict(metadata or {}),
                )
            )
            return Reservation(allowed=True, offline=True, error=error)
        remaining = getattr(record, "remaining", None)
        self.store.remember(meter, customer, remaining)
        return Reservation(allowed=True, record=record, remaining=remaining)

    def release(self, usage_id: str) -> BucliObject:
        """Give a unit back (``POST /v1/usage/{id}/release``); the cached
        balance of its customer is dropped."""
        record = self.client.usage.release(usage_id)
        meter, customer = getattr(record, "meter", None), getattr(record, "customer", None)
        if isinstance(meter, str) and isinstance(customer, str):
            self.store.forget(meter, customer)
        return record

    def discard(self, meter: str, *, customer: str, idempotency_key: str) -> bool:
        """Drop a unit let through offline that never happened after all (the
        document was not sent): it is not reported. Whether it was queued."""
        return self.store.discard(meter, customer, idempotency_key)

    def forget(self, customer: str, meter: str | None = None) -> None:
        """Drop the cached balance (a grant bought, a plan changed)."""
        self.store.forget(meter, customer)

    def flush(self, limit: int = 200) -> dict[str, int]:
        """Report the queued units to Bucli. Returns ``{"reported", "failed"}``;
        what fails stays queued for the next flush."""
        reported = failed = 0
        for unit in self.store.pending(limit):
            try:
                self.client.usage.report(
                    meter=unit.meter,
                    customer=unit.customer,
                    idempotency_key=unit.idempotency_key,
                    quantity=unit.quantity,
                    occurred_at=aware(unit.occurred_at),
                    billable=unit.billable,
                    metadata=unit.metadata or None,
                )
            except BucliError as error:
                failed += 1
                if _unavailable(error):
                    break  # still down: nothing is the units' fault; they wait for the next flush
                self._refused(unit, f"{type(error).__name__}: {error.code or error}")
                continue
            except (TypeError, ValueError) as error:  # a unit that cannot be sent: never block the rest
                failed += 1
                self._refused(unit, f"{type(error).__name__}: {error}")
                continue
            self.store.done(unit)
            self.store.forget(unit.meter, unit.customer)
            reported += 1
        return {"reported": reported, "failed": failed}

    def _refused(self, unit: PendingUnit, error: str) -> None:
        """Bucli refused a report (not an outage): one attempt spent; past the
        cap the unit stays stored but out of the flush, and it is logged."""
        self.store.failed(unit, error)
        if unit.attempts >= getattr(self.store, "max_attempts", MAX_REPORT_ATTEMPTS):
            logger.error(
                "Bucli refused the report of a metered unit too many times (%s, %s, %s): %s",
                unit.meter,
                unit.customer,
                unit.idempotency_key,
                error,
            )
        else:
            logger.warning("Bucli refused the report of a metered unit (%s): %s", unit.idempotency_key, error)
