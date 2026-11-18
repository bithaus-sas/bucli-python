"""Bucli for Python: the ``/v1`` client, webhook verification and (optionally)
a Django app (``bucli.contrib.django``).

    import bucli

    client = bucli.Bucli("sk_test_…")
    customer = client.customers.create(email="ana@tienda.co")
    event = bucli.Webhook.construct_event(request.body, request.headers["Bucli-Signature"], "whsec_…")
"""

from bucli._version import __version__
from bucli.client import Bucli
from bucli.constants import API_VERSION, EVENT_TYPES, RUNNING_STATUSES, SUBSCRIPTION_STATUSES
from bucli.errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BillingError,
    BucliError,
    CardError,
    IdempotencyError,
    InvalidRequestError,
    PermissionError,  # noqa: A004 — Stripe's name, scoped to this package
    RateLimitError,
    SignatureVerificationError,
)
from bucli.objects import BucliObject, ListObject
from bucli.webhook import Webhook, WebhookSignature

__all__ = [
    "API_VERSION",
    "EVENT_TYPES",
    "RUNNING_STATUSES",
    "SUBSCRIPTION_STATUSES",
    "APIConnectionError",
    "APIError",
    "AuthenticationError",
    "BillingError",
    "Bucli",
    "BucliError",
    "BucliObject",
    "CardError",
    "IdempotencyError",
    "InvalidRequestError",
    "ListObject",
    "PermissionError",
    "RateLimitError",
    "SignatureVerificationError",
    "Webhook",
    "WebhookSignature",
    "__version__",
]
