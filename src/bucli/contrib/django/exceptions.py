"""Refusals of the Django services (the API's own errors stay ``bucli.BucliError``)."""


class BillingStateError(ValueError):
    """The account is not in a state that allows this action."""


class AlreadySubscribed(BillingStateError):  # noqa: N818 — reads as a state, like Django's DoesNotExist
    """It already has a running subscription: change its plan or open the portal instead."""


class NotSubscribed(BillingStateError):  # noqa: N818
    """It has no Bucli customer or subscription yet."""


class UnknownPrice(BillingStateError):  # noqa: N818
    """The price is not in ``BUCLI_PRICE_PLANS``."""
