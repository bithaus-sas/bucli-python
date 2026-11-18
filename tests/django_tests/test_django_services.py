"""Services (checkout, portal, plan, cancel, sync), the access rule, the command and the app's checks."""

import datetime
import io
import json
from urllib.parse import parse_qsl, urlsplit

import pytest
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from bucli.contrib.django import access, services
from bucli.contrib.django.exceptions import AlreadySubscribed, NotSubscribed, UnknownPrice
from bucli.contrib.django.models import BillingAccount

from factories import API, checkout_session, error, item, page, subscription
from helpers import BucliTestCase
from tenants.models import Tenant


def body_of(call):
    return json.loads(call.request.body)


def query_of(call):
    return dict(parse_qsl(urlsplit(call.request.url).query))


class StartCheckoutTests(BucliTestCase):
    def open_session(self):
        session = checkout_session(status="open", subscription_id=None, reference=str(self.tenant.pk))
        self.api.post(f"{API}/checkout_sessions", json=session)
        return session

    def test_creates_the_customer_and_the_session_without_activating(self):
        self.api.get(f"{API}/customers", json=page([], "/v1/customers"))
        self.api.post(f"{API}/customers", json={"id": "cus_new", "object": "customer"})
        self.open_session()
        session = services.start_checkout(
            self.tenant, "price_pro", "https://app.co/ok", "https://app.co/planes", "ana@tienda.co", name="Ana"
        )
        assert session.url.startswith("https://pay.bucli.co/c/")
        customer = body_of(self.api_calls("POST", "/customers")[0])
        assert customer == {
            "email": "ana@tienda.co",
            "name": "Ana",
            "metadata": {"bucli_account": f"tenants.tenant:{self.tenant.pk}"},
        }
        sent = body_of(self.api_calls("POST", "/checkout_sessions")[0])
        assert sent["customer"] == "cus_new" and sent["price"] == "price_pro"
        assert sent["client_reference_id"] == str(self.tenant.pk)
        assert sent["success_url"] == "https://app.co/ok" and sent["cancel_url"] == "https://app.co/planes"
        billing = BillingAccount.objects.get(account=self.tenant)
        assert billing.bucli_customer_id == "cus_new"
        assert billing.status == "" and not access.has_access(self.tenant), "success_url never activates"

    def test_reuses_the_customer_with_that_email(self):
        self.api.get(f"{API}/customers", json=page([{"id": "cus_old", "object": "customer"}], "/v1/customers"))
        self.open_session()
        services.start_checkout(self.tenant, "price_basic", "https://app.co/ok", None, "ana@tienda.co")
        assert query_of(self.api_calls("GET", "/customers")[0]) == {"email": "ana@tienda.co", "limit": "1"}
        assert not self.api_calls("POST", "/customers")
        assert BillingAccount.objects.get(account=self.tenant).bucli_customer_id == "cus_old"

    def test_reuses_the_linked_customer(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_linked", status="canceled")
        self.open_session()
        services.start_checkout(self.tenant, "price_pro", "https://app.co/ok", None, "ana@tienda.co")
        assert not self.api_calls("GET", "/customers")
        assert body_of(self.api_calls("POST", "/checkout_sessions")[0])["customer"] == "cus_linked"

    def test_customer_created_meanwhile(self):
        self.api.get(f"{API}/customers", json=page([], "/v1/customers"))
        self.api.post(f"{API}/customers", status=409, json=error("resource_already_exists"))
        self.api.get(f"{API}/customers", json=page([{"id": "cus_race", "object": "customer"}], "/v1/customers"))
        self.open_session()
        services.start_checkout(self.tenant, "price_pro", "https://app.co/ok", None, "ana@tienda.co")
        assert BillingAccount.objects.get(account=self.tenant).bucli_customer_id == "cus_race"

    def test_finds_and_creates_the_customer_by_its_external_id_with_its_fiscal_data(self):
        self.api.get(f"{API}/customers", json=page([], "/v1/customers"))
        self.api.post(f"{API}/customers", json={"id": "cus_new", "object": "customer"})
        self.open_session()
        fiscal = {"party_type": "company", "document_type": "nit", "document_number": "900123456"}
        services.start_checkout(
            self.tenant,
            "price_pro",
            "https://app.co/ok",
            None,
            "ana@tienda.co",
            external_id="org-1",
            phone="+573001234567",
            fiscal=fiscal,
        )
        assert query_of(self.api_calls("GET", "/customers")[0]) == {"external_id": "org-1", "limit": "1"}
        sent = body_of(self.api_calls("POST", "/customers")[0])
        assert sent["external_id"] == "org-1" and sent["phone"] == "+573001234567"
        assert sent["fiscal"] == fiscal

    def test_refuses_a_second_running_subscription(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1", status="active")
        with pytest.raises(AlreadySubscribed):
            services.start_checkout(self.tenant, "price_pro", "https://app.co/ok", None, "a@b.co")
        assert not self.api.calls

    def test_refuses_an_unmapped_price(self):
        with pytest.raises(UnknownPrice):
            services.start_checkout(self.tenant, "price_nope", "https://app.co/ok", None, "a@b.co")

    def test_the_subscription_carries_the_account_label(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1", status="")
        self.open_session()
        services.start_checkout(
            self.tenant, "price_pro", "https://app.co/ok", None, "a@b.co", subscription_metadata={"origen": "web"}
        )
        sent = body_of(self.api_calls("POST", "/checkout_sessions")[0])
        assert sent["subscription_data"] == {
            "metadata": {"origen": "web", "bucli_account": f"tenants.tenant:{self.tenant.pk}"}
        }
        assert "line_items" not in sent and "quantity" not in sent

    def test_several_items_go_as_line_items(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1", status="")
        self.open_session()
        services.start_checkout(
            self.tenant,
            success_url="https://app.co/ok",
            email="a@b.co",
            items=["price_pro", ("price_seat", 3), {"price": "price_addon", "quantity": 1}],
            trial_days=14,
        )
        sent = body_of(self.api_calls("POST", "/checkout_sessions")[0])
        assert sent["line_items"] == [
            {"price": "price_pro"},
            {"price": "price_seat", "quantity": 3},
            {"price": "price_addon", "quantity": 1},
        ]
        assert "price" not in sent and sent["trial_days"] == 14 and sent["customer"] == "cus_1"

    def test_items_need_a_plan_price_and_go_alone(self):
        with pytest.raises(UnknownPrice):
            services.start_checkout(self.tenant, success_url="https://app.co/ok", email="a@b.co", items=["price_seat"])
        with pytest.raises(ValueError):
            services.start_checkout(
                self.tenant, "price_pro", "https://app.co/ok", None, "a@b.co", items=[("price_seat", 2)]
            )
        with pytest.raises(ValueError):
            services.start_checkout(self.tenant, success_url="https://app.co/ok", email="a@b.co", items=[])
        assert not self.api.calls


class AccountActionTests(BucliTestCase):
    def setUp(self):
        super().setUp()
        self.billing = BillingAccount.objects.create(
            account=self.tenant, bucli_customer_id="cus_1", bucli_subscription_id="sub_1", status="active"
        )

    def test_portal_url(self):
        self.api.post(
            f"{API}/portal_sessions", json={"id": "bps_1", "object": "portal.session", "url": "https://p/s/x"}
        )
        assert services.portal_url(self.tenant, "https://app.co/cuenta") == "https://p/s/x"
        assert body_of(self.api.calls[0]) == {"customer": "cus_1", "return_url": "https://app.co/cuenta"}

    def test_portal_needs_a_customer(self):
        with pytest.raises(NotSubscribed):
            services.portal_url(Tenant.objects.create(name="Nueva"))

    def test_change_plan_one_item(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        self.api.patch(f"{API}/subscriptions/sub_1", json=subscription(pending_price="price_basic"))
        billing = services.change_plan(self.tenant, "price_basic")
        assert body_of(self.api_calls("PATCH")[0]) == {"price": "price_basic", "proration_behavior": "none"}
        assert billing.pending_price_id == "price_basic" and billing.plan == "pro", "applies next cycle"

    def test_change_plan_multi_item_changes_the_base_item(self):
        items = [item("si_base", "price_pro"), item("si_seat", "price_seat", 3)]
        new_items = [item("si_base", "price_basic"), item("si_seat", "price_seat", 3)]
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(items=items))
        self.api.patch(f"{API}/subscription_items/si_base", json=new_items[0])
        services.change_plan(self.tenant, "price_basic")
        assert body_of(self.api_calls("PATCH", "/subscription_items/si_base")[0]) == {
            "price": "price_basic",
            "proration_behavior": "none",
        }
        assert not self.api_calls("PATCH", "/subscriptions/sub_1")

    def test_change_plan_prorated_sends_the_previewed_amount(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        self.api.patch(f"{API}/subscriptions/sub_1", json=subscription())
        services.change_plan(self.tenant, "price_basic", proration_behavior="create_prorations", expected_amount=45000)
        assert body_of(self.api_calls("PATCH")[0]) == {
            "price": "price_basic",
            "proration_behavior": "create_prorations",
            "expected_amount": 45000,
        }

    def test_change_plan_stores_the_subscription_even_if_answered_a_pending_change(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        self.api.patch(f"{API}/subscriptions/sub_1", status=202, json={"id": "sc_1", "object": "subscription_change"})
        billing = services.change_plan(
            self.tenant, "price_basic", proration_behavior="create_prorations", expected_amount=45000
        )
        assert billing.bucli_subscription_id == "sub_1"
        assert len(self.api_calls("GET", "/subscriptions/sub_1")) == 2

    def test_change_plan_prorated_without_amount_is_refused_before_calling(self):
        with pytest.raises(ValueError):
            services.change_plan(self.tenant, "price_basic", proration_behavior="create_prorations")
        with pytest.raises(ValueError):
            services.change_plan(self.tenant, "price_basic", proration_behavior="always_invoice")
        assert not self.api.calls

    def test_change_plan_prorated_multi_item(self):
        items = [item("si_base", "price_pro"), item("si_seat", "price_seat", 3)]
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(items=items))
        self.api.patch(f"{API}/subscription_items/si_base", json=items[0])
        services.change_plan(self.tenant, "price_basic", proration_behavior="create_prorations", expected_amount=0)
        assert body_of(self.api_calls("PATCH", "/subscription_items/si_base")[0]) == {
            "price": "price_basic",
            "proration_behavior": "create_prorations",
            "expected_amount": 0,
        }

    def test_preview_plan_change_one_item(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        self.api.post(f"{API}/subscriptions/sub_1/preview", json={"object": "subscription_quote", "amount": 45000})
        quote = services.preview_plan_change(self.tenant, "price_basic")
        assert quote["amount"] == 45000
        assert body_of(self.api_calls("POST")[0]) == {"price": "price_basic"}

    def test_preview_plan_change_multi_item_swaps_only_the_plan(self):
        items = [item("si_base", "price_pro"), item("si_seat", "price_seat", 3)]
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(items=items))
        self.api.post(f"{API}/subscriptions/sub_1/preview", json={"object": "subscription_quote", "amount": 0})
        services.preview_plan_change(self.tenant, "price_basic")
        assert body_of(self.api_calls("POST")[0]) == {
            "items": [
                {"id": "si_base", "price": "price_basic", "quantity": 1},
                {"id": "si_seat", "price": "price_seat", "quantity": 3},
            ]
        }

    def test_change_plan_needs_a_running_subscription(self):
        self.billing.status = "canceled"
        self.billing.save()
        with pytest.raises(NotSubscribed):
            services.change_plan(self.tenant, "price_basic")

    def test_cancel_at_period_end(self):
        self.api.post(f"{API}/subscriptions/sub_1/cancel", json=subscription(cancel_at_period_end=True))
        billing = services.cancel(self.tenant, reason="requested_by_customer")
        assert body_of(self.api.calls[0]) == {"at_period_end": True, "reason": "requested_by_customer"}
        assert billing.cancel_at_period_end is True and access.has_access(self.tenant)

    def test_cancel_now(self):
        self.api.post(f"{API}/subscriptions/sub_1/cancel", json=subscription(status="canceled"))
        services.cancel(self.tenant, at_period_end=False)
        assert body_of(self.api.calls[0]) == {"at_period_end": False}
        assert access.access_level(self.tenant) == access.FREE

    def test_reactivate(self):
        self.api.patch(f"{API}/subscriptions/sub_1", json=subscription())
        services.reactivate(self.tenant)
        assert body_of(self.api.calls[0]) == {"cancel_at_period_end": False}

    def test_sync_picks_the_running_subscription(self):
        self.api.get(
            f"{API}/subscriptions",
            json=page(
                [subscription("sub_2", status="incomplete"), subscription("sub_3", status="trialing")],
                "/v1/subscriptions",
            ),
        )
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription("sub_1", status="canceled"))
        billing = services.sync(self.tenant)
        assert query_of(self.api_calls("GET", "/subscriptions")[0]) == {"customer": "cus_1", "limit": "20"}
        assert (billing.bucli_subscription_id, billing.status) == ("sub_3", "trialing")
        assert billing.last_synced_at is not None

    def test_sync_does_not_touch_the_event_sequence(self):
        self.billing.last_event_sequence = 50
        self.billing.save()
        self.api.get(f"{API}/subscriptions", json=page([subscription(status="past_due")], "/v1/subscriptions"))
        billing = services.sync(self.tenant)
        assert billing.status == "past_due" and billing.last_event_sequence == 50

    def test_sync_never_overwrites_a_newer_local_copy(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="canceled", updated="2026-11-02T10:00:00Z"))
        self.api.get(f"{API}/subscriptions", json=page([], "/v1/subscriptions"))
        services.sync(self.tenant)
        self.billing.refresh_from_db()
        assert self.billing.status == "canceled"
        assert self.billing.bucli_updated_at == datetime.datetime(2026, 11, 2, 10, tzinfo=datetime.UTC)
        # An older copy (a cache, a replica behind): kept out.
        older = subscription(status="active", updated="2026-11-02T09:59:59Z")
        assert services.store_subscription(self.billing, older) == services.STALE
        self.billing.refresh_from_db()
        assert self.billing.status == "canceled"
        newer = subscription(status="active", updated="2026-11-02T10:00:01Z")
        assert services.store_subscription(self.billing, newer) == services.APPLIED

    def test_sync_without_customer_does_nothing(self):
        assert services.sync(Tenant.objects.create(name="Nueva")).status == ""

    def test_command_all_and_by_pk(self):
        self.api.get(f"{API}/subscriptions", json=page([subscription(status="past_due")], "/v1/subscriptions"))
        out = io.StringIO()
        call_command("bucli_sync", "--all", stdout=out)
        assert "Sincronizadas: 1 · errores: 0" in out.getvalue()
        assert BillingAccount.objects.get(pk=self.billing.pk).status == "past_due"
        out = io.StringIO()
        call_command("bucli_sync", str(self.tenant.pk), stdout=out)
        assert "past_due" in out.getvalue()

    def test_command_reports_failures(self):
        self.api.get(f"{API}/subscriptions", status=401, json=error("api_key_invalid", type_="authentication_error"))
        with pytest.raises(CommandError):
            call_command("bucli_sync", "--all", stdout=io.StringIO(), stderr=io.StringIO())

    def test_command_needs_targets(self):
        with pytest.raises(CommandError):
            call_command("bucli_sync")


NOW = datetime.datetime(2026, 11, 20, tzinfo=datetime.UTC)


class AccessRuleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tienda")
        self.billing = BillingAccount.objects.create(
            account=self.tenant, plan="pro", current_period_end=NOW - datetime.timedelta(days=2)
        )

    def level(self, status):
        self.billing.status = status
        self.billing.save()
        self.tenant.refresh_from_db()
        return access.access_level(self.tenant, now=NOW)

    def test_the_table(self):
        assert self.level("trialing") == access.FULL
        assert self.level("active") == access.FULL
        assert self.level("past_due") == access.WARNING
        assert self.level("paused") == access.FREE
        assert self.level("canceled") == access.FREE
        assert self.level("incomplete_expired") == access.FREE
        assert self.level("incomplete") == access.NONE

    @override_settings(BUCLI_LAPSED_ACCESS="read_only")
    def test_lapsed_read_only(self):
        assert self.level("canceled") == access.READ_ONLY
        assert access.is_read_only(self.billing, now=NOW)
        assert access.current_plan(self.billing, now=NOW) is None
        assert self.level("incomplete") == access.NONE
        assert access.current_plan(self.billing, now=NOW) is None

    @override_settings(BUCLI_GRACE=1)
    def test_past_due_beyond_the_grace(self):
        assert self.level("past_due") == access.FREE

    @override_settings(BUCLI_GRACE=datetime.timedelta(days=5))
    def test_past_due_within_the_grace(self):
        assert self.level("past_due") == access.WARNING
        assert access.needs_payment_update(self.billing, now=NOW)

    def test_has_access_and_current_plan(self):
        self.level("past_due")
        assert access.has_access(self.tenant, now=NOW) and access.current_plan(self.tenant, now=NOW) == "pro"
        self.level("canceled")
        assert not access.has_access(self.tenant, now=NOW) and access.current_plan(self.tenant, now=NOW) == "free"
        self.level("incomplete")
        assert not access.has_access(self.tenant, now=NOW)
        assert access.current_plan(self.tenant, now=NOW) == "free", "incomplete keeps the fallback, not the new plan"

    def test_no_billing_account(self):
        other = Tenant.objects.create(name="Sin cobro")
        assert access.access_level(other) == access.FREE and not access.has_access(other)
        assert access.billing_account_of(other) is None


class AppChecksTests(TestCase):
    def test_migration_matches_the_models(self):
        out = io.StringIO()
        call_command("makemigrations", "bucli_billing", "--check", "--dry-run", stdout=out)
        assert "No changes detected" in out.getvalue()

    def test_system_checks_pass(self):
        call_command("check", stdout=io.StringIO())


class MigrationHelpersTests(BucliTestCase):
    def test_discover_links_by_metadata(self):
        other = Tenant.objects.create(name="Otra")
        labelled = dict(
            subscription("sub_a", customer="cus_a"), metadata={"bucli_account": f"tenants.tenant:{self.tenant.pk}"}
        )
        unknown = dict(subscription("sub_b", customer="cus_b"), metadata={"bucli_account": "tenants.tenant:999999"})
        bare = subscription("sub_c", customer="cus_c")
        old = dict(subscription("sub_d", status="canceled"), metadata={"bucli_account": f"tenants.tenant:{other.pk}"})
        running = dict(subscription("sub_e"), metadata={"bucli_account": f"tenants.tenant:{other.pk}"})
        self.api.get(f"{API}/subscriptions", json=page([labelled, unknown, bare, running, old], "/v1/subscriptions"))
        out = io.StringIO()
        call_command("bucli_sync", "--discover", stdout=out)
        assert "vistas: 5 · vinculadas: 2 · saltadas: 3" in out.getvalue()
        assert BillingAccount.objects.get(account=self.tenant).bucli_subscription_id == "sub_a"
        assert BillingAccount.objects.get(account=other).bucli_subscription_id == "sub_e", "canceled never replaces"

    def test_link_by_ids(self):
        self.api.get(f"{API}/subscriptions", json=page([subscription("sub_9", customer="cus_9")], "/v1/subscriptions"))
        billing = services.link(self.tenant, customer_id="cus_9", subscription_id="sub_9")
        assert (billing.bucli_customer_id, billing.bucli_subscription_id, billing.status) == (
            "cus_9",
            "sub_9",
            "active",
        )


class SyncCustomerTests(BucliTestCase):
    def test_sends_the_account_s_data_to_its_customer(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1")
        self.api.patch(f"{API}/customers/cus_1", json={"id": "cus_1", "object": "customer"})
        fiscal = {"party_type": "company", "document_type": "nit", "document_number": "900123456"}

        services.sync_customer(self.tenant, name="Tienda Ana", phone=None, fiscal=fiscal)

        assert body_of(self.api_calls("PATCH", "/customers/cus_1")[0]) == {"name": "Tienda Ana", "fiscal": fiscal}

    def test_without_a_customer_it_does_nothing(self):
        assert services.sync_customer(self.tenant, name="Tienda Ana") is None
        assert not self.api_calls()

    def test_only_none_sends_nothing(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1")

        assert services.sync_customer(self.tenant, phone=None, fiscal=None) is None
        assert not self.api_calls()

    def test_a_linked_account_s_checkout_sends_its_fiscal_data(self):
        BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1", status="canceled")
        self.api.patch(f"{API}/customers/cus_1", json={"id": "cus_1", "object": "customer"})
        self.api.post(f"{API}/checkout_sessions", json=checkout_session(status="open", subscription_id=None))
        fiscal = {"party_type": "company", "document_type": "nit", "document_number": "900123456"}

        services.start_checkout(self.tenant, "price_pro", "https://app.co/ok", None, "ana@tienda.co", fiscal=fiscal)

        assert body_of(self.api_calls("PATCH", "/customers/cus_1")[0]) == {"fiscal": fiscal}

    def test_refuses_what_it_does_not_send(self):
        with pytest.raises(ValueError):
            services.sync_customer(self.tenant, legal_id="123")
