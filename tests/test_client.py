"""The HTTP layer: keys, headers, idempotency, retries and typed errors."""

import datetime
import json

import pytest
import requests
import responses
from responses import matchers

import bucli

from factories import API, error

# -- keys and headers ---------------------------------------------------------


@pytest.mark.parametrize("key", ["pk_test_abc", "pk_live_abc"])
def test_publishable_key_is_refused(key):
    with pytest.raises(ValueError, match="publicable"):
        bucli.Bucli(key)


@pytest.mark.parametrize("key", ["", "abc", "rk_test_1", None])
def test_invalid_key_is_refused(key):
    with pytest.raises(ValueError):
        bucli.Bucli(key)


def test_the_key_decides_the_mode():
    assert bucli.Bucli("sk_test_1").livemode is False
    assert bucli.Bucli("sk_live_1").livemode is True


def test_base_url_tolerates_v1_suffix():
    assert bucli.Bucli("sk_test_1", base_url="https://api.bucli.co/v1/").base_url == "https://api.bucli.co"


@responses.activate
def test_headers_on_every_request(client):
    responses.get(f"{API}/account", json={"id": "acct_1", "object": "account"})
    client.account.retrieve()
    sent = responses.calls[0].request.headers
    assert sent["Authorization"] == "Bearer sk_test_abc123"
    assert sent["Bucli-Version"] == bucli.API_VERSION == "2026-11-01"
    assert sent["User-Agent"].startswith(f"bucli-python/{bucli.__version__}")
    assert "Idempotency-Key" not in sent


@responses.activate
def test_custom_api_version(client):
    responses.get(f"{API}/account", json={"id": "acct_1", "object": "account"})
    bucli.Bucli("sk_test_1", api_version="2027-01-01").account.retrieve()
    assert responses.calls[0].request.headers["Bucli-Version"] == "2027-01-01"


# -- idempotency ---------------------------------------------------------------


@responses.activate
def test_every_post_gets_an_idempotency_key(client):
    responses.post(f"{API}/customers", json={"id": "cus_1", "object": "customer"})
    responses.post(f"{API}/customers", json={"id": "cus_2", "object": "customer"})
    client.customers.create(email="a@b.co")
    client.customers.create(email="c@d.co")
    first = responses.calls[0].request.headers["Idempotency-Key"]
    second = responses.calls[1].request.headers["Idempotency-Key"]
    assert len(first) == 36 and first != second


@responses.activate
def test_explicit_idempotency_key_is_used(client):
    responses.post(
        f"{API}/customers",
        json={"id": "cus_1", "object": "customer"},
        match=[matchers.header_matcher({"Idempotency-Key": "alta-ana"})],
    )
    customer = client.customers.create(email="a@b.co", idempotency_key="alta-ana")
    assert customer.last_response.idempotency_key == "alta-ana"


@responses.activate
def test_patch_and_delete_carry_no_idempotency_key(client):
    responses.patch(f"{API}/customers/cus_1", json={"id": "cus_1", "object": "customer"})
    responses.delete(f"{API}/customers/cus_1", json={"id": "cus_1", "object": "customer", "deleted": True})
    client.customers.update("cus_1", name="Ana")
    client.customers.delete("cus_1")
    assert all("Idempotency-Key" not in call.request.headers for call in responses.calls)


@responses.activate
def test_replayed_header_is_exposed(client):
    responses.post(
        f"{API}/customers", json={"id": "cus_1", "object": "customer"}, headers={"Idempotent-Replayed": "true"}
    )
    assert client.customers.create(email="a@b.co").last_response.replayed is True


# -- retries -------------------------------------------------------------------


@responses.activate
def test_network_error_is_retried_with_the_same_key(client, sleeps):
    responses.post(f"{API}/customers", body=requests.exceptions.ConnectionError("reset"))
    responses.post(f"{API}/customers", body=requests.exceptions.ReadTimeout("slow"))
    responses.post(f"{API}/customers", json={"id": "cus_1", "object": "customer"})
    customer = client.customers.create(email="a@b.co")
    assert customer.id == "cus_1"
    keys = {call.request.headers["Idempotency-Key"] for call in responses.calls}
    bodies = {call.request.body for call in responses.calls}
    assert len(responses.calls) == 3 and len(keys) == 1 and len(bodies) == 1
    assert len(sleeps) == 2 and sleeps[0] <= sleeps[1] * 2


@responses.activate
def test_network_error_exhausts_retries(client, sleeps):
    for _ in range(3):
        responses.get(f"{API}/account", body=requests.exceptions.ConnectionError("down"))
    with pytest.raises(bucli.APIConnectionError) as caught:
        client.account.retrieve()
    assert caught.value.http_status is None
    assert len(responses.calls) == 3 and len(sleeps) == 2


@responses.activate
def test_5xx_is_retried(client, sleeps):
    responses.post(f"{API}/subscriptions", status=503, json=error("engine_unavailable", type_="api_error"))
    responses.post(f"{API}/subscriptions", status=500, json=error("internal_error", type_="api_error"))
    responses.post(f"{API}/subscriptions", json={"id": "sub_1", "object": "subscription"})
    assert client.subscriptions.create(customer="cus_1", price="price_1").id == "sub_1"
    assert len({call.request.headers["Idempotency-Key"] for call in responses.calls}) == 1


@responses.activate
def test_5xx_exhausted_raises_api_error(client):
    for _ in range(3):
        responses.get(f"{API}/account", status=500, json=error("internal_error", type_="api_error"))
    with pytest.raises(bucli.APIError) as caught:
        client.account.retrieve()
    assert caught.value.http_status == 500 and caught.value.code == "internal_error"


@responses.activate
def test_429_honours_retry_after(client, sleeps):
    responses.get(
        f"{API}/customers",
        status=429,
        json=error("rate_limited", type_="rate_limit_error"),
        headers={"Retry-After": "3"},
    )
    responses.get(f"{API}/customers", json={"object": "list", "data": [], "has_more": False, "url": "/v1/customers"})
    client.customers.list()
    assert sleeps == [3.0]


@responses.activate
def test_429_with_a_long_retry_after_is_raised(client, sleeps):
    responses.get(
        f"{API}/customers",
        status=429,
        json=error("rate_limited", type_="rate_limit_error"),
        headers={"Retry-After": "120"},
    )
    with pytest.raises(bucli.RateLimitError) as caught:
        client.customers.list()
    assert caught.value.retry_after == 120 and sleeps == []


@responses.activate
def test_idempotency_key_in_use_is_retried(client, sleeps):
    responses.post(
        f"{API}/invoices/in_1/pay", status=409, json=error("idempotency_key_in_use", type_="idempotency_error")
    )
    responses.post(f"{API}/invoices/in_1/pay", json={"id": "in_1", "object": "invoice", "status": "paid"})
    assert client.invoices.pay("in_1").status == "paid"
    assert len(responses.calls) == 2 and len(sleeps) == 1


@responses.activate
def test_idempotency_key_reused_is_not_retried(client, sleeps):
    responses.post(f"{API}/customers", status=409, json=error("idempotency_key_reused", type_="idempotency_error"))
    with pytest.raises(bucli.IdempotencyError) as caught:
        client.customers.create(email="a@b.co", idempotency_key="k")
    assert caught.value.code == "idempotency_key_reused" and len(responses.calls) == 1 and sleeps == []


@pytest.mark.parametrize(
    ("status", "code", "type_", "cls"),
    [
        (400, "parameter_missing", "invalid_request_error", bucli.InvalidRequestError),
        (401, "api_key_invalid", "authentication_error", bucli.AuthenticationError),
        (402, "active_subscriptions_limit_reached", "billing_error", bucli.BillingError),
        (402, "payment_method_rejected", "billing_error", bucli.CardError),
        (403, "secret_key_required", "permission_error", bucli.PermissionError),
        (404, "resource_missing", "invalid_request_error", bucli.InvalidRequestError),
        (409, "resource_already_exists", "invalid_request_error", bucli.InvalidRequestError),
        (422, "customer_has_active_subscriptions", "invalid_request_error", bucli.InvalidRequestError),
    ],
)
@responses.activate
def test_4xx_is_never_retried_and_is_typed(client, sleeps, status, code, type_, cls):
    responses.post(
        f"{API}/customers",
        status=status,
        json=error(code, message="No.", type_=type_, param="email", request_id="req_42"),
    )
    with pytest.raises(cls) as caught:
        client.customers.create(email="a@b.co")
    err = caught.value
    assert (err.http_status, err.code, err.type, err.param, err.request_id, err.message) == (
        status,
        code,
        type_,
        "email",
        "req_42",
        "No.",
    )
    assert err.doc_url.endswith(code)
    assert isinstance(err, bucli.BucliError)
    assert len(responses.calls) == 1 and sleeps == []


@responses.activate
def test_non_json_error_still_typed(client):
    responses.get(f"{API}/account", status=502, body="<html>Bad gateway</html>")
    no_retry = bucli.Bucli("sk_test_1", max_retries=0)
    with pytest.raises(bucli.APIError) as caught:
        no_retry.account.retrieve()
    assert caught.value.http_status == 502


@responses.activate
def test_request_id_from_header_when_missing_in_body(client):
    responses.get(f"{API}/customers/cus_x", status=404, json={}, headers={"Request-Id": "req_h"})
    with pytest.raises(bucli.InvalidRequestError) as caught:
        client.customers.retrieve("cus_x")
    assert caught.value.request_id == "req_h"


# -- bodies and queries --------------------------------------------------------


@responses.activate
def test_none_values_are_not_sent_and_dates_go_iso(client):
    responses.post(f"{API}/subscriptions", json={"id": "sub_1", "object": "subscription"})
    anchor = datetime.datetime(2026, 11, 15, 5, tzinfo=datetime.UTC)
    client.subscriptions.create(customer="cus_1", price="price_1", trial_days=None, billing_cycle_anchor=anchor)
    assert json.loads(responses.calls[0].request.body) == {
        "customer": "cus_1",
        "price": "price_1",
        "billing_cycle_anchor": "2026-11-15T05:00:00+00:00",
    }


def test_naive_datetime_is_refused(client):
    with pytest.raises(ValueError, match="zona horaria"):
        client.subscriptions.create(customer="cus_1", price="p", trial_end=datetime.datetime(2026, 12, 1))


@responses.activate
def test_query_booleans_lists_and_expand(client):
    responses.get(f"{API}/prices", json={"object": "list", "data": [], "has_more": False, "url": "/v1/prices"})
    client.prices.list(active=True, product="prod_1", expand=["data.product"])
    query = responses.calls[0].request.url.split("?", 1)[1]
    assert "active=true" in query and "product=prod_1" in query and "expand%5B%5D=data.product" in query


@responses.activate
def test_expand_on_retrieve_and_post(client):
    responses.get(f"{API}/subscriptions/sub_1", json={"id": "sub_1", "object": "subscription"})
    responses.post(f"{API}/subscriptions", json={"id": "sub_1", "object": "subscription"})
    client.subscriptions.retrieve("sub_1", expand=["customer", "latest_invoice"])
    client.subscriptions.create(customer="cus_1", price="p", expand=["customer"])
    assert "expand%5B%5D=customer&expand%5B%5D=latest_invoice" in responses.calls[0].request.url
    assert "expand%5B%5D=customer" in responses.calls[1].request.url


def test_ids_are_required(client):
    with pytest.raises(ValueError):
        client.customers.retrieve("")


@responses.activate
def test_ids_are_escaped(client):
    responses.get(f"{API}/customers/cus_a%2Fb", json={"id": "cus_a/b", "object": "customer"})
    client.customers.retrieve("cus_a/b")
    assert responses.calls[0].request.url.endswith("/customers/cus_a%2Fb")
