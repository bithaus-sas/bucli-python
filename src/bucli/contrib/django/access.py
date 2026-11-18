"""The access rule, the same in every SaaS that bills with Bucli.

=========================================  ======================================
Estado en Bucli                            ``access_level``
=========================================  ======================================
``trialing``, ``active``                   ``FULL``
``past_due``                               ``WARNING`` (completo, con aviso de
                                           «actualiza tu tarjeta»); pasado
                                           ``BUCLI_GRACE`` desde el fin del
                                           periodo, como un estado vencido
``paused``, ``canceled``,                  ``FREE`` o ``READ_ONLY`` según
``incomplete_expired``, sin suscripción    ``BUCLI_LAPSED_ACCESS``
``incomplete``                             ``NONE``: nada nuevo, todavía no pagó
=========================================  ======================================

It reads only the local copy (``BillingAccount``): no call to Bucli. The plan's
limits (users, messages, documents) stay in the SaaS; Bucli only says which
plan it has and whether it is up to date.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from bucli.constants import ACTIVE, INCOMPLETE, PAST_DUE, TRIALING
from bucli.contrib.django import conf
from bucli.contrib.django.models import BillingAccount

FULL = "full"
WARNING = "warning"
FREE = "free"
READ_ONLY = "read_only"
NONE = "none"

LEVELS = (FULL, WARNING, FREE, READ_ONLY, NONE)


def billing_account_of(account) -> BillingAccount | None:
    """The ``BillingAccount`` of an account of the SaaS (or itself), without creating it."""
    if account is None:
        return None
    if isinstance(account, BillingAccount):
        return account
    try:
        return account.bucli_billing
    except BillingAccount.DoesNotExist:
        return None


def _lapsed() -> str:
    return READ_ONLY if conf.lapsed_access() == conf.LAPSED_READ_ONLY else FREE


def access_level(account, *, now: datetime.datetime | None = None) -> str:
    """One of ``FULL``, ``WARNING``, ``FREE``, ``READ_ONLY``, ``NONE``."""
    billing = billing_account_of(account)
    status = billing.status if billing else ""
    if status in (TRIALING, ACTIVE):
        return FULL
    if status == PAST_DUE:
        grace = conf.grace()
        end = billing.current_period_end
        if grace is not None and end is not None and (now or timezone.now()) > end + grace:
            return _lapsed()
        return WARNING
    if status == INCOMPLETE:
        return NONE
    return _lapsed()


def has_access(account, *, now: datetime.datetime | None = None) -> bool:
    """The paid plan applies (``FULL`` or ``WARNING``)."""
    return access_level(account, now=now) in (FULL, WARNING)


def needs_payment_update(account, *, now: datetime.datetime | None = None) -> bool:
    """Show the «actualiza tu tarjeta» notice (``past_due`` while it still has access)."""
    return access_level(account, now=now) == WARNING


def is_read_only(account, *, now: datetime.datetime | None = None) -> bool:
    return access_level(account, now=now) == READ_ONLY


def current_plan(account, *, now: datetime.datetime | None = None) -> str | None:
    """The plan whose limits apply now: the paid one, ``BUCLI_FREE_PLAN`` when the
    rule falls back to free, or ``None`` (read-only).

    ``incomplete`` grants nothing new: it keeps the fallback (the free plan when
    ``BUCLI_LAPSED_ACCESS`` is ``"free"``), never the plan it has not paid yet."""
    level = access_level(account, now=now)
    if level in (FULL, WARNING):
        billing = billing_account_of(account)
        return (billing.plan or None) if billing else None
    if level == FREE or (level == NONE and _lapsed() == FREE):
        return conf.free_plan()
    return None
