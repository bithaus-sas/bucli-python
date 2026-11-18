"""The settings of ``bucli.contrib.django``, read in one place.

Required:
    ``BUCLI_SECRET_KEY``      ``sk_test_…`` (staging) or ``sk_live_…`` (production).
    ``BUCLI_WEBHOOK_SECRET``  The ``whsec_…`` of your endpoint in Bucli (a list during your own rotation).
    ``BUCLI_PRICE_PLANS``     ``{"price_…": "pro", ...}``: Bucli's prices → your internal plans.
    ``BUCLI_ACCOUNT_MODEL``   ``"tenants.Tenant"``: the model that owns a subscription.

Optional:
    ``BUCLI_GRACE``           How long ``past_due`` keeps full access after ``current_period_end``
                              (``timedelta`` or days). ``None`` (default): while Bucli keeps retrying.
    ``BUCLI_LAPSED_ACCESS``   ``"free"`` (default) or ``"read_only"``: what ``paused``, ``canceled``,
                              ``incomplete_expired`` (and no subscription) get.
    ``BUCLI_FREE_PLAN``       Your free plan's name (default ``"free"``).
    ``BUCLI_API_BASE_URL``, ``BUCLI_API_VERSION``, ``BUCLI_TIMEOUT``, ``BUCLI_MAX_RETRIES``,
    ``BUCLI_WEBHOOK_TOLERANCE`` (seconds, default 300).
"""

from __future__ import annotations

import datetime
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from bucli.client import Bucli
from bucli.constants import API_VERSION, DEFAULT_BASE_URL, SIGNATURE_TOLERANCE_SECONDS

LAPSED_FREE = "free"
LAPSED_READ_ONLY = "read_only"


def _setting(name: str, default=None):
    return getattr(settings, name, default)


def secret_key() -> str:
    value = _setting("BUCLI_SECRET_KEY")
    if not value:
        raise ImproperlyConfigured("Falta BUCLI_SECRET_KEY (sk_test_… o sk_live_…).")
    return value


def webhook_secrets() -> list[str]:
    value = _setting("BUCLI_WEBHOOK_SECRET")
    values = [value] if isinstance(value, str) else list(value or [])
    values = [item for item in values if item]
    if not values:
        raise ImproperlyConfigured("Falta BUCLI_WEBHOOK_SECRET (el whsec_… de tu endpoint en Bucli).")
    return values


def webhook_tolerance() -> int:
    return int(_setting("BUCLI_WEBHOOK_TOLERANCE", SIGNATURE_TOLERANCE_SECONDS))


def price_plans() -> dict[str, str]:
    value = _setting("BUCLI_PRICE_PLANS") or {}
    if not isinstance(value, dict):
        raise ImproperlyConfigured("BUCLI_PRICE_PLANS debe ser un dict {price_id: plan}.")
    return dict(value)


def account_model_label() -> str:
    value = _setting("BUCLI_ACCOUNT_MODEL")
    if not value or value.count(".") != 1:
        raise ImproperlyConfigured('BUCLI_ACCOUNT_MODEL debe ser "app_label.Modelo" (p. ej. "tenants.Tenant").')
    return value


def grace() -> datetime.timedelta | None:
    value = _setting("BUCLI_GRACE")
    if value in (None, ""):
        return None
    if isinstance(value, datetime.timedelta):
        return value
    if isinstance(value, int | float):
        return datetime.timedelta(days=value)
    raise ImproperlyConfigured("BUCLI_GRACE debe ser un timedelta o un número de días.")


def lapsed_access() -> str:
    value = _setting("BUCLI_LAPSED_ACCESS", LAPSED_FREE)
    if value not in (LAPSED_FREE, LAPSED_READ_ONLY):
        raise ImproperlyConfigured('BUCLI_LAPSED_ACCESS debe ser "free" o "read_only".')
    return value


def free_plan() -> str:
    return _setting("BUCLI_FREE_PLAN", "free")


@lru_cache(maxsize=8)
def _client(key: str, base_url: str, api_version: str, timeout: float, max_retries: int) -> Bucli:
    return Bucli(key, base_url=base_url, api_version=api_version, timeout=timeout, max_retries=max_retries)


def get_client() -> Bucli:
    """The client of ``BUCLI_SECRET_KEY`` (one per configuration, reused)."""
    return _client(
        secret_key(),
        _setting("BUCLI_API_BASE_URL", DEFAULT_BASE_URL),
        _setting("BUCLI_API_VERSION", API_VERSION),
        float(_setting("BUCLI_TIMEOUT", 15.0)),
        int(_setting("BUCLI_MAX_RETRIES", 2)),
    )


# -- metering (``bucli.contrib.django.metering``) -------------------------------------


def meter_on_unavailable() -> str | dict[str, str]:
    value = _setting("BUCLI_METER_ON_UNAVAILABLE", "deny")
    allowed = ("allow", "deny")
    if isinstance(value, dict):
        if any(policy not in allowed for policy in value.values()):
            raise ImproperlyConfigured('BUCLI_METER_ON_UNAVAILABLE: cada medidor es "allow" o "deny".')
        return dict(value)
    if value not in allowed:
        raise ImproperlyConfigured('BUCLI_METER_ON_UNAVAILABLE debe ser "allow", "deny" o {medidor: política}.')
    return value


def meter_keys() -> list[str]:
    """The meters named in the settings (to drop a customer's cached balances)."""
    value = _setting("BUCLI_METER_ON_UNAVAILABLE", "deny")
    return list(value) if isinstance(value, dict) else list(_setting("BUCLI_METERS", []))


def meter_timeout() -> float:
    return float(_setting("BUCLI_METER_TIMEOUT", 2.0))


def meter_cache_seconds() -> int:
    return int(_setting("BUCLI_METER_CACHE_SECONDS", 300))


def meter_max_report_attempts() -> int:
    return int(_setting("BUCLI_METER_MAX_REPORT_ATTEMPTS", 20))
