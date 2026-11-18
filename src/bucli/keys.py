"""HTTP idempotency keys this library derives (no imports: used by resources and metering)."""

from __future__ import annotations

import hashlib

#: Bucli's ``Idempotency-Key`` limit.
MAX_LENGTH = 255


def reserve_request_key(meter: str, customer: str, idempotency_key: str) -> str:
    """The header of a reservation, scoped to the meter and the customer:
    Bucli scopes the header per account only, and one document key may be
    reserved on two meters (or for two customers)."""
    key = f"reserve:{meter}:{customer}:{idempotency_key}"
    if len(key) <= MAX_LENGTH:
        return key
    return "reserve:" + hashlib.sha256(key.encode()).hexdigest()
