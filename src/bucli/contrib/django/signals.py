"""Signals, sent AFTER the transaction that stored the change commits
(``send_robust``: a failing receiver is logged, never undoes the update).

``subscription_changed(sender=BillingAccount, account, billing_account,
previous_status, status, previous_plan, plan)``
    The local copy changed (status, plan, period, cancellation…). ``account``
    is the instance of ``BUCLI_ACCOUNT_MODEL``.

``event_processed(sender=BillingAccount, event, account, billing_account,
invoice, outcome)``
    Once per verified webhook event (``event`` is the delivered event,
    ``invoice`` the fresh invoice for ``invoice.*`` and ``charge.refunded``
    events, or ``None``). Use it for e-mails on ``invoice.payment_failed``, to
    keep the DIAN PDF of ``invoice.fiscal_document.issued``, to put a
    ``notice.sent`` in the account's bell (``event["data"]["object"]``) or to
    tell the user that a prorated change failed (``subscription.update_failed``).
"""

from django.dispatch import Signal

subscription_changed = Signal()
event_processed = Signal()
