"""Verifying Bucli's webhooks.

Every delivery carries ``Bucli-Signature: t=<timestamp>,v1=<hex>`` where
``v1 = HMAC-SHA256(whsec_…, "<t>.<raw body>")``. For 24 hours after the
endpoint's secret is rolled the header carries TWO ``v1`` (the previous
secret's and, last, the new one's): any match is enough.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterable
from typing import Any

from bucli.constants import SIGNATURE_SCHEME, SIGNATURE_TOLERANCE_SECONDS
from bucli.errors import SignatureVerificationError
from bucli.objects import BucliObject, convert


def _as_bytes(payload: bytes | bytearray | str) -> bytes:
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise TypeError("El cuerpo del webhook debe ser bytes (request.body), sin parsear.")


def _secrets(secret: str | Iterable[str]) -> list[str]:
    values = [secret] if isinstance(secret, str) else list(secret or [])
    values = [value for value in values if isinstance(value, str) and value]
    if not values:
        raise ValueError("Falta el secreto del endpoint de webhooks (whsec_…).")
    return values


def compute_signature(payload: bytes | str, secret: str, timestamp: int | str) -> str:
    """``HMAC-SHA256(secret, "<timestamp>.<payload>")`` in hex."""
    message = f"{timestamp}.".encode() + _as_bytes(payload)
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class WebhookSignature:
    @staticmethod
    def parse_header(header: str) -> tuple[int, list[str]]:
        timestamp: int | None = None
        signatures: list[str] = []
        for item in (header or "").split(","):
            key, sep, value = item.strip().partition("=")
            if not sep:
                continue
            if key == "t":
                try:
                    timestamp = int(value)
                except ValueError:
                    raise SignatureVerificationError(
                        "El timestamp de Bucli-Signature no es un número.", header
                    ) from None
            elif key == SIGNATURE_SCHEME and value:
                signatures.append(value)
        if timestamp is None:
            raise SignatureVerificationError("Bucli-Signature no trae el timestamp (t=…).", header)
        if not signatures:
            raise SignatureVerificationError("Bucli-Signature no trae ninguna firma v1.", header)
        return timestamp, signatures

    @classmethod
    def verify_header(
        cls,
        payload: bytes | str,
        header: str,
        secret: str | Iterable[str],
        tolerance: int | None = SIGNATURE_TOLERANCE_SECONDS,
        *,
        now: float | None = None,
    ) -> bool:
        """``True`` or ``SignatureVerificationError``. ``secret`` may be a list
        (your own rotation of ``BUCLI_WEBHOOK_SECRET``)."""
        if not header:
            raise SignatureVerificationError("Falta la cabecera Bucli-Signature.", header)
        timestamp, signatures = cls.parse_header(header)
        if tolerance is not None:
            age = (time.time() if now is None else now) - timestamp
            if age > tolerance:
                raise SignatureVerificationError("La firma es demasiado vieja: posible repetición.", header)
            if age < -tolerance:
                raise SignatureVerificationError("La firma viene del futuro: revisa el reloj.", header)
        body = _as_bytes(payload)
        for value in _secrets(secret):
            expected = compute_signature(body, value, timestamp)
            # Constant-time per pair; any v1 may match (two during a rotation).
            if any(hmac.compare_digest(expected, candidate) for candidate in signatures):
                return True
        raise SignatureVerificationError("Ninguna firma v1 coincide con el cuerpo y el secreto.", header)


class Webhook:
    @staticmethod
    def construct_event(
        payload: bytes | str,
        sig_header: str,
        secret: str | Iterable[str],
        tolerance: int | None = SIGNATURE_TOLERANCE_SECONDS,
        *,
        now: float | None = None,
    ) -> BucliObject:
        """The verified ``event`` of a delivery.

        ``payload`` is the RAW body (Django: ``request.body``), never a parsed and
        re-serialized one. Raises ``SignatureVerificationError`` when the header is
        missing or malformed, no ``v1`` matches, ``t`` is older or newer than
        ``tolerance`` seconds, or the body is not a JSON object."""
        body = _as_bytes(payload)
        WebhookSignature.verify_header(body, sig_header, secret, tolerance, now=now)
        try:
            data: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SignatureVerificationError("El cuerpo del webhook no es JSON válido.", sig_header) from None
        if not isinstance(data, dict):
            raise SignatureVerificationError("El cuerpo del webhook no es un objeto JSON.", sig_header)
        return convert(data)

    @staticmethod
    def generate_test_header(
        payload: bytes | str,
        secret: str | Iterable[str],
        *,
        timestamp: int | None = None,
    ) -> str:
        """A valid ``Bucli-Signature`` for your tests. A list of secrets yields
        one ``v1`` per secret, as a rotation does."""
        ts = int(time.time()) if timestamp is None else int(timestamp)
        signatures = [compute_signature(payload, value, ts) for value in _secrets(secret)]
        return ",".join([f"t={ts}", *(f"{SIGNATURE_SCHEME}={item}" for item in signatures)])
