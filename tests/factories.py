"""Objects as Bucli's API answers them (``apps/developers/api/objects.py``)."""

API = "https://api.bucli.co/v1"


def price(price_id="price_pro", amount=89900, type_="recurring"):
    return {
        "id": price_id,
        "object": "price",
        "product": "prod_1",
        "type": type_,
        "amount": amount,
        "currency": "COP",
        "interval": "month" if type_ == "recurring" else None,
        "interval_count": 1 if type_ == "recurring" else None,
        "active": True,
        "metadata": {},
        "livemode": False,
    }


def item(item_id="si_1", price_id="price_pro", quantity=1, subscription_id="sub_1"):
    return {
        "id": item_id,
        "object": "subscription_item",
        "subscription": subscription_id,
        "price": price(price_id),
        "quantity": quantity,
        "metadata": {},
        "livemode": False,
    }


def subscription(
    sub_id="sub_1",
    status="active",
    customer="cus_1",
    items=None,
    current_period_end="2026-12-01T05:00:00Z",
    cancel_at_period_end=False,
    pending_price=None,
    livemode=False,
    updated=None,
):
    items = items if items is not None else [item(subscription_id=sub_id)]
    return {
        "id": sub_id,
        "object": "subscription",
        "status": status,
        "customer": customer,
        "price": items[0]["price"]["id"] if items else None,
        "items": {
            "object": "list",
            "data": items,
            "has_more": False,
            "url": f"/v1/subscription_items?subscription={sub_id}",
        },
        "billing_cycle_anchor": None,
        "pending_price": pending_price,
        "default_payment_method": "pm_1",
        "latest_invoice": "in_1",
        "start_date": "2026-11-01T05:00:00Z",
        "current_period_start": "2026-11-01T05:00:00Z",
        "current_period_end": current_period_end,
        "next_billing_at": current_period_end,
        "trial_start": None,
        "trial_end": None,
        "cancel_at_period_end": cancel_at_period_end,
        "canceled_at": None,
        "ended_at": None,
        "cancellation_details": {"reason": None, "comment": None},
        "metadata": {},
        "livemode": livemode,
        "created": "2026-11-01T05:00:00Z",
        "updated": updated,
    }


def invoice(invoice_id="in_1", subscription_id="sub_1", customer="cus_1", status="paid"):
    return {
        "id": invoice_id,
        "object": "invoice",
        "status": status,
        "customer": customer,
        "subscription": subscription_id,
        "amount": 89900,
        "currency": "COP",
        "fiscal_document": None,
        "livemode": False,
    }


def checkout_session(session_id="cs_1", subscription_id="sub_1", customer="cus_1", reference=None, status="complete"):
    return {
        "id": session_id,
        "object": "checkout.session",
        "status": status,
        "url": None if status != "open" else f"https://pay.bucli.co/c/tok_{session_id}",
        "price": "price_pro",
        "customer": customer,
        "client_reference_id": reference,
        "subscription": subscription_id,
        "livemode": False,
    }


def page(data, url, has_more=False):
    return {"object": "list", "data": data, "has_more": has_more, "url": url}


def event(event_type, obj, event_id="evt_1", sequence=1, livemode=False):
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "sequence": sequence,
        "api_version": "2026-11-01",
        "data": {"object": obj},
        "request": None,
        "livemode": livemode,
        "created": "2026-11-01T05:00:00Z",
    }


def error(
    code,
    message="Algo falló.",
    type_="invalid_request_error",
    param=None,
    request_id="req_1",
    object_id=None,
    object_type=None,
):
    body = {
        "type": type_,
        "code": code,
        "message": message,
        "param": param,
        "doc_url": f"https://bucli.co/docs/api/errores#{code}",
        "request_id": request_id,
    }
    if object_id is not None:
        # A replay that cannot repeat a secret or a URL names the object.
        body.update(object_id=object_id, object_type=object_type)
    return {"error": body}
