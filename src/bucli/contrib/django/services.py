"""What the SaaS calls: subscribe, open the portal, change plan, cancel, sync.

Every function takes the SaaS's account (an instance of ``BUCLI_ACCOUNT_MODEL``)
and talks to Bucli OUTSIDE any database transaction; the local copy is written
only by ``store_subscription``, from an object Bucli just answered.

Nothing here activates a plan from ``success_url``: the activation comes from
the webhook (``views.BucliWebhookView``) or from ``sync``.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from bucli.client import Bucli
from bucli.constants import ENDED_STATUSES, RUNNING_STATUSES
from bucli.contrib.django import conf
from bucli.contrib.django.exceptions import AlreadySubscribed, NotSubscribed, UnknownPrice
from bucli.contrib.django.models import BillingAccount
from bucli.contrib.django.signals import subscription_changed
from bucli.errors import InvalidRequestError
from bucli.objects import BucliObject

logger = logging.getLogger("bucli.contrib.django")

#: The fields of the local copy whose change fires ``subscription_changed``.
TRACKED_FIELDS = (
    "bucli_subscription_id",
    "bucli_customer_id",
    "status",
    "price_id",
    "plan",
    "pending_price_id",
    "current_period_end",
    "trial_end",
    "cancel_at_period_end",
    "items",
    "livemode",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ref(value: Any) -> str:
    """The id of a related field, expanded (``{"id": ...}``) or not."""
    if isinstance(value, dict):
        return value.get("id") or ""
    return value or ""


def parse_datetime(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, int | float):
        return datetime.datetime.fromtimestamp(value, tz=datetime.UTC)
    parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


#: The metadata key that names the SaaS account on Bucli's objects
#: (``"tenants.tenant:42"``): the customers and checkouts ``start_checkout``
#: creates carry it, and an import file should put it on each subscription.
ACCOUNT_METADATA_KEY = "bucli_account"


def account_label(account) -> str:
    return f"{account._meta.label_lower}:{account.pk}"


def account_from_label(label: str):
    """The SaaS account a ``bucli_account`` label names, or ``None``."""
    model_label, sep, pk = (label or "").rpartition(":")
    if not sep or not pk or model_label != conf.account_model_label().lower():
        return None
    model = apps.get_model(conf.account_model_label())
    try:
        return model._default_manager.filter(pk=pk).first()
    except (ValueError, TypeError, ValidationError):
        return None


def _client(client: Bucli | None) -> Bucli:
    return client or conf.get_client()


def get_billing_account(account) -> BillingAccount:
    """The ``BillingAccount`` of ``account`` (or itself), created when missing."""
    if isinstance(account, BillingAccount):
        return account
    try:
        # The instance cached on ``account.bucli_billing``, re-read: what the
        # services store on it is what ``access.has_access(account)`` reads next.
        billing = account.bucli_billing
        billing.refresh_from_db()
    except BillingAccount.DoesNotExist:
        billing, _ = BillingAccount.objects.get_or_create(account=account)
        account.bucli_billing = billing
    return billing


def subscription_items(subscription: dict) -> list[dict]:
    items = subscription.get("items") or {}
    return list(items.get("data") or []) if isinstance(items, dict) else []


def base_item(subscription: dict, plans: dict[str, str] | None = None) -> tuple[dict | None, str]:
    """``(item, price_id)`` of the item that sets the plan: the first whose price is
    in ``BUCLI_PRICE_PLANS``, else the subscription's ``price`` (its first item)."""
    plans = conf.price_plans() if plans is None else plans
    items = subscription_items(subscription)
    for item in items:
        price_id = ref(item.get("price"))
        if price_id in plans:
            return item, price_id
    first = items[0] if items else None
    return first, ref(subscription.get("price")) or (ref(first.get("price")) if first else "")


def _snapshot(billing: BillingAccount) -> dict[str, Any]:
    return {field: getattr(billing, field) for field in TRACKED_FIELDS}


def _should_track(billing: BillingAccount, subscription: dict) -> bool:
    """A subscription other than the tracked one replaces it only when it runs,
    or when the tracked one is over (an old canceled subscription never
    overwrites the active one)."""
    current = billing.bucli_subscription_id
    if not current or current == subscription.get("id"):
        return True
    if subscription.get("status") in RUNNING_STATUSES:
        return True
    return billing.status in ENDED_STATUSES or not billing.status


def _apply(billing: BillingAccount, subscription: dict) -> None:
    plans = conf.price_plans()
    _item, price_id = base_item(subscription, plans)
    billing.bucli_subscription_id = subscription.get("id") or ""
    billing.bucli_customer_id = ref(subscription.get("customer")) or billing.bucli_customer_id
    billing.status = subscription.get("status") or ""
    billing.price_id = price_id
    billing.plan = plans.get(price_id, "")
    if price_id and price_id not in plans:
        logger.warning("Bucli: price %s of subscription %s is not in BUCLI_PRICE_PLANS", price_id, billing.pk)
    billing.pending_price_id = ref(subscription.get("pending_price"))
    billing.current_period_end = parse_datetime(subscription.get("current_period_end"))
    billing.trial_end = parse_datetime(subscription.get("trial_end"))
    billing.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
    billing.livemode = bool(subscription.get("livemode"))
    billing.bucli_updated_at = parse_datetime(subscription.get("updated"))
    billing.items = [
        {
            "id": item.get("id"),
            "price": ref(item.get("price")),
            "quantity": item.get("quantity"),
            "plan": plans.get(ref(item.get("price"))),
        }
        for item in subscription_items(subscription)
    ]


# ---------------------------------------------------------------------------
# The one writer of the local copy
# ---------------------------------------------------------------------------

#: ``store_subscription`` outcomes.
APPLIED = "applied"
UNCHANGED = "unchanged"
STALE = "stale"
NOT_TRACKED = "not_tracked"


def _freshness(billing: BillingAccount, subscription: dict) -> int | None:
    """How ``subscription`` compares with the local copy of the SAME
    subscription by Bucli's ``updated``: ``1`` newer, ``0`` same moment,
    ``-1`` older; ``None`` when it cannot be told (another subscription, or a
    side without ``updated``)."""
    if not billing.bucli_subscription_id or billing.bucli_subscription_id != subscription.get("id"):
        return None
    try:
        incoming = parse_datetime(subscription.get("updated"))
    except (TypeError, ValueError):
        return None
    stored = billing.bucli_updated_at
    if incoming is None or stored is None:
        return None
    return (incoming > stored) - (incoming < stored)


def store_subscription(billing: BillingAccount, subscription: dict, *, sequence: int | None = None) -> str:
    """Write ``subscription`` (fresh from Bucli) on the local copy, under the row's lock.

    Bucli's ``updated`` orders the copies of one subscription: one OLDER than
    the local copy is ``STALE`` — never overwrites it, from an event or from
    a sync —, a newer one always applies. When ``updated`` cannot tell (the
    same second, another subscription, an answer without it), ``sequence``
    does: an event older than the last applied one is ``STALE`` (the stored
    copy was fetched after a later event, so it already includes this
    change). A sync passes no sequence. Fires ``subscription_changed`` after
    the commit when something changed."""
    now = timezone.now()
    with transaction.atomic():
        locked = BillingAccount.objects.select_for_update().get(pk=billing.pk)
        freshness = _freshness(locked, subscription)
        if freshness == -1:
            return STALE
        if freshness != 1 and sequence is not None and sequence < locked.last_event_sequence:
            return STALE
        if not _should_track(locked, subscription):
            return NOT_TRACKED
        before = _snapshot(locked)
        _apply(locked, subscription)
        if sequence is not None:
            locked.last_event_sequence = max(sequence, locked.last_event_sequence)
            locked.last_event_at = now
        locked.last_synced_at = now
        locked.save()
        after = _snapshot(locked)
        for field in (
            *TRACKED_FIELDS,
            "bucli_updated_at",
            "last_event_sequence",
            "last_event_at",
            "last_synced_at",
            "updated_at",
        ):
            setattr(billing, field, getattr(locked, field))
        if before == after:
            return UNCHANGED

        def notify() -> None:
            results = subscription_changed.send_robust(
                sender=BillingAccount,
                account=locked.account,
                billing_account=locked,
                previous_status=before["status"],
                status=after["status"],
                previous_plan=before["plan"],
                plan=after["plan"],
            )
            for receiver, result in results:
                if isinstance(result, Exception):
                    logger.error("Bucli: subscription_changed receiver %r failed: %s", receiver, result)

        transaction.on_commit(notify)
        return APPLIED


# ---------------------------------------------------------------------------
# What the SaaS calls
# ---------------------------------------------------------------------------


def _check_price(price_id: str) -> None:
    if price_id not in conf.price_plans():
        raise UnknownPrice(f"El precio {price_id} no está en BUCLI_PRICE_PLANS.")


def _find_customer(api: Bucli, *, external_id: str | None, email: str) -> str:
    """The id of the customer with that ``external_id`` or, without one, that
    e-mail — or ``""``."""
    found = (
        api.customers.list(external_id=external_id, limit=1)
        if external_id
        else api.customers.list(email=email, limit=1)
    )
    return found.data[0]["id"] if found.data else ""


def ensure_customer(
    account,
    email: str,
    *,
    name: str | None = None,
    external_id: str | None = None,
    phone: str | None = None,
    fiscal: dict[str, Any] | None = None,
    client: Bucli | None = None,
) -> BillingAccount:
    """The account's Bucli customer: the linked one, the one found (by
    ``external_id`` when given — one customer per account, two companies of
    one owner never mixed — else by e-mail) or a new one. Saves the link.

    ``fiscal`` is the customer's data for the electronic invoice (see
    ``DATOS-DEL-CLIENTE.md``): sent when the customer is created; an existing
    customer gets it with ``sync_customer``."""
    billing = get_billing_account(account)
    if billing.bucli_customer_id:
        return billing
    if external_id is not None:
        external_id = str(external_id)
    api = _client(client)
    customer_id = _find_customer(api, external_id=external_id, email=email)
    if not customer_id:
        metadata = {ACCOUNT_METADATA_KEY: account_label(billing.account)}
        try:
            customer_id = api.customers.create(
                email=email, name=name, external_id=external_id, phone=phone, fiscal=fiscal, metadata=metadata
            )["id"]
        except InvalidRequestError as error:
            if error.code != "resource_already_exists":
                raise
            customer_id = _find_customer(api, external_id=external_id, email=email)
            if not customer_id:
                raise
    BillingAccount.objects.filter(pk=billing.pk).update(bucli_customer_id=customer_id, updated_at=timezone.now())
    billing.bucli_customer_id = customer_id
    return billing


#: What ``sync_customer`` may change on the Bucli customer.
CUSTOMER_FIELDS = ("email", "name", "phone", "fiscal")


def sync_customer(account, *, client: Bucli | None = None, **fields: Any) -> BucliObject | None:
    """Send the account's current data to its Bucli customer: ``email``,
    ``name``, ``phone`` and ``fiscal`` (whole: it replaces the previous one).
    ``None`` = not sent (an account whose fiscal data are still incomplete
    sends no ``fiscal``). Call it after the account's billing data change
    (from a task, after the commit). Without a linked customer it does
    nothing (``ensure_customer`` sends the data when the customer is
    created)."""
    unknown = sorted(set(fields) - set(CUSTOMER_FIELDS))
    if unknown:
        raise ValueError(f"sync_customer no envía «{unknown[0]}»: solo {', '.join(CUSTOMER_FIELDS)}.")
    billing = get_billing_account(account)
    if not billing.bucli_customer_id or not fields:
        return None
    params = {key: value for key, value in fields.items() if value is not None}
    if not params:
        return None
    return _client(client).customers.update(billing.bucli_customer_id, **params)


def _line_items(items: Any) -> list[dict[str, Any]]:
    """``items`` as Bucli's ``line_items``: ``"price_…"``, ``("price_…", 3)`` or
    ``{"price": "price_…", "quantity": 3}`` each (quantity 1 by default)."""
    lines = []
    for item in items:
        if isinstance(item, str):
            price, quantity = item, None
        elif isinstance(item, dict):
            price, quantity = item.get("price"), item.get("quantity")
        else:
            price, quantity = item
        if not price:
            raise ValueError("Cada ítem necesita su price (price_…).")
        lines.append({"price": price, **({"quantity": quantity} if quantity is not None else {})})
    if not lines:
        raise ValueError("Envía al menos un ítem.")
    return lines


def start_checkout(
    account,
    price_id: str | None = None,
    success_url: str | None = None,
    cancel_url: str | None = None,
    email: str | None = None,
    *,
    items: list[Any] | None = None,
    quantity: int | None = None,
    name: str | None = None,
    external_id: str | None = None,
    phone: str | None = None,
    fiscal: dict[str, Any] | None = None,
    customer_fields: list[str] | None = None,
    trial_days: int | None = None,
    metadata: dict[str, str] | None = None,
    subscription_metadata: dict[str, str] | None = None,
    client: Bucli | None = None,
) -> BucliObject:
    """The «Suscribirme» button: creates (or reuses) the Bucli customer and a
    checkout session; redirect the user to ``session.url``.

    One plan (``price_id``, + ``quantity``) or several items
    (``items=[("price_pro", 1), {"price": "price_seat", "quantity": 3}]``: the plan,
    extra seats, add-ons; at least one price must be in ``BUCLI_PRICE_PLANS``).
    The subscription it creates carries ``metadata.bucli_account`` (plus
    ``subscription_metadata``), so a webhook or ``discover`` finds the account.

    The account is NOT activated here, nor at ``success_url``: the webhook does
    it when Bucli says the subscription runs."""
    if not success_url or not email:
        raise ValueError("start_checkout necesita success_url y email.")
    if (price_id is None) == (items is None):
        raise ValueError("Envía price_id o items, uno de los dos.")
    if items is not None:
        if quantity is not None:
            raise ValueError("Con items, cada ítem lleva su quantity.")
        lines = _line_items(items)
        plans = conf.price_plans()
        if not any(line["price"] in plans for line in lines):
            raise UnknownPrice("Ninguno de los precios de items está en BUCLI_PRICE_PLANS.")
    else:
        _check_price(price_id)
        lines = None
    billing = get_billing_account(account)
    if billing.is_running:
        raise AlreadySubscribed("La cuenta ya tiene una suscripción en curso: cambia el plan o abre el portal.")
    linked = bool(get_billing_account(account).bucli_customer_id)
    billing = ensure_customer(
        account, email, name=name, external_id=external_id, phone=phone, fiscal=fiscal, client=client
    )
    if linked:
        # An account linked earlier: its customer gets what it brings now.
        sync_customer(account, phone=phone, fiscal=fiscal, client=client)
    label = {ACCOUNT_METADATA_KEY: account_label(billing.account)}
    return _client(client).checkout_sessions.create(
        price=price_id,
        quantity=quantity,
        line_items=lines,
        success_url=success_url,
        cancel_url=cancel_url,
        customer=billing.bucli_customer_id,
        customer_fields=customer_fields,
        trial_days=trial_days,
        client_reference_id=str(billing.account.pk),
        metadata={**label, **(metadata or {})},
        subscription_data={"metadata": {**(subscription_metadata or {}), **label}},
    )


def portal_url(account, return_url: str | None = None, *, client: Bucli | None = None) -> str:
    """The customer portal (card, invoices, data, cancel); valid one hour — create it per click."""
    billing = get_billing_account(account)
    if not billing.bucli_customer_id:
        raise NotSubscribed("La cuenta todavía no tiene cliente en Bucli.")
    return _client(client).portal_sessions.create(customer=billing.bucli_customer_id, return_url=return_url)["url"]


def _running_subscription(billing: BillingAccount) -> str:
    if not billing.bucli_subscription_id or billing.is_ended:
        raise NotSubscribed("La cuenta no tiene una suscripción en curso.")
    return billing.bucli_subscription_id


PRORATE = "create_prorations"
NO_PRORATION = "none"


def preview_plan_change(account, price_id: str, *, client: Bucli | None = None) -> BucliObject:
    """What changing the plan to ``price_id`` costs NOW (a ``subscription_quote``):
    show its ``amount`` and lines, then call ``change_plan(...,
    proration_behavior="create_prorations", expected_amount=quote["amount"])``.
    Nothing changes."""
    _check_price(price_id)
    billing = get_billing_account(account)
    api = _client(client)
    subscription = api.subscriptions.retrieve(_running_subscription(billing))
    items = subscription_items(subscription)
    if len(items) <= 1:
        return api.subscriptions.preview(subscription["id"], price=price_id)
    item, _current = base_item(subscription)
    if item is None or not item.get("id"):
        raise NotSubscribed("No se encontró el ítem del plan en la suscripción.")
    target = [
        {"id": row["id"], "price": price_id if row.get("id") == item["id"] else ref(row.get("price"))}
        | ({"quantity": row["quantity"]} if row.get("quantity") is not None else {})
        for row in items
    ]
    return api.subscriptions.preview(subscription["id"], items=target)


def change_plan(
    account,
    price_id: str,
    *,
    proration_behavior: str = NO_PRORATION,
    expected_amount: int | None = None,
    client: Bucli | None = None,
) -> BillingAccount:
    """Change the plan (the item whose price is in ``BUCLI_PRICE_PLANS``).

    ``proration_behavior="none"`` (the default): it applies now and is billed
    from the next period. ``"create_prorations"`` with ``expected_amount`` (the
    ``amount`` of ``preview_plan_change``): an upgrade is charged now, prorated,
    and applies when Wompi approves it (the webhook updates the account; a
    failure arrives as ``subscription.update_failed``); a downgrade waits for the
    renewal. A stale amount raises ``bucli.InvalidRequestError`` with
    ``code == "quote_changed"`` and the new quote in ``error.quote``."""
    _check_price(price_id)
    if proration_behavior not in (NO_PRORATION, PRORATE):
        raise ValueError('proration_behavior es "none" o "create_prorations".')
    if proration_behavior == PRORATE and expected_amount is None:
        raise ValueError("Con create_prorations envía expected_amount: el amount de preview_plan_change.")
    change = {"proration_behavior": proration_behavior}
    if proration_behavior == PRORATE:
        change["expected_amount"] = expected_amount
    billing = get_billing_account(account)
    api = _client(client)
    subscription = api.subscriptions.retrieve(_running_subscription(billing))
    items = subscription_items(subscription)
    if len(items) <= 1:
        updated = api.subscriptions.update(subscription["id"], price=price_id, **change)
        # Only a subscription is ever stored: an answer of another kind (a pending
        # change) is followed by the subscription as it is now.
        is_subscription = isinstance(updated, dict) and updated.get("object") == "subscription"
        subscription = updated if is_subscription else api.subscriptions.retrieve(subscription["id"])
    else:
        item, current = base_item(subscription)
        if item is None or not item.get("id"):
            raise NotSubscribed("No se encontró el ítem del plan en la suscripción.")
        if current != price_id:
            api.subscription_items.update(item["id"], price=price_id, **change)
        subscription = api.subscriptions.retrieve(subscription["id"])
    store_subscription(billing, subscription)
    return billing


def cancel(
    account,
    at_period_end: bool = True,
    *,
    reason: str | None = None,
    comment: str | None = None,
    client: Bucli | None = None,
) -> BillingAccount:
    """Cancel at the end of the period (default) or now."""
    billing = get_billing_account(account)
    subscription = _client(client).subscriptions.cancel(
        _running_subscription(billing), at_period_end=at_period_end, reason=reason, comment=comment
    )
    store_subscription(billing, subscription)
    return billing


def reactivate(account, *, client: Bucli | None = None) -> BillingAccount:
    """Undo a cancellation scheduled for the end of the period."""
    billing = get_billing_account(account)
    subscription = _client(client).subscriptions.update(_running_subscription(billing), cancel_at_period_end=False)
    store_subscription(billing, subscription)
    return billing


def _pick(candidates: list[dict], tracked: str) -> dict | None:
    """The subscription the account should follow: a running one (the tracked
    first), else the tracked one, else the newest."""
    running = [sub for sub in candidates if sub.get("status") in RUNNING_STATUSES]
    for sub in running:
        if sub.get("id") == tracked:
            return sub
    if running:
        return running[0]
    for sub in candidates:
        if sub.get("id") == tracked:
            return sub
    return candidates[0] if candidates else None


def sync(account, *, client: Bucli | None = None) -> BillingAccount:
    """Read the account's subscription(s) again from Bucli and store the right one.

    For missed webhooks, a migration or a support case (``manage.py bucli_sync``)."""
    billing = get_billing_account(account)
    api = _client(client)
    candidates: list[dict] = []
    if billing.bucli_customer_id:
        candidates = list(api.subscriptions.list(customer=billing.bucli_customer_id, limit=20).auto_paging_iter())
    if billing.bucli_subscription_id and not any(c.get("id") == billing.bucli_subscription_id for c in candidates):
        try:
            candidates.append(api.subscriptions.retrieve(billing.bucli_subscription_id))
        except InvalidRequestError as error:
            if error.code != "resource_missing":
                raise
    chosen = _pick(candidates, billing.bucli_subscription_id)
    if chosen is None:
        BillingAccount.objects.filter(pk=billing.pk).update(last_synced_at=timezone.now())
        return billing
    store_subscription(billing, chosen)
    return billing


def link(account, *, customer_id: str, subscription_id: str = "", client: Bucli | None = None) -> BillingAccount:
    """Tie an account to an existing Bucli customer (and subscription), then sync.

    For customers migrated with ``import_subscriptions`` whose subscriptions do
    not carry ``metadata.bucli_account``: use the ids of the import report."""
    billing = get_billing_account(account)
    fields = {"bucli_customer_id": customer_id, "updated_at": timezone.now()}
    if subscription_id:
        fields["bucli_subscription_id"] = subscription_id
    BillingAccount.objects.filter(pk=billing.pk).update(**fields)
    billing.refresh_from_db()
    return sync(billing, client=client)


def discover(*, client: Bucli | None = None) -> dict[str, int]:
    """Walk every Bucli subscription and store those whose ``metadata.bucli_account``
    names an account of the SaaS (``manage.py bucli_sync --discover``): how the
    migrated customers get their ``BillingAccount`` without a webhook."""
    counts = {"seen": 0, "linked": 0, "skipped": 0}
    for subscription in _client(client).subscriptions.list(limit=100).auto_paging_iter():
        counts["seen"] += 1
        metadata = subscription.get("metadata") or {}
        account = account_from_label(metadata.get(ACCOUNT_METADATA_KEY, ""))
        if account is None:
            counts["skipped"] += 1
            continue
        billing = get_billing_account(account)
        outcome = store_subscription(billing, subscription)
        counts["linked" if outcome in (APPLIED, UNCHANGED) else "skipped"] += 1
    return counts
