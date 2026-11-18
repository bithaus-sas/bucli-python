"""The metering guard (``bucli.metering.UsageGuard``): Bucli answers, refuses,
or does not answer — and a meter in ``allow`` mode never stops the business."""

import datetime
import json

import pytest
import requests
import responses

from bucli.errors import APIConnectionError
from bucli.metering import MemoryUsageStore, UsageGuard

from factories import API, error

RESERVE = f"{API}/meters/documents/reserve"
USAGE = f"{API}/usage"


def record(remaining=9, usage_id="usg_1"):
    return {
        "id": usage_id,
        "object": "usage_record",
        "meter": "documents",
        "customer": "cus_1",
        "quantity": 1,
        "source": "allowance",
        "remaining": remaining,
    }


@pytest.fixture
def store():
    return MemoryUsageStore()


def guard(client, store, policy="allow", **kwargs):
    return UsageGuard(client, on_unavailable=policy, store=store, **kwargs)


@responses.activate
def test_an_answer_is_used_and_its_balance_kept(client, store):
    responses.post(RESERVE, json=record(remaining=4), status=201)
    reservation = guard(client, store).reserve("documents", customer="cus_1", idempotency_key="doc-1")
    assert reservation.allowed and not reservation.offline and reservation.usage_id == "usg_1"
    assert reservation.remaining == 4
    sent = responses.calls[0].request
    assert json.loads(sent.body) == {"customer": "cus_1", "idempotency_key": "doc-1", "quantity": 1, "billable": True}
    assert sent.headers["Idempotency-Key"] == "reserve:documents:cus_1:doc-1"


@responses.activate
def test_a_refusal_is_an_answer_not_an_outage(client, store):
    responses.post(RESERVE, json=error("quota_exhausted", type_="billing_error"), status=402)
    reservation = guard(client, store).reserve("documents", customer="cus_1", idempotency_key="doc-1")
    assert not reservation.allowed and reservation.refusal == "quota_exhausted"
    assert store.pending(10) == []


@responses.activate
def test_at_zero_it_refuses_without_calling(client, store):
    responses.post(RESERVE, json=record(remaining=0), status=201)
    watcher = guard(client, store)
    assert watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1").allowed
    second = watcher.reserve("documents", customer="cus_1", idempotency_key="doc-2")
    assert not second.allowed and second.refusal == "quota_exhausted" and len(responses.calls) == 1
    watcher.forget("cus_1")  # a grant bought (grant.created)
    responses.post(RESERVE, json=record(remaining=99, usage_id="usg_2"), status=201)
    assert watcher.reserve("documents", customer="cus_1", idempotency_key="doc-2").allowed


@responses.activate
def test_allow_lets_the_unit_pass_queues_it_and_reports_it_later(client, store):
    responses.post(RESERVE, body=requests.exceptions.ConnectTimeout("lento"))
    moment = datetime.datetime(2026, 11, 10, 15, 0, tzinfo=datetime.UTC)
    watcher = guard(client, store, clock=lambda: moment)
    reservation = watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    assert reservation.allowed and reservation.offline and reservation.record is None
    assert len(responses.calls) == 1  # 2 s and no retries: the business does not wait
    [unit] = store.pending(10)
    assert unit.idempotency_key == "doc-1" and unit.occurred_at == moment

    responses.post(USAGE, json={**record(), "reported": True}, status=201)
    assert watcher.flush() == {"reported": 1, "failed": 0}
    sent = responses.calls[-1].request
    assert json.loads(sent.body)["occurred_at"] == "2026-11-10T15:00:00+00:00"
    assert sent.headers["Idempotency-Key"] == "report:doc-1"
    assert store.pending(10) == []


@responses.activate
def test_a_flush_while_bucli_is_still_down_keeps_the_queue(client, store):
    responses.post(RESERVE, body=requests.exceptions.ConnectionError("caído"))
    watcher = guard(client, store)
    watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    responses.post(USAGE, json=error("internal_error", type_="api_error"), status=500)
    assert watcher.flush() == {"reported": 0, "failed": 1}
    assert len(store.pending(10)) == 1


@responses.activate
def test_deny_raises_when_bucli_does_not_answer(client, store):
    responses.post(RESERVE, body=requests.exceptions.ConnectionError("caído"))
    with pytest.raises(APIConnectionError):
        guard(client, store, policy={"documents": "deny"}).reserve("documents", customer="cus_1", idempotency_key="d")
    assert store.pending(10) == []


# -- regressions of the audit -----------------------------------------------------------


@responses.activate
def test_an_outage_never_spends_report_attempts(client, store):
    responses.post(RESERVE, body=requests.exceptions.ConnectionError("caído"))
    watcher = guard(client, store)
    watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    responses.post(USAGE, body=requests.exceptions.ConnectionError("sigue caído"))
    for _ in range(30):
        watcher.flush()
    [unit] = store.pending(10)
    assert unit.attempts == 0


@responses.activate
def test_a_refusal_for_more_than_is_left_caches_what_is_left(client, store):
    body = error("quota_exhausted", type_="billing_error")
    body["error"].update({"allowance": 10, "used": 9, "grants_remaining": 0})
    responses.post(RESERVE, json=body, status=402)
    watcher = guard(client, store)
    refused = watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1", quantity=3)
    assert not refused.allowed and refused.remaining == 1
    responses.post(RESERVE, json=record(remaining=0), status=201)
    assert watcher.reserve("documents", customer="cus_1", idempotency_key="doc-2").allowed


@responses.activate
def test_a_release_drops_the_cached_balance(client, store):
    responses.post(RESERVE, json=record(remaining=0), status=201)
    responses.post(f"{API}/usage/usg_1/release", json={**record(), "released_at": "2026-11-10T15:00:00Z"})
    watcher = guard(client, store)
    watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    watcher.release("usg_1")
    assert store.remaining("documents", "cus_1", 300) is None


@responses.activate
def test_a_unit_let_through_that_never_happened_is_discarded(client, store):
    responses.post(RESERVE, body=requests.exceptions.ConnectionError("caído"))
    watcher = guard(client, store)
    watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    assert watcher.discard("documents", customer="cus_1", idempotency_key="doc-1") is True
    assert store.pending(10) == []


@responses.activate
def test_a_naive_clock_is_taken_as_utc_and_never_blocks_the_queue(client, store):
    responses.post(RESERVE, body=requests.exceptions.ConnectionError("caído"))
    watcher = guard(client, store, clock=lambda: datetime.datetime(2026, 11, 10, 15, 0))
    watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    responses.post(USAGE, json=record(), status=201)
    assert watcher.flush() == {"reported": 1, "failed": 0}
    assert json.loads(responses.calls[-1].request.body)["occurred_at"] == "2026-11-10T15:00:00+00:00"


@responses.activate
def test_two_meters_may_reserve_the_same_document_key(client, store):
    responses.post(RESERVE, json=record(), status=201)
    responses.post(f"{API}/meters/sms/reserve", json=record(usage_id="usg_2"), status=201)
    watcher = guard(client, store)
    watcher.reserve("documents", customer="cus_1", idempotency_key="doc-1")
    watcher.reserve("sms", customer="cus_1", idempotency_key="doc-1")
    headers = [call.request.headers["Idempotency-Key"] for call in responses.calls]
    assert len(set(headers)) == 2
