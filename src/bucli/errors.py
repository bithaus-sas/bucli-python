"""Typed errors. Every refusal of ``/v1`` answers::

    {"error": {"type", "code", "message", "param", "doc_url", "request_id"}}

``type`` picks the class (what to do about it); ``code`` is the stable reason a
program branches on (``e.code == "resource_missing"``); ``message`` is the
Spanish sentence for a person. A replay that cannot repeat a secret or a URL
(``409 url_unavailable_on_replay`` / ``webhook_secret_unavailable_on_replay``)
also names the object: ``e.object_id`` (``cs_…``) and ``e.object_type``.
"""

from __future__ import annotations

from typing import Any

from bucli.objects import BucliObject, convert


class BucliError(Exception):
    """Base of every error this library raises."""

    def __init__(
        self,
        message: str | None = None,
        *,
        type: str | None = None,  # noqa: A002 — the API's own word
        code: str | None = None,
        param: str | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
        doc_url: str | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        object_id: str | None = None,
        object_type: str | None = None,
    ) -> None:
        super().__init__(message or "")
        self.message = message or ""
        self.type = type
        self.code = code
        self.param = param
        self.request_id = request_id
        self.http_status = http_status
        self.doc_url = doc_url
        self.headers = dict(headers or {})
        self.json_body = json_body
        #: The object a replay refusal names (``error.object_id``/``object_type``), else ``None``.
        self.object_id = object_id
        self.object_type = object_type

    @property
    def quote(self) -> BucliObject | None:
        """The new quote a ``409 quote_changed`` carries (``error.quote``), else ``None``:
        the same kind of object ``subscriptions.preview`` answers (``quote.amount``,
        ``quote["amount"]``); show it and send its ``amount`` again as ``expected_amount``."""
        payload = self.json_body.get("error") if isinstance(self.json_body, dict) else None
        quote = payload.get("quote") if isinstance(payload, dict) else None
        return convert(quote) if isinstance(quote, dict) else None

    def __str__(self) -> str:
        parts = [self.message or self.__class__.__name__]
        if self.code:
            parts.append(f"(code={self.code})")
        if self.request_id:
            parts.append(f"[{self.request_id}]")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(message={self.message!r}, code={self.code!r}, "
            f"http_status={self.http_status!r}, request_id={self.request_id!r})"
        )


class APIConnectionError(BucliError):
    """Bucli could not be reached (DNS, TLS, timeout, connection reset)."""


class AuthenticationError(BucliError):
    """401: the key is missing, invalid or revoked."""


class PermissionError(BucliError):  # noqa: A001 — Stripe's name, scoped to this package
    """403: the plan, the key kind or the role does not allow it."""


class InvalidRequestError(BucliError):
    """400/404/409/413/422: the request itself is wrong (``param`` says where)."""


class IdempotencyError(BucliError):
    """409 ``idempotency_key_in_use`` / ``idempotency_key_reused``."""


class RateLimitError(BucliError):
    """429: too many requests; ``retry_after`` says how long to wait."""

    @property
    def retry_after(self) -> float | None:
        return parse_retry_after(self.headers.get("Retry-After"))


class BillingError(BucliError):
    """402: Bucli's own plan of the merchant refuses it (limit, read-only)."""


class CardError(BillingError):
    """402 ``payment_method_rejected``: Wompi refused the payment method."""


class APIError(BucliError):
    """5xx or an answer that is not the API's error shape."""


class SignatureVerificationError(BucliError):
    """A webhook whose ``Bucli-Signature`` does not verify, is too old or is not JSON."""

    def __init__(self, message: str, sig_header: str | None = None) -> None:
        super().__init__(message)
        self.sig_header = sig_header


ERROR_TYPES: dict[str, type[BucliError]] = {
    "authentication_error": AuthenticationError,
    "permission_error": PermissionError,
    "invalid_request_error": InvalidRequestError,
    "idempotency_error": IdempotencyError,
    "rate_limit_error": RateLimitError,
    "billing_error": BillingError,
    "api_error": APIError,
}

#: Codes that deserve a narrower class than their type's.
ERROR_CODES: dict[str, type[BucliError]] = {
    "payment_method_rejected": CardError,
}


def _class_for(error_type: str | None, code: str | None, status: int) -> type[BucliError]:
    if code in ERROR_CODES:
        return ERROR_CODES[code]
    if error_type in ERROR_TYPES:
        return ERROR_TYPES[error_type]
    if status == 401:
        return AuthenticationError
    if status == 403:
        return PermissionError
    if status == 402:
        return BillingError
    if status == 429:
        return RateLimitError
    if 400 <= status < 500:
        return InvalidRequestError
    return APIError


def error_from_response(status: int, body: Any, headers: dict[str, str]) -> BucliError:
    """The typed error of an HTTP answer ``status`` >= 400."""
    payload = body.get("error") if isinstance(body, dict) else None
    if not isinstance(payload, dict):
        payload = {}
    error_type = payload.get("type")
    code = payload.get("code")
    message = payload.get("message") or f"Bucli respondió HTTP {status}."
    cls = _class_for(error_type, code, status)
    return cls(
        message,
        type=error_type,
        code=code,
        param=payload.get("param"),
        request_id=payload.get("request_id") or headers.get("Request-Id"),
        http_status=status,
        doc_url=payload.get("doc_url"),
        headers=headers,
        json_body=body,
        object_id=_text(payload.get("object_id")),
        object_type=_text(payload.get("object_type")),
    )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_retry_after(value: str | None) -> float | None:
    """Seconds of a ``Retry-After`` header (Bucli sends whole seconds)."""
    if value in (None, ""):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return max(seconds, 0.0)
