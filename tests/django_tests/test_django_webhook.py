"""The webhook view: signature, deduplication, fresh GET, order, signals."""

import json
import time

from django.test import Client
from django.urls import resolve

import bucli
from bucli.contrib.django import webhooks
from bucli.contrib.django.models import BillingAccount, ProcessedEvent
from bucli.contrib.django.signals import event_processed, subscription_changed

from factories import API, checkout_session, error, event, invoice, item, subscription
from helpers import SECRET, WEBHOOK_URL, BucliTestCase
from tenants.models import Tenant


class SignalRecorder:
    def __init__(self, signal):
        self.calls = []
        self.signal = signal

    def __call__(self, sender, **kwargs):
        self.calls.append(kwargs)

    def __enter__(self):
        self.signal.connect(self, weak=False)
        return self

    def __exit__(self, *exc):
        self.signal.disconnect(self)


class WebhookViewTests(BucliTestCase):
    def setUp(self):
        super().setUp()
        self.billing = BillingAccount.objects.create(account=self.tenant, bucli_customer_id="cus_1")

    # -- signature ---------------------------------------------------------------

    def test_bad_signature_is_400_and_records_nothing(self):
        response = self.post_event(event("subscription.updated", subscription()), secret="whsec_other")
        assert response.status_code == 400
        assert response.json() == {"error": "invalid_signature"}
        assert not ProcessedEvent.objects.exists() and not self.api.calls

    def test_missing_signature_is_400(self):
        response = self.post_event(event("subscription.updated", subscription()), header="")
        assert response.status_code == 400

    def test_old_signature_is_400(self):
        response = self.post_event(event("subscription.updated", subscription()), timestamp=int(time.time()) - 600)
        assert response.status_code == 400

    def test_tampered_body_is_400(self):
        body = json.dumps(event("subscription.updated", subscription())).encode()
        header = bucli.Webhook.generate_test_header(body, SECRET)
        response = self.client.post(
            WEBHOOK_URL,
            data=body.replace(b"active", b"paused"),
            content_type="application/json",
            HTTP_BUCLI_SIGNATURE=header,
        )
        assert response.status_code == 400

    def test_rotation_two_v1(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        body = json.dumps(event("subscription.updated", subscription())).encode()
        header = bucli.Webhook.generate_test_header(body, ["whsec_previous", SECRET])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                WEBHOOK_URL, data=body, content_type="application/json", HTTP_BUCLI_SIGNATURE=header
            )
        assert response.status_code == 200

    def test_csrf_exempt_and_post_only(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        strict = Client(enforce_csrf_checks=True)
        body = json.dumps(event("subscription.updated", subscription())).encode()
        header = bucli.Webhook.generate_test_header(body, SECRET)
        response = strict.post(WEBHOOK_URL, data=body, content_type="application/json", HTTP_BUCLI_SIGNATURE=header)
        assert response.status_code == 200
        assert self.client.get(WEBHOOK_URL).status_code == 405

    def test_view_opts_out_of_atomic_requests(self):
        assert "default" in getattr(resolve(WEBHOOK_URL).func, "_non_atomic_requests", set())

    # -- the fresh object --------------------------------------------------------

    def test_stores_the_fresh_object_not_the_payload(self):
        stale_payload = subscription(status="incomplete")
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="active"))
        with SignalRecorder(subscription_changed) as changed:
            response = self.post_event(event("subscription.created", stale_payload, sequence=7))
        assert response.status_code == 200 and response.json()["outcome"] == "applied"
        self.billing.refresh_from_db()
        assert (self.billing.status, self.billing.plan, self.billing.price_id) == ("active", "pro", "price_pro")
        assert self.billing.bucli_subscription_id == "sub_1"
        assert self.billing.current_period_end.isoformat() == "2026-12-01T05:00:00+00:00"
        assert self.billing.last_event_sequence == 7
        assert self.billing.items == [{"id": "si_1", "price": "price_pro", "quantity": 1, "plan": "pro"}]
        assert len(changed.calls) == 1
        call = changed.calls[0]
        assert call["account"] == self.tenant and call["billing_account"].pk == self.billing.pk
        assert (call["previous_status"], call["status"], call["previous_plan"], call["plan"]) == (
            "",
            "active",
            "",
            "pro",
        )
        assert ProcessedEvent.objects.get(event_id="evt_1").outcome == "applied"

    def test_duplicate_event_does_nothing(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        first = self.post_event(event("subscription.updated", subscription()))
        with SignalRecorder(subscription_changed) as changed:
            second = self.post_event(event("subscription.updated", subscription()))
        assert first.json()["outcome"] == "applied"
        assert second.status_code == 200 and second.json()["outcome"] == "duplicate"
        assert len(self.api_calls("GET")) == 1
        assert ProcessedEvent.objects.count() == 1 and changed.calls == []

    def test_reversed_order_keeps_the_newer_copy(self):
        """Event 2 (canceled) arrives before event 1 (created): 1 never overwrites 2."""
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="canceled"))
        newer = self.post_event(event("subscription.canceled", subscription(status="canceled"), "evt_2", sequence=20))
        self.api.replace("GET", f"{API}/subscriptions/sub_1", json=subscription(status="active"))
        older = self.post_event(event("subscription.created", subscription(status="active"), "evt_1", sequence=10))
        assert newer.json()["outcome"] == "applied"
        assert older.status_code == 200 and older.json()["outcome"] == "stale"
        self.billing.refresh_from_db()
        assert self.billing.status == "canceled" and self.billing.last_event_sequence == 20
        assert set(ProcessedEvent.objects.values_list("event_id", flat=True)) == {"evt_1", "evt_2"}

    def test_updated_orders_the_copies_before_sequence(self):
        """A later event whose GET answered an OLDER copy (its request was slower) never
        overwrites the newer one; an older event whose GET is newer still applies."""
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="past_due", updated="2026-11-03T12:00:00Z"))
        first = self.post_event(event("subscription.past_due", subscription(), "evt_1", sequence=10))
        self.api.replace(
            "GET", f"{API}/subscriptions/sub_1", json=subscription(status="active", updated="2026-11-03T11:00:00Z")
        )
        slow = self.post_event(event("subscription.updated", subscription(), "evt_2", sequence=11))
        assert first.json()["outcome"] == "applied" and slow.json()["outcome"] == "stale"
        self.billing.refresh_from_db()
        assert self.billing.status == "past_due"

        self.api.replace(
            "GET", f"{API}/subscriptions/sub_1", json=subscription(status="canceled", updated="2026-11-03T13:00:00Z")
        )
        late = self.post_event(event("subscription.created", subscription(), "evt_0", sequence=5))
        assert late.json()["outcome"] == "applied"
        self.billing.refresh_from_db()
        assert self.billing.status == "canceled" and self.billing.last_event_sequence == 10

    def test_in_order_events_apply(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="active"))
        self.post_event(event("subscription.created", subscription(), "evt_1", sequence=10))
        self.api.replace("GET", f"{API}/subscriptions/sub_1", json=subscription(status="past_due"))
        self.post_event(event("subscription.past_due", subscription(), "evt_2", sequence=11))
        self.billing.refresh_from_db()
        assert self.billing.status == "past_due" and self.billing.last_event_sequence == 11

    def test_unchanged_copy_sends_no_signal(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        self.post_event(event("subscription.created", subscription(), "evt_1", sequence=1))
        with SignalRecorder(subscription_changed) as changed:
            response = self.post_event(event("subscription.trial_will_end", subscription(), "evt_2", sequence=2))
        assert response.json()["outcome"] == "unchanged" and changed.calls == []

    def test_an_old_canceled_subscription_never_replaces_the_running_one(self):
        self.billing.bucli_subscription_id = "sub_new"
        self.billing.status = "active"
        self.billing.save()
        self.api.get(f"{API}/subscriptions/sub_old", json=subscription("sub_old", status="canceled"))
        response = self.post_event(event("subscription.canceled", subscription("sub_old", status="canceled")))
        assert response.json()["outcome"] == "not_tracked"
        self.billing.refresh_from_db()
        assert (self.billing.bucli_subscription_id, self.billing.status) == ("sub_new", "active")

    # -- each event type ---------------------------------------------------------

    def test_checkout_completed_links_the_account_by_reference(self):
        other = Tenant.objects.create(name="Sin fila de cobro")
        self.api.get(
            f"{API}/checkout_sessions/cs_1",
            json=checkout_session(customer="cus_9", reference=str(other.pk), subscription_id="sub_9"),
        )
        self.api.get(f"{API}/subscriptions/sub_9", json=subscription("sub_9", customer="cus_9"))
        response = self.post_event(event("checkout.session.completed", checkout_session(reference=str(other.pk))))
        assert response.json()["outcome"] == "applied"
        billing = BillingAccount.objects.get(account=other)
        assert (billing.bucli_customer_id, billing.bucli_subscription_id, billing.status) == (
            "cus_9",
            "sub_9",
            "active",
        )

    def test_checkout_completed_with_a_shared_customer_goes_by_reference(self):
        """Bucli keeps one customer per e-mail: two accounts of one person share it."""
        second = Tenant.objects.create(name="Otra empresa de Ana")
        BillingAccount.objects.create(account=second, bucli_customer_id="cus_1")
        self.api.get(f"{API}/checkout_sessions/cs_1", json=checkout_session(reference=str(second.pk)))
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        self.post_event(event("checkout.session.completed", checkout_session(reference=str(second.pk))))
        assert BillingAccount.objects.get(account=second).bucli_subscription_id == "sub_1"
        self.billing.refresh_from_db()
        assert self.billing.bucli_subscription_id == ""

    def test_invoice_payment_failed_fetches_invoice_and_subscription(self):
        self.api.get(f"{API}/invoices/in_1", json=invoice(status="open"))
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="past_due"))
        with SignalRecorder(event_processed) as processed:
            response = self.post_event(event("invoice.payment_failed", invoice(status="open")))
        assert response.json()["outcome"] == "applied"
        self.billing.refresh_from_db()
        assert self.billing.status == "past_due"
        assert processed.calls[0]["invoice"]["id"] == "in_1"
        assert processed.calls[0]["event"]["type"] == "invoice.payment_failed"
        assert processed.calls[0]["account"] == self.tenant

    def test_invoice_paid(self):
        self.api.get(f"{API}/invoices/in_1", json=invoice())
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="active"))
        assert self.post_event(event("invoice.paid", invoice())).json()["outcome"] == "applied"

    def test_fiscal_document_issued_reaches_the_signal(self):
        fiscal = {
            "status": "issued",
            "number": "FE-10",
            "cufe": "abc",
            "pdf_url": "https://x/pdf",
            "dian_message": None,
        }
        self.api.get(f"{API}/invoices/in_1", json={**invoice(), "fiscal_document": fiscal})
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        with SignalRecorder(event_processed) as processed:
            self.post_event(event("invoice.fiscal_document.issued", invoice()))
        assert processed.calls[0]["invoice"]["fiscal_document"]["number"] == "FE-10"

    def test_one_off_invoice_without_subscription(self):
        self.api.get(f"{API}/invoices/in_2", json=invoice("in_2", subscription_id=None))
        with SignalRecorder(event_processed) as processed:
            response = self.post_event(event("invoice.paid", invoice("in_2", subscription_id=None)))
        assert response.json()["outcome"] == "ignored"
        assert processed.calls[0]["billing_account"].pk == self.billing.pk
        assert len(self.api_calls("GET")) == 1

    def test_unhandled_type_is_recorded_without_calling_bucli(self):
        response = self.post_event(event("customer.updated", {"id": "cus_1", "object": "customer"}))
        assert response.json()["outcome"] == "ignored" and not self.api.calls
        assert ProcessedEvent.objects.get(event_id="evt_1").type == "customer.updated"

    def test_event_of_the_other_mode_is_ignored(self):
        response = self.post_event(event("subscription.updated", subscription(livemode=True), livemode=True))
        assert response.json()["outcome"] == "wrong_mode" and not self.api.calls

    def test_unknown_account_is_unmatched(self):
        self.api.get(f"{API}/subscriptions/sub_x", json=subscription("sub_x", customer="cus_unknown"))
        response = self.post_event(event("subscription.created", subscription("sub_x", customer="cus_unknown")))
        assert response.status_code == 200 and response.json()["outcome"] == "unmatched"
        assert ProcessedEvent.objects.get(event_id="evt_1").billing_account is None

    def test_object_gone_is_recorded(self):
        self.api.get(f"{API}/subscriptions/sub_1", status=404, json=error("resource_missing"))
        assert self.post_event(event("subscription.updated", subscription())).json()["outcome"] == "missing"

    def test_bucli_down_answers_503_and_the_redelivery_applies(self):
        self.api.get(f"{API}/subscriptions/sub_1", status=503, json=error("engine_unavailable", type_="api_error"))
        first = self.post_event(event("subscription.updated", subscription()))
        assert first.status_code == 503 and not ProcessedEvent.objects.exists()
        self.api.replace("GET", f"{API}/subscriptions/sub_1", json=subscription())
        second = self.post_event(event("subscription.updated", subscription()))
        assert second.status_code == 200 and second.json()["outcome"] == "applied"

    def test_multi_item_subscription_plan_comes_from_the_mapped_item(self):
        items = [item("si_seat", "price_seat", 3), item("si_base", "price_basic", 1)]
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(items=items))
        self.post_event(event("subscription.updated", subscription(items=items)))
        self.billing.refresh_from_db()
        assert (self.billing.price_id, self.billing.plan) == ("price_basic", "basic")
        assert [entry["quantity"] for entry in self.billing.items] == [3, 1]

    def test_failing_receiver_does_not_break_the_webhook(self):
        def broken(sender, **kwargs):
            raise RuntimeError("boom")

        subscription_changed.connect(broken, weak=False)
        self.addCleanup(subscription_changed.disconnect, broken)
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        with self.assertLogs("bucli.contrib.django", level="ERROR"):
            response = self.post_event(event("subscription.updated", subscription()))
        assert response.status_code == 200

    def test_update_failed_refreshes_the_subscription(self):
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription(status="active"))
        with SignalRecorder(event_processed) as processed:
            response = self.post_event(event("subscription.update_failed", subscription()))
        assert response.json()["outcome"] == "applied"
        assert processed.calls[0]["account"] == self.tenant

    def test_refund_reaches_the_account_through_its_subscription(self):
        refund = {
            "id": "re_1",
            "object": "refund",
            "payment": "py_1",
            "invoice": "in_1",
            "subscription": "sub_1",
            "status": "succeeded",
            "amount": 2000,
        }
        self.api.get(f"{API}/refunds/re_1", json=refund)
        self.api.get(f"{API}/invoices/in_1", json=invoice())
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        with SignalRecorder(event_processed) as processed:
            response = self.post_event(event("charge.refunded", refund))
        assert response.json()["outcome"] == "applied"
        call = processed.calls[0]
        assert call["account"] == self.tenant and call["invoice"]["id"] == "in_1"

    def test_refund_without_invoice_goes_by_subscription(self):
        refund = {"id": "re_2", "object": "refund", "invoice": None, "subscription": "sub_1", "status": "succeeded"}
        self.api.get(f"{API}/refunds/re_2", json=refund)
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        with SignalRecorder(event_processed) as processed:
            self.post_event(event("charge.refunded", refund))
        assert processed.calls[0]["account"] == self.tenant and processed.calls[0]["invoice"] is None

    def test_notice_reaches_the_account_by_its_customer(self):
        notice = {"id": "ntc_1", "object": "notice", "customer": "cus_1", "subscription": None, "kind": "card_expiring"}
        with SignalRecorder(event_processed) as processed:
            response = self.post_event(event("notice.sent", notice))
        assert response.json()["outcome"] == "ignored" and not self.api.calls
        assert processed.calls[0]["billing_account"].pk == self.billing.pk
        assert processed.calls[0]["event"]["data"]["object"]["kind"] == "card_expiring"

    def test_notice_of_a_subscription_refreshes_it(self):
        notice = {"id": "ntc_2", "object": "notice", "customer": "cus_1", "subscription": "sub_1"}
        self.api.get(f"{API}/subscriptions/sub_1", json=subscription())
        assert self.post_event(event("notice.sent", notice)).json()["outcome"] == "applied"

    def test_handled_events_exist_in_bucli(self):
        assert set(bucli.EVENT_TYPES) >= webhooks.HANDLED_EVENTS


class ImportedSubscriptionTests(BucliTestCase):
    def test_imported_subscription_links_by_metadata(self):
        """``import_subscriptions`` with ``metadata.bucli_account``: the first event links the account."""
        imported = dict(
            subscription("sub_imp", customer="cus_imp"),
            metadata={"bucli_account": f"tenants.tenant:{self.tenant.pk}", "legacy_id": "klopi-sub-1"},
        )
        self.api.get(f"{API}/subscriptions/sub_imp", json=imported)
        response = self.post_event(event("subscription.created", imported))
        assert response.json()["outcome"] == "applied"
        billing = BillingAccount.objects.get(account=self.tenant)
        assert (billing.bucli_customer_id, billing.bucli_subscription_id) == ("cus_imp", "sub_imp")

    def test_label_of_another_model_is_ignored(self):
        foreign = dict(
            subscription("sub_f", customer="cus_f"), metadata={"bucli_account": f"auth.user:{self.tenant.pk}"}
        )
        self.api.get(f"{API}/subscriptions/sub_f", json=foreign)
        assert self.post_event(event("subscription.created", foreign)).json()["outcome"] == "unmatched"
