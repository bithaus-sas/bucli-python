"""What the Bucli API promises and this library relies on.

Mirrors Bucli's API constants; ``tests/test_contract.py`` fails when they
drift apart.
"""

#: The date-version this library is written against (``Bucli-Version``).
API_VERSION = "2026-11-01"

DEFAULT_BASE_URL = "https://api.bucli.co"
API_PREFIX = "/v1"

#: Headers of the API.
VERSION_HEADER = "Bucli-Version"
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
REQUEST_ID_HEADER = "Request-Id"
SIGNATURE_HEADER = "Bucli-Signature"
SIGNATURE_SCHEME = "v1"
SIGNATURE_TOLERANCE_SECONDS = 300

#: Key prefixes. The key decides the mode; a publishable key (``pk_``) lives
#: in a browser and has no business in a server library.
SECRET_KEY_PREFIXES = ("sk_test_", "sk_live_")
PUBLISHABLE_KEY_PREFIXES = ("pk_test_", "pk_live_")

#: Subscription statuses (the subscription statuses of Bucli's API).
INCOMPLETE = "incomplete"
INCOMPLETE_EXPIRED = "incomplete_expired"
TRIALING = "trialing"
ACTIVE = "active"
PAST_DUE = "past_due"
CANCELED = "canceled"
PAUSED = "paused"
SUBSCRIPTION_STATUSES = (INCOMPLETE, INCOMPLETE_EXPIRED, TRIALING, ACTIVE, PAST_DUE, CANCELED, PAUSED)
#: A subscription that is still billing (Bucli's ``RUNNING``).
RUNNING_STATUSES = (TRIALING, ACTIVE, PAST_DUE)
#: A subscription that will never bill again.
ENDED_STATUSES = (CANCELED, INCOMPLETE_EXPIRED)

#: Every event type Bucli sends (the event types of Bucli's API).
EVENT_TYPES = (
    "customer.created",
    "customer.updated",
    "customer.deleted",
    "product.created",
    "product.updated",
    "subscription.created",
    "subscription.updated",
    "subscription.canceled",
    "subscription.past_due",
    "subscription.update_failed",
    "subscription.trial_will_end",
    "invoice.created",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.voided",
    "invoice.updated",
    "notice.sent",
    "charge.refunded",
    "invoice.fiscal_document.issued",
    "invoice.fiscal_document.rejected",
    "payment_method.attached",
    "payment_method.detached",
    "payment_method.suspended",
    "payment_method.expiring",
    "checkout.session.completed",
    "checkout.session.expired",
    "entitlements.updated",
    "meter.threshold_reached",
    "meter.exhausted",
    "grant.created",
    "grant.expired",
    "grant.withdrawn",
)

#: A product's ``storefront.placement``: a card among the main plans, or a
#: line under them (sold, but not as a card).
PLACEMENT_MAIN = "main"
PLACEMENT_SECONDARY = "secondary"
#: A product's ``kind``, read by Bucli from its rules and prices.
PRODUCT_KIND_PLAN = "plan"
PRODUCT_KIND_ADDON = "addon"
PRODUCT_KIND_ONE_TIME = "one_time"
PRODUCT_KIND_TRIAL = "trial"

#: A customer's access in its entitlements.
ACCESS_FULL = "full"
ACCESS_READ_ONLY = "read_only"
ACCESS_NONE = "none"
#: ``-1`` in a limit or a balance: unlimited.
UNLIMITED = -1
