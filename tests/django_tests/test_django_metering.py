"""The guard on a Django SaaS: the queue in the database, the balances in the
cache, and ``manage.py bucli_flush_usage``."""

import io

import requests
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings

from bucli.contrib.django import metering
from bucli.contrib.django.models import PendingUsage

from factories import API, error
from helpers import BucliTestCase

SETTINGS = {
    "BUCLI_SECRET_KEY": "sk_test_saas",
    "BUCLI_METER_ON_UNAVAILABLE": {"documents": "allow"},
}


@override_settings(**SETTINGS)
class DatabaseQueueTests(BucliTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_a_unit_let_through_is_queued_once_and_reported_by_the_command(self):
        self.api.post(f"{API}/meters/documents/reserve", body=requests.exceptions.ConnectionError("caído"))
        guard = metering.guard()
        for _ in range(2):
            assert guard.reserve("documents", customer="cus_1", idempotency_key="doc-1").offline
        row = PendingUsage.objects.get()
        assert row.idempotency_key == "doc-1" and row.reported_at is None

        self.api.post(f"{API}/usage", json={"id": "usg_1", "object": "usage_record", "remaining": 3}, status=201)
        out = io.StringIO()
        call_command("bucli_flush_usage", stdout=out)
        assert "Reportados: 1" in out.getvalue()
        row.refresh_from_db()
        assert row.reported_at is not None

    def test_a_refused_report_stays_with_its_error(self):
        PendingUsage.objects.create(
            meter="documents", customer="cus_1", idempotency_key="doc-9", occurred_at="2026-11-01T00:00:00Z"
        )
        self.api.post(f"{API}/usage", json=error("usage_report_invalid"), status=400)
        assert metering.guard().flush() == {"reported": 0, "failed": 1}
        row = PendingUsage.objects.get()
        assert row.attempts == 1 and "usage_report_invalid" in row.last_error

    def test_the_balance_lives_in_the_cache(self):
        self.api.post(
            f"{API}/meters/documents/reserve",
            json={"id": "usg_1", "object": "usage_record", "remaining": 0},
            status=201,
        )
        guard = metering.guard()
        assert guard.reserve("documents", customer="cus_1", idempotency_key="doc-1").allowed
        refused = guard.reserve("documents", customer="cus_1", idempotency_key="doc-2")
        assert not refused.allowed and len(self.api_calls("POST", "/meters/documents/reserve")) == 1
        guard.forget("cus_1")
        assert cache.get("bucli:meter:documents:cus_1") is None


@override_settings(BUCLI_SECRET_KEY="sk_test_saas", BUCLI_METER_ON_UNAVAILABLE="allow")
class ForgetWithoutNamedMetersTests(BucliTestCase):
    def test_forget_drops_every_meter_of_the_customer(self):
        cache.clear()
        self.api.post(
            f"{API}/meters/documents/reserve",
            json={"id": "usg_1", "object": "usage_record", "remaining": 0},
            status=201,
        )
        guard = metering.guard()
        guard.reserve("documents", customer="cus_1", idempotency_key="doc-1")
        guard.forget("cus_1")
        assert cache.get("bucli:meter:documents:cus_1") is None
