"""Objects read by attribute without losing the dict; cursor pagination."""

import copy
import json
import pickle
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses

import bucli
from bucli.objects import convert

from factories import API, page, subscription


def test_object_is_a_dict_with_attributes():
    sub = convert(subscription())
    assert isinstance(sub, dict) and isinstance(sub, bucli.BucliObject)
    assert sub.status == sub["status"] == "active"
    assert sub.cancellation_details.reason is None
    assert isinstance(sub["items"], bucli.ListObject)
    assert sub["items"].data[0].price.id == "price_pro"
    assert json.loads(json.dumps(sub)) == subscription()
    assert sub.to_dict() == subscription() and type(sub.to_dict()) is dict


def test_missing_attribute_raises_attribute_error():
    sub = convert({"id": "sub_1", "object": "subscription"})
    with pytest.raises(AttributeError):
        _ = sub.nope
    assert getattr(sub, "nope", "x") == "x"


def test_set_and_delete_by_attribute():
    obj = convert({"id": "cus_1"})
    obj.name = "Ana"
    assert obj["name"] == "Ana"
    del obj.name
    assert "name" not in obj


def test_copy_and_pickle():
    sub = convert(subscription())
    assert copy.deepcopy(sub) == sub
    restored = pickle.loads(pickle.dumps(sub))
    assert restored == sub and restored.status == "active"


@responses.activate
def test_list_returns_one_page(client):
    responses.get(
        f"{API}/customers",
        json=page([{"id": "cus_3"}, {"id": "cus_2"}], "/v1/customers", has_more=True),
    )
    result = client.customers.list(limit=2)
    assert isinstance(result, bucli.ListObject)
    assert [c.id for c in result.data] == ["cus_3", "cus_2"] and result.has_more
    assert len(responses.calls) == 1


@responses.activate
def test_auto_paging_iter_walks_every_page_with_the_filters(client):
    responses.get(f"{API}/subscriptions", json=page([{"id": "sub_5"}, {"id": "sub_4"}], "/v1/subscriptions", True))
    responses.get(f"{API}/subscriptions", json=page([{"id": "sub_3"}, {"id": "sub_2"}], "/v1/subscriptions", True))
    responses.get(f"{API}/subscriptions", json=page([{"id": "sub_1"}], "/v1/subscriptions", False))
    first = client.subscriptions.list(customer="cus_1", limit=2, expand=["data.customer"])
    ids = [sub.id for sub in first.auto_paging_iter()]
    assert ids == ["sub_5", "sub_4", "sub_3", "sub_2", "sub_1"]
    queries = [parse_qsl(urlsplit(call.request.url).query) for call in responses.calls]
    assert ("starting_after", "sub_4") in queries[1] and ("starting_after", "sub_2") in queries[2]
    for query in queries:
        assert ("customer", "cus_1") in query and ("limit", "2") in query and ("expand[]", "data.customer") in query


@responses.activate
def test_auto_paging_iter_backwards_from_ending_before(client):
    responses.get(f"{API}/events", json=page([{"id": "evt_4"}, {"id": "evt_3"}], "/v1/events", True))
    responses.get(f"{API}/events", json=page([{"id": "evt_6"}, {"id": "evt_5"}], "/v1/events", False))
    first = client.events.list(ending_before="evt_2", limit=2)
    assert [e.id for e in first.auto_paging_iter()] == ["evt_3", "evt_4", "evt_5", "evt_6"]
    second = dict(parse_qsl(urlsplit(responses.calls[1].request.url).query))
    assert second["ending_before"] == "evt_4" and "starting_after" not in second


@responses.activate
def test_embedded_list_pages_from_its_url(client):
    responses.get(
        f"{API}/subscriptions/sub_1", json=dict(subscription(), items={**subscription()["items"], "has_more": True})
    )
    responses.get(f"{API}/subscription_items", json=page([{"id": "si_2"}], "/v1/subscription_items", False))
    sub = client.subscriptions.retrieve("sub_1")
    ids = [item.id for item in sub["items"].auto_paging_iter()]
    assert ids == ["si_1", "si_2"]
    query = dict(parse_qsl(urlsplit(responses.calls[1].request.url).query))
    assert urlsplit(responses.calls[1].request.url).path == "/v1/subscription_items"
    assert query == {"subscription": "sub_1", "starting_after": "si_1"}


@responses.activate
def test_empty_page_stops(client):
    responses.get(f"{API}/invoices", json=page([], "/v1/invoices", False))
    assert list(client.invoices.list().auto_paging_iter()) == []
