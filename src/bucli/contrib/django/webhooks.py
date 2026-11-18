"""Handling one verified Bucli event.

1. **Deduplicated** by ``event.id`` (``ProcessedEvent``): a redelivery does nothing.
2. **Never trusts the order** nor the payload's snapshot: it asks Bucli for the
   object again (``GET /v1/subscriptions/{id}``…) and stores THAT; the
   object's ``updated`` (then ``sequence``) keeps an older copy from
   overwriting a newer one (``services.store_subscription``).
3. **One transaction** records the event and updates the ``BillingAccount``;
   signals go after the commit.

A failure talking to Bucli raises (``bucli.BucliError``): the view answers 503
and Bucli delivers the event again later, since nothing was recorded.
"""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from bucli.client import Bucli
from bucli.contrib.django import conf
from bucli.contrib.django.models import BillingAccount, ProcessedEvent
from bucli.contrib.django.services import ACCOUNT_METADATA_KEY, STALE, account_from_label, ref, store_subscription
from bucli.contrib.django.signals import event_processed
from bucli.errors import InvalidRequestError

logger = logging.getLogger("bucli.contrib.django")

SUBSCRIPTION_EVENTS = frozenset(
    {
        "subscription.created",
        "subscription.updated",
        "subscription.canceled",
        "subscription.past_due",
        "subscription.update_failed",
        "subscription.trial_will_end",
    }
)
INVOICE_EVENTS = frozenset(
    {
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.fiscal_document.issued",
        "invoice.fiscal_document.rejected",
    }
)
CHECKOUT_EVENTS = frozenset({"checkout.session.completed"})
#: A refund (``charge.refunded``): its invoice is fetched and handed to the signal.
REFUND_EVENTS = frozenset({"charge.refunded"})
#: A notice Bucli sent the customer (``notice.sent``): matched to the account for
#: the signal (put it in the SaaS's bell); there is no endpoint to fetch it again.
NOTICE_EVENTS = frozenset({"notice.sent"})
#: The events the app acts on; subscribe your Bucli endpoint to these.
HANDLED_EVENTS = SUBSCRIPTION_EVENTS | INVOICE_EVENTS | CHECKOUT_EVENTS | REFUND_EVENTS | NOTICE_EVENTS

# Outcomes (``ProcessedEvent.outcome`` and the webhook's answer).
DUPLICATE = "duplicate"
IGNORED = "ignored"
WRONG_MODE = "wrong_mode"
UNMATCHED = "unmatched"
MISSING = "missing"


def _account_by_reference(reference: str) -> BillingAccount | None:
    """``client_reference_id`` is the SaaS account's pk (``start_checkout``)."""
    if not reference:
        return None
    try:
        billing = BillingAccount.objects.filter(account_id=reference).first()
        if billing is not None:
            return billing
        model = apps.get_model(conf.account_model_label())
        account = model._default_manager.filter(pk=reference).first()
    except (ValueError, TypeError, ValidationError):
        return None
    if account is None:
        return None
    billing, _ = BillingAccount.objects.get_or_create(account=account)
    return billing


def find_billing_account(*, subscription_id: str = "", customer_id: str = "", reference: str = "", label: str = ""):
    """Subscription id first, then the checkout's reference, then the
    subscription's ``metadata.bucli_account`` (imported subscriptions), then the
    customer (only when exactly one account has it: Bucli keeps one customer per
    e-mail)."""
    if subscription_id:
        billing = BillingAccount.objects.filter(bucli_subscription_id=subscription_id).first()
        if billing is not None:
            return billing
    billing = _account_by_reference(reference)
    if billing is not None:
        return billing
    account = account_from_label(label)
    if account is not None:
        billing, _ = BillingAccount.objects.get_or_create(account=account)
        return billing
    if customer_id:
        matches = list(BillingAccount.objects.filter(bucli_customer_id=customer_id)[:2])
        if len(matches) == 1:
            return matches[0]
    return None


def _fetch(event: dict, api: Bucli) -> dict[str, Any]:
    """The fresh objects behind ``event``: ``subscription``, ``invoice``,
    ``customer_id``, ``reference``."""
    event_type = event["type"]
    obj = (event.get("data") or {}).get("object") or {}
    found: dict[str, Any] = {"subscription": None, "invoice": None, "customer_id": "", "reference": ""}
    subscription_id = ""
    if event_type in SUBSCRIPTION_EVENTS:
        subscription_id = obj.get("id") or ""
    elif event_type in INVOICE_EVENTS:
        invoice = api.invoices.retrieve(obj["id"])
        found["invoice"] = invoice
        found["customer_id"] = ref(invoice.get("customer"))
        subscription_id = ref(invoice.get("subscription"))
    elif event_type in CHECKOUT_EVENTS:
        session = api.checkout_sessions.retrieve(obj["id"])
        found["customer_id"] = ref(session.get("customer"))
        found["reference"] = session.get("client_reference_id") or ""
        subscription_id = ref(session.get("subscription"))
    elif event_type in REFUND_EVENTS:
        refund = api.refunds.retrieve(obj["id"])
        invoice_id = ref(refund.get("invoice"))
        if invoice_id:
            invoice = api.invoices.retrieve(invoice_id)
            found["invoice"] = invoice
            found["customer_id"] = ref(invoice.get("customer"))
        subscription_id = ref(refund.get("subscription"))
    elif event_type in NOTICE_EVENTS:
        # Frozen when sent: the payload is the notice; only its subscription is fetched.
        found["customer_id"] = ref(obj.get("customer"))
        subscription_id = ref(obj.get("subscription"))
    if subscription_id:
        subscription = api.subscriptions.retrieve(subscription_id)
        found["subscription"] = subscription
        found["customer_id"] = found["customer_id"] or ref(subscription.get("customer"))
    return found


def _record(event: dict, outcome: str, billing: BillingAccount | None = None) -> ProcessedEvent | None:
    try:
        with transaction.atomic():
            return ProcessedEvent.objects.create(
                event_id=event["id"],
                type=event.get("type") or "",
                sequence=event.get("sequence"),
                livemode=bool(event.get("livemode")),
                billing_account=billing,
                outcome=outcome,
            )
    except IntegrityError:
        return None


def _notify(event: dict, billing: BillingAccount | None, invoice: Any, outcome: str) -> None:
    def send() -> None:
        results = event_processed.send_robust(
            sender=BillingAccount,
            event=event,
            account=billing.account if billing else None,
            billing_account=billing,
            invoice=invoice,
            outcome=outcome,
        )
        for receiver, result in results:
            if isinstance(result, Exception):
                logger.error("Bucli: event_processed receiver %r failed: %s", receiver, result)

    transaction.on_commit(send)


def handle_event(event: dict, *, client: Bucli | None = None) -> str:
    """Process a verified event; answers its outcome (``applied``, ``duplicate``…)."""
    event_id = event.get("id")
    event_type = event.get("type") or ""
    if not event_id:
        raise ValueError("El evento no trae id.")
    if ProcessedEvent.objects.filter(event_id=event_id).exists():
        return DUPLICATE
    api = client or conf.get_client()

    if bool(event.get("livemode")) != api.livemode:
        # A test event reaching production (or the other way): never applied.
        logger.warning("Bucli: event %s is from the other mode; ignored", event_id)
        with transaction.atomic():
            return WRONG_MODE if _record(event, WRONG_MODE) else DUPLICATE
    if event_type not in HANDLED_EVENTS:
        with transaction.atomic():
            if _record(event, IGNORED) is None:
                return DUPLICATE
            _notify(event, None, None, IGNORED)
        return IGNORED

    try:
        found = _fetch(event, api)
    except InvalidRequestError as error:
        if error.code != "resource_missing":
            raise
        logger.warning("Bucli: the object of event %s no longer exists", event_id)
        with transaction.atomic():
            return MISSING if _record(event, MISSING) else DUPLICATE

    subscription = found["subscription"]
    billing = find_billing_account(
        subscription_id=subscription["id"] if subscription else "",
        customer_id=found["customer_id"],
        reference=found["reference"],
        label=((subscription or {}).get("metadata") or {}).get(ACCOUNT_METADATA_KEY, ""),
    )
    with transaction.atomic():
        processed = _record(event, UNMATCHED, billing)
        if processed is None:
            return DUPLICATE
        if billing is None:
            logger.info("Bucli: event %s (%s) matches no account", event_id, event_type)
            outcome = UNMATCHED
        elif subscription is None:
            if found["customer_id"] and not billing.bucli_customer_id:
                BillingAccount.objects.filter(pk=billing.pk).update(bucli_customer_id=found["customer_id"])
                billing.bucli_customer_id = found["customer_id"]
            outcome = IGNORED  # e.g. a one-off invoice: nothing to copy, the signal tells the SaaS
        else:
            outcome = store_subscription(billing, subscription, sequence=event.get("sequence"))
        if outcome != UNMATCHED:
            ProcessedEvent.objects.filter(pk=processed.pk).update(outcome=outcome)
        _notify(event, billing, found["invoice"], outcome)
    if outcome == STALE:
        logger.info("Bucli: event %s is older than the stored copy; kept the copy", event_id)
    return outcome
