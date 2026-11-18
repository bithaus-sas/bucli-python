"""``bucli.Webhook.construct_event``: the receiver's side of ``Bucli-Signature``."""

import hashlib
import hmac
import json
import time

import pytest

import bucli

SECRET = "whsec_test_secret"
OLD_SECRET = "whsec_old_secret"
BODY = json.dumps({"id": "evt_1", "object": "event", "type": "invoice.paid", "data": {"object": {}}}).encode()


def sign(body: bytes, secret: str, timestamp: int) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def header(*signatures: str, timestamp: int) -> str:
    return ",".join([f"t={timestamp}", *(f"v1={item}" for item in signatures)])


def test_valid_signature():
    now = int(time.time())
    event = bucli.Webhook.construct_event(BODY, header(sign(BODY, SECRET, now), timestamp=now), SECRET)
    assert isinstance(event, bucli.BucliObject)
    assert event.id == "evt_1" and event.type == "invoice.paid"


def test_same_algorithm_as_bucli_api():
    """The bytes Bucli signs: ``f"{timestamp}.{body}".encode()`` (apps/webhooks/services.py)."""
    now = int(time.time())
    text = BODY.decode()
    theirs = hmac.new(SECRET.encode(), f"{now}.{text}".encode(), hashlib.sha256).hexdigest()
    assert bucli.Webhook.construct_event(BODY, f"t={now},v1={theirs}", SECRET).id == "evt_1"


def test_utf8_body():
    now = int(time.time())
    body = json.dumps({"id": "evt_ñ", "type": "customer.updated", "name": "Ana Pérez"}, ensure_ascii=False).encode()
    assert bucli.Webhook.construct_event(body, header(sign(body, SECRET, now), timestamp=now), SECRET).id == "evt_ñ"


@pytest.mark.parametrize("order", ["old_first", "new_first"])
def test_rotation_two_v1_either_secret(order):
    now = int(time.time())
    old, new = sign(BODY, OLD_SECRET, now), sign(BODY, SECRET, now)
    value = header(old, new, timestamp=now) if order == "old_first" else header(new, old, timestamp=now)
    assert bucli.Webhook.construct_event(BODY, value, SECRET).id == "evt_1"
    assert bucli.Webhook.construct_event(BODY, value, OLD_SECRET).id == "evt_1"


def test_receiver_side_rotation_list_of_secrets():
    now = int(time.time())
    value = header(sign(BODY, SECRET, now), timestamp=now)
    assert bucli.Webhook.construct_event(BODY, value, [OLD_SECRET, SECRET]).id == "evt_1"


def test_wrong_secret():
    now = int(time.time())
    with pytest.raises(bucli.SignatureVerificationError, match="Ninguna"):
        bucli.Webhook.construct_event(BODY, header(sign(BODY, "whsec_other", now), timestamp=now), SECRET)


def test_tampered_body():
    now = int(time.time())
    value = header(sign(BODY, SECRET, now), timestamp=now)
    tampered = BODY.replace(b"invoice.paid", b"invoice.void")
    with pytest.raises(bucli.SignatureVerificationError):
        bucli.Webhook.construct_event(tampered, value, SECRET)


def test_tampered_timestamp():
    now = int(time.time())
    value = header(sign(BODY, SECRET, now), timestamp=now - 1)
    with pytest.raises(bucli.SignatureVerificationError):
        bucli.Webhook.construct_event(BODY, value, SECRET)


def test_old_timestamp_is_refused():
    old = int(time.time()) - 301
    with pytest.raises(bucli.SignatureVerificationError, match="vieja"):
        bucli.Webhook.construct_event(BODY, header(sign(BODY, SECRET, old), timestamp=old), SECRET)


def test_future_timestamp_is_refused():
    future = int(time.time()) + 301
    with pytest.raises(bucli.SignatureVerificationError, match="futuro"):
        bucli.Webhook.construct_event(BODY, header(sign(BODY, SECRET, future), timestamp=future), SECRET)


def test_custom_tolerance_and_now():
    ts = 1_800_000_000
    value = header(sign(BODY, SECRET, ts), timestamp=ts)
    assert bucli.Webhook.construct_event(BODY, value, SECRET, tolerance=600, now=ts + 500).id == "evt_1"
    with pytest.raises(bucli.SignatureVerificationError):
        bucli.Webhook.construct_event(BODY, value, SECRET, now=ts + 500)


@pytest.mark.parametrize(
    "value",
    ["", "garbage", "t=abc,v1=00", "v1=00", "t=1800000000", "t=1800000000,v0=abc"],
)
def test_malformed_header(value):
    with pytest.raises(bucli.SignatureVerificationError):
        bucli.Webhook.construct_event(BODY, value, SECRET, now=1_800_000_000)


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_non_json_or_non_object_body(body):
    now = int(time.time())
    with pytest.raises(bucli.SignatureVerificationError, match="JSON"):
        bucli.Webhook.construct_event(body, header(sign(body, SECRET, now), timestamp=now), SECRET)


def test_missing_secret_is_a_configuration_error():
    now = int(time.time())
    with pytest.raises(ValueError):
        bucli.Webhook.construct_event(BODY, header(sign(BODY, SECRET, now), timestamp=now), "")


def test_generate_test_header_round_trips():
    value = bucli.Webhook.generate_test_header(BODY, [OLD_SECRET, SECRET])
    assert value.count("v1=") == 2
    assert bucli.Webhook.construct_event(BODY, value, SECRET).id == "evt_1"
