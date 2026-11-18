"""The HTTP client of the API ``/v1``.

* **The key decides the mode**: ``sk_test_…`` (Wompi's sandbox) or ``sk_live_…``.
  A publishable key (``pk_``) is refused: it belongs in a browser.
* **Idempotency**: every POST carries an ``Idempotency-Key`` (a uuid4 unless
  you pass one) and the SAME key on every retry, so a retried POST never does
  the thing twice.
* **Retries** with exponential backoff and jitter, only when retrying is safe
  and useful: network errors, ``409 idempotency_key_in_use``, ``429``
  (honouring ``Retry-After``) and ``5xx``. Never on another 4xx.
"""

from __future__ import annotations

import datetime
import json as jsonlib
import platform
import random
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import requests

from bucli import resources
from bucli._version import __version__
from bucli.constants import (
    API_PREFIX,
    API_VERSION,
    DEFAULT_BASE_URL,
    IDEMPOTENCY_HEADER,
    PUBLISHABLE_KEY_PREFIXES,
    SECRET_KEY_PREFIXES,
    VERSION_HEADER,
)
from bucli.errors import APIConnectionError, APIError, BucliError, error_from_response, parse_retry_after
from bucli.objects import BucliObject, LastResponse, ListObject, convert

#: Network failures after which the request may be retried (the same
#: ``Idempotency-Key`` makes a retried POST safe).
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
RETRYABLE_CODES = ("idempotency_key_in_use",)


def _check_key(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Falta la llave secreta de Bucli (sk_test_… o sk_live_…).")
    api_key = api_key.strip()
    if api_key.startswith(PUBLISHABLE_KEY_PREFIXES):
        raise ValueError(
            "Esa es una llave publicable (pk_): solo sirve en el navegador para abrir un checkout. "
            "En el servidor usa la llave secreta (sk_test_… o sk_live_…)."
        )
    if not api_key.startswith(SECRET_KEY_PREFIXES):
        raise ValueError("La llave de Bucli debe empezar por sk_test_ o sk_live_.")
    return api_key


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def encode_params(params: Mapping[str, Any] | None) -> list[tuple[str, Any]]:
    """Query parameters as Bucli reads them: booleans as ``true``/``false``,
    lists repeated (``expand[]=a&expand[]=b``), ``None`` left out."""
    pairs: list[tuple[str, Any]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, list | tuple | set):
            name = key if key.endswith("[]") else f"{key}[]"
            pairs.extend((name, _query_value(item)) for item in value)
        else:
            pairs.append((key, _query_value(value)))
    return pairs


class Bucli:
    """``Bucli("sk_test_…")`` — one client per key; thread-safe for requests."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = API_VERSION,
        timeout: float = 30.0,
        max_retries: int = 2,
        max_retry_after: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = _check_key(api_key)
        self.livemode = self.api_key.startswith("sk_live_")
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith(API_PREFIX):
            self.base_url = self.base_url[: -len(API_PREFIX)]
        self.api_version = api_version
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        #: A ``Retry-After`` longer than this is not waited for: the error is raised.
        self.max_retry_after = max_retry_after
        self.session = session or requests.Session()
        self._sleep: Callable[[float], None] = time.sleep

        self.account = resources.Account(self)
        self.customers = resources.Customers(self)
        self.products = resources.Products(self)
        self.prices = resources.Prices(self)
        self.subscriptions = resources.Subscriptions(self)
        self.subscription_items = resources.SubscriptionItems(self)
        self.payment_methods = resources.PaymentMethods(self)
        self.invoices = resources.Invoices(self)
        self.payments = resources.Payments(self)
        self.refunds = resources.Refunds(self)
        self.coupons = resources.Coupons(self)
        self.fixed_term_offers = resources.FixedTermOffers(self)
        self.events = resources.Events(self)
        self.webhook_endpoints = resources.WebhookEndpoints(self)
        self.checkout_sessions = resources.CheckoutSessions(self)
        self.payment_links = resources.PaymentLinks(self)
        self.portal_sessions = resources.PortalSessions(self)
        self.features = resources.Features(self)
        self.meters = resources.Meters(self)
        self.usage = resources.Usage(self)
        self.grant_offers = resources.GrantOffers(self)
        self.grants = resources.Grants(self)

    def __repr__(self) -> str:
        mode = "live" if self.livemode else "test"
        return f"Bucli(mode={mode!r}, base_url={self.base_url!r}, api_version={self.api_version!r})"

    # -- requests -------------------------------------------------------------

    def _headers(self, method: str, idempotency_key: str | None, *, has_body: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            VERSION_HEADER: self.api_version,
            "User-Agent": f"bucli-python/{__version__} (Python {platform.python_version()})",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        return headers

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        base = min(0.5 * (2**attempt), 8.0)
        return base * random.uniform(0.5, 1.0)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        expand: Iterable[str] | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """One call to the API, retried when safe. Answers the converted object.

        ``path`` is relative to ``/v1`` (``"/customers"``). ``timeout`` and
        ``max_retries`` override the client's for this call (the metering
        guard asks with 2 s and no retry)."""
        method = method.upper()
        if method == "POST" and not idempotency_key:
            idempotency_key = str(uuid.uuid4())
        if method != "POST":
            idempotency_key = None
        query = dict(params or {})
        if expand:
            query["expand[]"] = list(expand)
        url = f"{self.base_url}{API_PREFIX}{path}"
        data = None
        clean = {key: value for key, value in (body or {}).items() if value is not None}
        if method in ("POST", "PATCH") or (method == "DELETE" and clean):
            data = jsonlib.dumps(clean, separators=(",", ":"), default=_json_default)
        headers = self._headers(method, idempotency_key, has_body=data is not None)

        retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        wait = self.timeout if timeout is None else timeout
        attempt = 0
        while True:
            try:
                response = self.session.request(
                    method,
                    url,
                    params=encode_params(query),
                    data=data,
                    headers=headers,
                    timeout=wait,
                    allow_redirects=False,
                )
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt < retries:
                    self._sleep(self._delay(attempt, None))
                    attempt += 1
                    continue
                raise APIConnectionError(
                    f"No se pudo conectar con Bucli ({type(exc).__name__}) tras {attempt + 1} intento(s).",
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise APIConnectionError(f"No se pudo hacer la petición a Bucli: {exc}") from exc

            response_headers = dict(response.headers)
            try:
                payload = response.json() if response.content else None
            except ValueError:
                payload = None

            if response.status_code < 400:
                if payload is None and response.content:
                    raise APIError(
                        f"Bucli respondió HTTP {response.status_code} con un cuerpo que no es JSON.",
                        http_status=response.status_code,
                        headers=response_headers,
                    )
                obj = convert(payload, self)
                if isinstance(obj, BucliObject):
                    obj.last_response = LastResponse(response.status_code, response_headers, idempotency_key)
                return obj

            error = error_from_response(response.status_code, payload, response_headers)
            retry_after = parse_retry_after(response_headers.get("Retry-After"))
            if attempt < retries and self._should_retry(response.status_code, error, retry_after):
                self._sleep(self._delay(attempt, retry_after))
                attempt += 1
                continue
            raise error

    def _should_retry(self, status: int, error: BucliError, retry_after: float | None) -> bool:
        if retry_after is not None and retry_after > self.max_retry_after:
            return False
        if status == 409:
            return error.code in RETRYABLE_CODES
        return status == 429 or status >= 500

    def _get_list(self, path: str, params: Mapping[str, Any] | None = None) -> ListObject:
        params = dict(params or {})
        expand = params.pop("expand[]", None) or params.pop("expand", None)
        if isinstance(expand, str):
            expand = [expand]
        result = self.request("GET", path, params=params, expand=expand)
        if isinstance(result, ListObject):
            object.__setattr__(result, "_path", path)
            stored = dict(params)
            if expand:
                stored["expand[]"] = list(expand)
            object.__setattr__(result, "_params", stored)
        return result


def _json_default(value: Any) -> Any:
    """Dates (``billing_cycle_anchor``, ``trial_end``) go as ISO 8601 with
    their time zone; a naive datetime is refused (Bucli would guess wrong)."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Envía las fechas con zona horaria (datetime aware), p. ej. datetime.UTC.")
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} no se puede enviar como JSON")
