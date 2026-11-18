"""Every resource method sends the documented method, path and body."""

import json
import re
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses

import bucli

from factories import API, error

CASES = [
    # -- entitlements and metering --
    (
        "customers.entitlements",
        lambda c: c.customers.entitlements("cus_1"),
        "GET",
        "/customers/cus_1/entitlements",
        None,
        None,
    ),
    (
        "subscriptions.entitlements",
        lambda c: c.subscriptions.entitlements("sub_1"),
        "GET",
        "/subscriptions/sub_1/entitlements",
        None,
        None,
    ),
    (
        "subscriptions.set_entitlement_overrides",
        lambda c: c.subscriptions.set_entitlement_overrides("sub_1", features_add=["payroll"], note="Acuerdo"),
        "POST",
        "/subscriptions/sub_1/entitlement_overrides",
        {"features_add": ["payroll"], "note": "Acuerdo"},
        None,
    ),
    (
        "features.create",
        lambda c: c.features.create(key="documents", name="Documentos", type="limit", limit_kind="metered"),
        "POST",
        "/features",
        {"key": "documents", "name": "Documentos", "type": "limit", "limit_kind": "metered"},
        None,
    ),
    ("features.list", lambda c: c.features.list(type="boolean"), "GET", "/features", None, {"type": "boolean"}),
    ("features.retrieve", lambda c: c.features.retrieve("feat_1"), "GET", "/features/feat_1", None, None),
    (
        "features.update",
        lambda c: c.features.update("feat_1", name="Docs"),
        "PATCH",
        "/features/feat_1",
        {"name": "Docs"},
        None,
    ),
    ("features.delete", lambda c: c.features.delete("feat_1"), "DELETE", "/features/feat_1", None, None),
    (
        "meters.create",
        lambda c: c.meters.create(key="documents", name="Documentos", on_unavailable="allow"),
        "POST",
        "/meters",
        {"key": "documents", "name": "Documentos", "on_unavailable": "allow"},
        None,
    ),
    ("meters.list", lambda c: c.meters.list(), "GET", "/meters", None, None),
    ("meters.retrieve", lambda c: c.meters.retrieve("documents"), "GET", "/meters/documents", None, None),
    (
        "meters.update",
        lambda c: c.meters.update("documents", threshold_percent=90),
        "PATCH",
        "/meters/documents",
        {"threshold_percent": 90},
        None,
    ),
    (
        "meters.summary",
        lambda c: c.meters.summary("documents", customer="cus_1"),
        "GET",
        "/meters/documents/summary",
        None,
        {"customer": "cus_1"},
    ),
    (
        "meters.reserve",
        lambda c: c.meters.reserve("documents", customer="cus_1", idempotency_key="doc-1"),
        "POST",
        "/meters/documents/reserve",
        {"customer": "cus_1", "idempotency_key": "doc-1"},
        None,
    ),
    (
        "usage.report",
        lambda c: c.usage.report(meter="documents", customer="cus_1", idempotency_key="doc-1", occurred_at=1767225600),
        "POST",
        "/usage",
        {"meter": "documents", "customer": "cus_1", "idempotency_key": "doc-1", "occurred_at": 1767225600},
        None,
    ),
    ("usage.list", lambda c: c.usage.list(meter="documents"), "GET", "/usage", None, {"meter": "documents"}),
    ("usage.retrieve", lambda c: c.usage.retrieve("usg_1"), "GET", "/usage/usg_1", None, None),
    ("usage.release", lambda c: c.usage.release("usg_1"), "POST", "/usage/usg_1/release", {}, None),
    (
        "grant_offers.create",
        lambda c: c.grant_offers.create(meter="documents", price="price_1", name="100", units=100),
        "POST",
        "/grant_offers",
        {"meter": "documents", "price": "price_1", "name": "100", "units": 100},
        None,
    ),
    ("grant_offers.list", lambda c: c.grant_offers.list(active=True), "GET", "/grant_offers", None, {"active": "true"}),
    ("grant_offers.retrieve", lambda c: c.grant_offers.retrieve("goff_1"), "GET", "/grant_offers/goff_1", None, None),
    (
        "grant_offers.update",
        lambda c: c.grant_offers.update("goff_1", active=False),
        "PATCH",
        "/grant_offers/goff_1",
        {"active": False},
        None,
    ),
    (
        "grant_offers.purchase",
        lambda c: c.grant_offers.purchase("goff_1", customer="cus_1"),
        "POST",
        "/grant_offers/goff_1/purchase",
        {"customer": "cus_1"},
        None,
    ),
    (
        "grants.create",
        lambda c: c.grants.create(customer="cus_1", meter="documents", units=5),
        "POST",
        "/grants",
        {"customer": "cus_1", "meter": "documents", "units": 5},
        None,
    ),
    ("grants.list", lambda c: c.grants.list(customer="cus_1"), "GET", "/grants", None, {"customer": "cus_1"}),
    ("grants.retrieve", lambda c: c.grants.retrieve("grt_1"), "GET", "/grants/grt_1", None, None),
    (
        "grants.withdraw",
        lambda c: c.grants.withdraw("grt_1", note="Reembolso"),
        "POST",
        "/grants/grt_1/withdraw",
        {"note": "Reembolso"},
        None,
    ),
    # (name, call, method, path, body or None, query or None)
    ("account.retrieve", lambda c: c.account.retrieve(), "GET", "/account", None, None),
    (
        "customers.create",
        lambda c: c.customers.create(email="a@b.co", name="Ana", metadata={"t": "1"}),
        "POST",
        "/customers",
        {"email": "a@b.co", "name": "Ana", "metadata": {"t": "1"}},
        None,
    ),
    ("customers.list", lambda c: c.customers.list(email="a@b.co"), "GET", "/customers", None, {"email": "a@b.co"}),
    ("customers.retrieve", lambda c: c.customers.retrieve("cus_1"), "GET", "/customers/cus_1", None, None),
    (
        "customers.update",
        lambda c: c.customers.update("cus_1", phone="300"),
        "PATCH",
        "/customers/cus_1",
        {"phone": "300"},
        None,
    ),
    ("customers.delete", lambda c: c.customers.delete("cus_1"), "DELETE", "/customers/cus_1", None, None),
    (
        "customers.list_payment_methods",
        lambda c: c.customers.list_payment_methods("cus_1", limit=5),
        "GET",
        "/customers/cus_1/payment_methods",
        None,
        {"limit": "5"},
    ),
    ("products.create", lambda c: c.products.create(name="Plan Pro"), "POST", "/products", {"name": "Plan Pro"}, None),
    ("products.list", lambda c: c.products.list(active=False), "GET", "/products", None, {"active": "false"}),
    ("products.retrieve", lambda c: c.products.retrieve("prod_1"), "GET", "/products/prod_1", None, None),
    (
        "products.update",
        lambda c: c.products.update("prod_1", active=False),
        "PATCH",
        "/products/prod_1",
        {"active": False},
        None,
    ),
    ("products.delete", lambda c: c.products.delete("prod_1"), "DELETE", "/products/prod_1", None, None),
    (
        "prices.create recurring",
        lambda c: c.prices.create(product={"name": "Pro"}, amount=89900, recurring={"interval": "month"}),
        "POST",
        "/prices",
        {"product": {"name": "Pro"}, "amount": 89900, "recurring": {"interval": "month"}},
        None,
    ),
    (
        "prices.create one_time",
        lambda c: c.prices.create(product="prod_1", amount=50000, type="one_time"),
        "POST",
        "/prices",
        {"product": "prod_1", "amount": 50000, "type": "one_time"},
        None,
    ),
    ("prices.list", lambda c: c.prices.list(type="one_time"), "GET", "/prices", None, {"type": "one_time"}),
    ("prices.retrieve", lambda c: c.prices.retrieve("price_1"), "GET", "/prices/price_1", None, None),
    (
        "prices.update",
        lambda c: c.prices.update("price_1", nickname="x"),
        "PATCH",
        "/prices/price_1",
        {"nickname": "x"},
        None,
    ),
    (
        "subscriptions.create items + anchor",
        lambda c: c.subscriptions.create(
            customer="cus_1",
            items=[{"price": "price_base", "quantity": 1}, {"price": "price_seat", "quantity": 3}],
            billing_cycle_anchor="2026-11-15T05:00:00Z",
            metadata={"legacy_id": "klopi-sub-1"},
        ),
        "POST",
        "/subscriptions",
        {
            "customer": "cus_1",
            "items": [{"price": "price_base", "quantity": 1}, {"price": "price_seat", "quantity": 3}],
            "billing_cycle_anchor": "2026-11-15T05:00:00Z",
            "metadata": {"legacy_id": "klopi-sub-1"},
        },
        None,
    ),
    (
        "subscriptions.create trial_end",
        lambda c: c.subscriptions.create(customer="cus_1", price="price_1", trial_end=1767225600),
        "POST",
        "/subscriptions",
        {"customer": "cus_1", "price": "price_1", "trial_end": 1767225600},
        None,
    ),
    (
        "subscriptions.list",
        lambda c: c.subscriptions.list(status="active", customer="cus_1"),
        "GET",
        "/subscriptions",
        None,
        {"status": "active", "customer": "cus_1"},
    ),
    ("subscriptions.retrieve", lambda c: c.subscriptions.retrieve("sub_1"), "GET", "/subscriptions/sub_1", None, None),
    (
        "subscriptions.update",
        lambda c: c.subscriptions.update("sub_1", price="price_2", proration_behavior="none"),
        "PATCH",
        "/subscriptions/sub_1",
        {"price": "price_2", "proration_behavior": "none"},
        None,
    ),
    (
        "subscriptions.cancel",
        lambda c: c.subscriptions.cancel("sub_1", at_period_end=True, reason="requested_by_customer"),
        "POST",
        "/subscriptions/sub_1/cancel",
        {"at_period_end": True, "reason": "requested_by_customer"},
        None,
    ),
    (
        "subscription_items.create",
        lambda c: c.subscription_items.create(subscription="sub_1", price="price_seat", quantity=2),
        "POST",
        "/subscription_items",
        {"subscription": "sub_1", "price": "price_seat", "quantity": 2},
        None,
    ),
    (
        "subscription_items.list",
        lambda c: c.subscription_items.list(subscription="sub_1"),
        "GET",
        "/subscription_items",
        None,
        {"subscription": "sub_1"},
    ),
    (
        "subscription_items.retrieve",
        lambda c: c.subscription_items.retrieve("si_1"),
        "GET",
        "/subscription_items/si_1",
        None,
        None,
    ),
    (
        "subscription_items.update",
        lambda c: c.subscription_items.update("si_1", quantity=4),
        "PATCH",
        "/subscription_items/si_1",
        {"quantity": 4},
        None,
    ),
    (
        "subscription_items.delete",
        lambda c: c.subscription_items.delete("si_1"),
        "DELETE",
        "/subscription_items/si_1",
        None,
        None,
    ),
    (
        "subscription_items.delete proration",
        lambda c: c.subscription_items.delete("si_1", proration_behavior="none"),
        "DELETE",
        "/subscription_items/si_1",
        {"proration_behavior": "none"},
        None,
    ),
    (
        "payment_methods.create",
        lambda c: c.payment_methods.create(customer="cus_1", wompi_payment_source_id="48213", make_default=True),
        "POST",
        "/payment_methods",
        {"customer": "cus_1", "wompi_payment_source_id": "48213", "make_default": True},
        None,
    ),
    (
        "payment_methods.import_wompi_source",
        lambda c: c.payment_methods.import_wompi_source(
            customer="cus_1", wompi_payment_source_id=48213, metadata={"legacy_id": "x"}
        ),
        "POST",
        "/payment_methods",
        {"customer": "cus_1", "wompi_payment_source_id": "48213", "metadata": {"legacy_id": "x"}},
        None,
    ),
    (
        "payment_methods.retrieve",
        lambda c: c.payment_methods.retrieve("pm_1"),
        "GET",
        "/payment_methods/pm_1",
        None,
        None,
    ),
    (
        "payment_methods.set_default",
        lambda c: c.payment_methods.set_default("pm_1"),
        "POST",
        "/payment_methods/pm_1/default",
        {},
        None,
    ),
    (
        "payment_methods.delete",
        lambda c: c.payment_methods.delete("pm_1"),
        "DELETE",
        "/payment_methods/pm_1",
        None,
        None,
    ),
    (
        "payment_methods.list",
        lambda c: c.payment_methods.list(customer="cus_1"),
        "GET",
        "/customers/cus_1/payment_methods",
        None,
        None,
    ),
    (
        "invoices.create lines",
        lambda c: c.invoices.create(customer="cus_1", lines=[{"price": "price_pack", "quantity": 2}], retry=False),
        "POST",
        "/invoices",
        {"customer": "cus_1", "lines": [{"price": "price_pack", "quantity": 2}], "retry": False},
        None,
    ),
    (
        "invoices.create amount",
        lambda c: c.invoices.create(customer="cus_1", amount=150000, description="Paquete 1.000 mensajes"),
        "POST",
        "/invoices",
        {"customer": "cus_1", "amount": 150000, "description": "Paquete 1.000 mensajes"},
        None,
    ),
    ("invoices.list", lambda c: c.invoices.list(status="open"), "GET", "/invoices", None, {"status": "open"}),
    ("invoices.retrieve", lambda c: c.invoices.retrieve("in_1"), "GET", "/invoices/in_1", None, None),
    ("invoices.list_lines", lambda c: c.invoices.list_lines("in_1"), "GET", "/invoices/in_1/lines", None, None),
    ("invoices.pay", lambda c: c.invoices.pay("in_1"), "POST", "/invoices/in_1/pay", {}, None),
    ("invoices.void", lambda c: c.invoices.void("in_1"), "POST", "/invoices/in_1/void", {}, None),
    ("payments.list", lambda c: c.payments.list(invoice="in_1"), "GET", "/payments", None, {"invoice": "in_1"}),
    ("payments.retrieve", lambda c: c.payments.retrieve("py_1"), "GET", "/payments/py_1", None, None),
    ("events.list", lambda c: c.events.list(type="invoice.*"), "GET", "/events", None, {"type": "invoice.*"}),
    ("events.retrieve", lambda c: c.events.retrieve("evt_1"), "GET", "/events/evt_1", None, None),
    (
        "events.resend",
        lambda c: c.events.resend("evt_1", webhook_endpoint="we_1"),
        "POST",
        "/events/evt_1/resend",
        {"webhook_endpoint": "we_1"},
        None,
    ),
    (
        "webhook_endpoints.create",
        lambda c: c.webhook_endpoints.create(url="https://app.co/webhooks/bucli/", enabled_events=["*"]),
        "POST",
        "/webhook_endpoints",
        {"url": "https://app.co/webhooks/bucli/", "enabled_events": ["*"]},
        None,
    ),
    ("webhook_endpoints.list", lambda c: c.webhook_endpoints.list(), "GET", "/webhook_endpoints", None, None),
    (
        "webhook_endpoints.retrieve",
        lambda c: c.webhook_endpoints.retrieve("we_1"),
        "GET",
        "/webhook_endpoints/we_1",
        None,
        None,
    ),
    (
        "webhook_endpoints.update",
        lambda c: c.webhook_endpoints.update("we_1", disabled=True),
        "PATCH",
        "/webhook_endpoints/we_1",
        {"disabled": True},
        None,
    ),
    (
        "webhook_endpoints.delete",
        lambda c: c.webhook_endpoints.delete("we_1"),
        "DELETE",
        "/webhook_endpoints/we_1",
        None,
        None,
    ),
    (
        "webhook_endpoints.roll_secret",
        lambda c: c.webhook_endpoints.roll_secret("we_1"),
        "POST",
        "/webhook_endpoints/we_1/roll_secret",
        {},
        None,
    ),
    (
        "checkout_sessions.create",
        lambda c: c.checkout_sessions.create(
            price="price_1", success_url="https://app.co/ok", customer="cus_1", client_reference_id="42"
        ),
        "POST",
        "/checkout_sessions",
        {"price": "price_1", "success_url": "https://app.co/ok", "customer": "cus_1", "client_reference_id": "42"},
        None,
    ),
    (
        "checkout_sessions.retrieve",
        lambda c: c.checkout_sessions.retrieve("cs_1"),
        "GET",
        "/checkout_sessions/cs_1",
        None,
        None,
    ),
    (
        "checkout_sessions.expire",
        lambda c: c.checkout_sessions.expire("cs_1"),
        "POST",
        "/checkout_sessions/cs_1/expire",
        {},
        None,
    ),
    (
        "checkout_sessions.create line_items",
        lambda c: c.checkout_sessions.create(
            line_items=[{"price": "price_1", "quantity": 1}, {"price": "price_seat", "quantity": 3}],
            success_url="https://app.co/ok",
            subscription_data={"metadata": {"account": "42"}},
        ),
        "POST",
        "/checkout_sessions",
        {
            "line_items": [{"price": "price_1", "quantity": 1}, {"price": "price_seat", "quantity": 3}],
            "success_url": "https://app.co/ok",
            "subscription_data": {"metadata": {"account": "42"}},
        },
        None,
    ),
    (
        "payment_links.create line_items",
        lambda c: c.payment_links.create(line_items=[{"price": "price_1"}, {"price": "price_seat", "quantity": 2}]),
        "POST",
        "/payment_links",
        {"line_items": [{"price": "price_1"}, {"price": "price_seat", "quantity": 2}]},
        None,
    ),
    (
        "payment_links.create",
        lambda c: c.payment_links.create(price="price_1"),
        "POST",
        "/payment_links",
        {"price": "price_1"},
        None,
    ),
    (
        "payment_links.list",
        lambda c: c.payment_links.list(active=True),
        "GET",
        "/payment_links",
        None,
        {"active": "true"},
    ),
    (
        "payment_links.retrieve",
        lambda c: c.payment_links.retrieve("plink_1"),
        "GET",
        "/payment_links/plink_1",
        None,
        None,
    ),
    (
        "payment_links.update",
        lambda c: c.payment_links.update("plink_1", active=False),
        "PATCH",
        "/payment_links/plink_1",
        {"active": False},
        None,
    ),
    (
        "portal_sessions.create",
        lambda c: c.portal_sessions.create(customer="cus_1", return_url="https://app.co/cuenta"),
        "POST",
        "/portal_sessions",
        {"customer": "cus_1", "return_url": "https://app.co/cuenta"},
        None,
    ),
    (
        "portal_sessions.retrieve",
        lambda c: c.portal_sessions.retrieve("bps_1"),
        "GET",
        "/portal_sessions/bps_1",
        None,
        None,
    ),
    # -- F3: prorations --
    (
        "subscriptions.preview",
        lambda c: c.subscriptions.preview("sub_1", price="price_2"),
        "POST",
        "/subscriptions/sub_1/preview",
        {"price": "price_2"},
        None,
    ),
    (
        "subscriptions.preview items",
        lambda c: c.subscriptions.preview("sub_1", items=[{"id": "si_1", "price": "price_2", "quantity": 2}]),
        "POST",
        "/subscriptions/sub_1/preview",
        {"items": [{"id": "si_1", "price": "price_2", "quantity": 2}]},
        None,
    ),
    (
        "subscriptions.update prorated",
        lambda c: c.subscriptions.update(
            "sub_1", price="price_2", proration_behavior="create_prorations", expected_amount=45000
        ),
        "PATCH",
        "/subscriptions/sub_1",
        {"price": "price_2", "proration_behavior": "create_prorations", "expected_amount": 45000},
        None,
    ),
    (
        "subscription_items.delete prorated",
        lambda c: c.subscription_items.delete("si_1", proration_behavior="create_prorations", expected_amount=0),
        "DELETE",
        "/subscription_items/si_1",
        {"proration_behavior": "create_prorations", "expected_amount": 0},
        None,
    ),
    # -- F4: staff actions, discounts, refunds --
    (
        "subscriptions.pause",
        lambda c: c.subscriptions.pause("sub_1", resumes_at="2027-01-01T05:00:00Z", reason="Vacaciones"),
        "POST",
        "/subscriptions/sub_1/pause",
        {"resumes_at": "2027-01-01T05:00:00Z", "reason": "Vacaciones"},
        None,
    ),
    (
        "subscriptions.resume",
        lambda c: c.subscriptions.resume("sub_1"),
        "POST",
        "/subscriptions/sub_1/resume",
        {},
        None,
    ),
    (
        "subscriptions.extend_period",
        lambda c: c.subscriptions.extend_period("sub_1", days=5, reason="Caída del servicio"),
        "POST",
        "/subscriptions/sub_1/extend_period",
        {"days": 5, "reason": "Caída del servicio"},
        None,
    ),
    (
        "subscriptions.extend_trial",
        lambda c: c.subscriptions.extend_trial("sub_1", trial_end=1767225600),
        "POST",
        "/subscriptions/sub_1/extend_trial",
        {"trial_end": 1767225600},
        None,
    ),
    (
        "subscriptions.end_trial",
        lambda c: c.subscriptions.end_trial("sub_1", reason="Ya eligió"),
        "POST",
        "/subscriptions/sub_1/end_trial",
        {"reason": "Ya eligió"},
        None,
    ),
    (
        "subscriptions.apply_discount",
        lambda c: c.subscriptions.apply_discount("sub_1", coupon="coupon_1"),
        "POST",
        "/subscriptions/sub_1/discount",
        {"coupon": "coupon_1"},
        None,
    ),
    (
        "subscriptions.delete_discount",
        lambda c: c.subscriptions.delete_discount("sub_1", reason="Fin del acuerdo"),
        "DELETE",
        "/subscriptions/sub_1/discount",
        {"reason": "Fin del acuerdo"},
        None,
    ),
    (
        "subscriptions.list_actions",
        lambda c: c.subscriptions.list_actions("sub_1", limit=5),
        "GET",
        "/subscriptions/sub_1/actions",
        None,
        {"limit": "5"},
    ),
    (
        "payment_methods.set_backup",
        lambda c: c.payment_methods.set_backup("pm_2"),
        "POST",
        "/payment_methods/pm_2/backup",
        {},
        None,
    ),
    (
        "invoices.mark_paid",
        lambda c: c.invoices.mark_paid("in_1", reference="TRF-881", reason="Transferencia"),
        "POST",
        "/invoices/in_1/mark_paid",
        {"reference": "TRF-881", "reason": "Transferencia"},
        None,
    ),
    (
        "payments.refund",
        lambda c: c.payments.refund("py_1", amount=2000, reason="Cobro doble"),
        "POST",
        "/payments/py_1/refund",
        {"amount": 2000, "reason": "Cobro doble"},
        None,
    ),
    (
        "payments.void",
        lambda c: c.payments.void("py_1", cancel_subscription=True),
        "POST",
        "/payments/py_1/void",
        {"cancel_subscription": True},
        None,
    ),
    ("refunds.list", lambda c: c.refunds.list(payment="py_1"), "GET", "/refunds", None, {"payment": "py_1"}),
    ("refunds.retrieve", lambda c: c.refunds.retrieve("re_1"), "GET", "/refunds/re_1", None, None),
    (
        "coupons.create",
        lambda c: c.coupons.create(name="Asambleas", amount_off=120000, duration="once", max_redemptions=1),
        "POST",
        "/coupons",
        {"name": "Asambleas", "amount_off": 120000, "duration": "once", "max_redemptions": 1},
        None,
    ),
    ("coupons.list", lambda c: c.coupons.list(limit=10), "GET", "/coupons", None, {"limit": "10"}),
    ("coupons.retrieve", lambda c: c.coupons.retrieve("coupon_1"), "GET", "/coupons/coupon_1", None, None),
    (
        "coupons.update",
        lambda c: c.coupons.update("coupon_1", active=False),
        "PATCH",
        "/coupons/coupon_1",
        {"active": False},
        None,
    ),
    ("coupons.delete", lambda c: c.coupons.delete("coupon_1"), "DELETE", "/coupons/coupon_1", None, None),
    # -- F5: fixed term --
    (
        "fixed_term_offers.create",
        lambda c: c.fixed_term_offers.create(
            customer="cus_1", items=[{"price": "price_1", "quantity": 1}], days=365, amount=950000
        ),
        "POST",
        "/fixed_term_offers",
        {"customer": "cus_1", "items": [{"price": "price_1", "quantity": 1}], "days": 365, "amount": 950000},
        None,
    ),
    (
        "fixed_term_offers.list",
        lambda c: c.fixed_term_offers.list(customer="cus_1"),
        "GET",
        "/fixed_term_offers",
        None,
        {"customer": "cus_1"},
    ),
    (
        "fixed_term_offers.retrieve",
        lambda c: c.fixed_term_offers.retrieve("fto_1"),
        "GET",
        "/fixed_term_offers/fto_1",
        None,
        None,
    ),
    (
        "fixed_term_offers.cancel",
        lambda c: c.fixed_term_offers.cancel("fto_1"),
        "POST",
        "/fixed_term_offers/fto_1/cancel",
        {},
        None,
    ),
]


@pytest.mark.parametrize(("name", "call", "method", "path", "body", "query"), CASES, ids=[case[0] for case in CASES])
@responses.activate
def test_resource_call(client, name, call, method, path, body, query):
    responses.add(method, re.compile(re.escape(API) + ".*"), json={"id": "x_1", "object": "thing"})
    result = call(client)
    assert isinstance(result, bucli.BucliObject)
    sent = responses.calls[0].request
    parts = urlsplit(sent.url)
    assert sent.method == method
    assert parts.path == "/v1" + path
    if body is None:
        assert not sent.body
    else:
        assert json.loads(sent.body) == body
        assert sent.headers["Content-Type"] == "application/json"
    assert dict(parse_qsl(parts.query)) == (query or {})
    if method == "POST":
        assert sent.headers["Idempotency-Key"]


@responses.activate
def test_checkout_replay_without_url_recovers_the_session(client):
    responses.post(
        f"{API}/checkout_sessions",
        status=409,
        json=error(
            "url_unavailable_on_replay",
            # The id is read from ``object_id``, never from the words.
            message="La respuesta original traía la URL de una sesión. Consulta la sesión con GET.",
            object_id="cs_Abc123",
            object_type="checkout.session",
        ),
    )
    responses.get(
        f"{API}/checkout_sessions/cs_Abc123",
        json={"id": "cs_Abc123", "object": "checkout.session", "url": "https://pay.bucli.co/c/tok"},
    )
    session = client.checkout_sessions.create(price="price_1", success_url="https://app.co/ok", idempotency_key="k")
    assert session.url == "https://pay.bucli.co/c/tok"


@responses.activate
def test_checkout_replay_without_object_id_is_raised(client):
    """An answer that names no object (only words) is not guessed from."""
    responses.post(
        f"{API}/checkout_sessions",
        status=409,
        json=error("url_unavailable_on_replay", message="La respuesta original traía la URL de cs_Abc123."),
    )
    with pytest.raises(bucli.InvalidRequestError) as caught:
        client.checkout_sessions.create(price="price_1", success_url="https://app.co/ok", idempotency_key="k")
    assert caught.value.object_id is None
    assert len(responses.calls) == 1


@responses.activate
def test_replay_errors_carry_the_object(client):
    responses.post(
        f"{API}/webhook_endpoints",
        status=409,
        json=error("webhook_secret_unavailable_on_replay", object_id="we_1", object_type="webhook_endpoint"),
    )
    with pytest.raises(bucli.InvalidRequestError) as caught:
        client.webhook_endpoints.create(url="https://a.co/h", enabled_events=["*"], idempotency_key="k")
    assert (caught.value.object_id, caught.value.object_type) == ("we_1", "webhook_endpoint")


@responses.activate
def test_portal_replay_without_url_creates_another(client):
    responses.post(
        f"{API}/portal_sessions",
        status=409,
        json=error(
            "url_unavailable_on_replay",
            message="La respuesta original traía la URL de bps_1.",
            object_id="bps_1",
            object_type="portal.session",
        ),
    )
    responses.post(f"{API}/portal_sessions", json={"id": "bps_2", "object": "portal.session", "url": "https://p/s/2"})
    session = client.portal_sessions.create(customer="cus_1", idempotency_key="k")
    assert session.url == "https://p/s/2"
    keys = [call.request.headers["Idempotency-Key"] for call in responses.calls]
    assert keys[0] == "k" and keys[1] != "k"


@responses.activate
def test_webhook_secret_replay_is_raised(client):
    responses.post(
        f"{API}/webhook_endpoints",
        status=409,
        json=error("webhook_secret_unavailable_on_replay", message="Regenéralo con roll_secret."),
    )
    with pytest.raises(bucli.InvalidRequestError) as caught:
        client.webhook_endpoints.create(url="https://a.co/h", enabled_events=["*"], idempotency_key="k")
    assert caught.value.code == "webhook_secret_unavailable_on_replay"


@responses.activate
def test_a_changed_quote_is_on_the_error(client):
    quote = {"object": "subscription_quote", "amount": 52000, "direction": "up"}
    body = error("quote_changed", message="El valor del cambio ya no es el de la vista previa.")
    body["error"]["quote"] = quote
    responses.patch(f"{API}/subscriptions/sub_1", status=409, json=body)
    with pytest.raises(bucli.InvalidRequestError) as caught:
        client.subscriptions.update("sub_1", price="price_2", proration_behavior="create_prorations", expected_amount=1)
    assert caught.value.code == "quote_changed"
    assert caught.value.quote == quote
    assert caught.value.quote.amount == 52000, "the same shape preview answers"


def test_mark_paid_needs_the_transfer_reference(client):
    with pytest.raises(TypeError):
        client.invoices.mark_paid("in_1")


def test_an_error_without_a_quote_says_none():
    assert bucli.BucliError("x", json_body={"error": {"code": "x"}}).quote is None
    assert bucli.BucliError("x", json_body="no es json").quote is None
