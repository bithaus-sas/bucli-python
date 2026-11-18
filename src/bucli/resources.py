"""The resources of ``/v1``, one class per resource, one method per documented endpoint.

Only what Bucli's API documentation registry documents is here:
``@route`` declares the method + path each method calls, and
``tests/test_contract.py`` checks every declared (and every actually sent)
route against that registry. Updates are ``PATCH`` (Bucli's choice), actions
are ``POST /{id}/<action>``.

Every method takes the API's own parameter names as keyword arguments
(``customers.create(email=..., metadata={...})``); ``None`` values are not sent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import quote

from bucli.errors import InvalidRequestError
from bucli.keys import reserve_request_key
from bucli.objects import BucliObject, ListObject

if TYPE_CHECKING:
    from bucli.client import Bucli

F = TypeVar("F", bound=Callable[..., Any])

#: Every (method, path template) this library calls: ``("GET", "/customers/{id}")``.
ROUTES: list[tuple[str, str, str]] = []


def route(method: str, template: str) -> Callable[[F], F]:
    """Declares the endpoint a resource method calls (read by the contract test)."""

    def decorate(fn: F) -> F:
        fn.__bucli_route__ = (method, template)  # type: ignore[attr-defined]
        ROUTES.append((method, template, fn.__qualname__))
        return fn

    return decorate


def _id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Falta el id del objeto (cus_…, sub_…).")
    return quote(value.strip(), safe="")


class Resource:
    path = ""

    def __init__(self, client: Bucli) -> None:
        self._client = client

    def _item(self, object_id: str, suffix: str = "") -> str:
        return f"{self.path}/{_id(object_id)}{suffix}"

    def _post(
        self,
        path: str,
        params: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        expand: Iterable[str] | None = None,
    ) -> BucliObject:
        return self._client.request("POST", path, body=params, idempotency_key=idempotency_key, expand=expand)

    def _patch(self, path: str, params: dict[str, Any], expand: Iterable[str] | None = None) -> BucliObject:
        return self._client.request("PATCH", path, body=params, expand=expand)

    def _get(self, path: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._client.request("GET", path, expand=expand)

    def _delete(self, path: str, body: dict[str, Any] | None = None) -> BucliObject:
        # A body only on ``DELETE /subscription_items/{id}`` and ``/subscriptions/{id}/discount``.
        return self._client.request("DELETE", path, body=body)

    def _list(self, path: str, params: dict[str, Any], expand: Iterable[str] | None = None) -> ListObject:
        if expand:
            params = {**params, "expand[]": list(expand)}
        return self._client._get_list(path, params)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class Account(Resource):
    path = "/account"

    @route("GET", "/account")
    def retrieve(self) -> BucliObject:
        """Tu comercio como lo ve la API en el modo de la llave."""
        return self._get(self.path)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class Customers(Resource):
    path = "/customers"

    @route("POST", "/customers")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``email`` (obligatorio), ``external_id`` (tu id: un cliente por empresa; con él el correo puede
        repetirse), ``name``, ``phone``, ``fiscal`` (sus datos para la factura electrónica, enteros), ``legal_id_type``,
        ``legal_id``, ``metadata``. Un ``external_id`` o, sin él, un correo repetido responde
        ``409 resource_already_exists``."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/customers")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``limit``, ``starting_after``, ``ending_before``, ``email``, ``external_id``."""
        return self._list(self.path, params, expand)

    @route("GET", "/customers/{id}")
    def retrieve(self, customer_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(customer_id), expand=expand)

    @route("PATCH", "/customers/{id}")
    def update(self, customer_id: str, **params: Any) -> BucliObject:
        """Lo que envías; ``fiscal`` va entero (reemplaza el anterior)."""
        return self._patch(self._item(customer_id), params)

    @route("DELETE", "/customers/{id}")
    def delete(self, customer_id: str) -> BucliObject:
        """Responde ``{id, object, deleted: true}``; con suscripciones en curso, ``422``."""
        return self._delete(self._item(customer_id))

    @route("GET", "/customers/{id}/entitlements")
    def entitlements(self, customer_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        """Sus suscripciones con acceso completo combinadas: ``features``, ``limits``, ``access``,
        ``read_only_reason``, ``period_end``, ``trial_end``. Es lo que cuentan sus medidores."""
        return self._get(self._item(customer_id, "/entitlements"), expand=expand)

    @route("GET", "/customers/{id}/payment_methods")
    def list_payment_methods(
        self, customer_id: str, *, expand: Iterable[str] | None = None, **params: Any
    ) -> ListObject:
        return self._list(self._item(customer_id, "/payment_methods"), params, expand)


# ---------------------------------------------------------------------------
# Products and prices
# ---------------------------------------------------------------------------


class Products(Resource):
    path = "/products"

    @route("POST", "/products")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``name`` (obligatorio), ``description``, ``active``, ``metadata``."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/products")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``active`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/products/{id}")
    def retrieve(self, product_id: str) -> BucliObject:
        return self._get(self._item(product_id))

    @route("PATCH", "/products/{id}")
    def update(self, product_id: str, **params: Any) -> BucliObject:
        """``active=False`` lo archiva."""
        return self._patch(self._item(product_id), params)

    @route("DELETE", "/products/{id}")
    def delete(self, product_id: str) -> BucliObject:
        """Solo un producto sin precios (``422 product_has_prices``)."""
        return self._delete(self._item(product_id))


class Prices(Resource):
    path = "/prices"

    @route("POST", "/prices")
    def create(
        self, *, idempotency_key: str | None = None, expand: Iterable[str] | None = None, **params: Any
    ) -> BucliObject:
        """``product`` (id u objeto ``{name}``), ``amount`` (pesos enteros), ``type``
        (``recurring`` por defecto u ``one_time``), ``recurring={interval, interval_count, trial_days}``
        o ``interval``/``interval_count``/``trial_days`` planos, ``nickname``, ``active``, ``metadata``."""
        return self._post(self.path, params, idempotency_key=idempotency_key, expand=expand)

    @route("GET", "/prices")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``product``, ``active``, ``type`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/prices/{id}")
    def retrieve(self, price_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(price_id), expand=expand)

    @route("PATCH", "/prices/{id}")
    def update(self, price_id: str, **params: Any) -> BucliObject:
        """Solo ``nickname``, ``active`` y ``metadata``: el monto no se edita nunca."""
        return self._patch(self._item(price_id), params)


# ---------------------------------------------------------------------------
# Subscriptions and their items
# ---------------------------------------------------------------------------


class Subscriptions(Resource):
    path = "/subscriptions"

    @route("POST", "/subscriptions")
    def create(
        self, *, idempotency_key: str | None = None, expand: Iterable[str] | None = None, **params: Any
    ) -> BucliObject:
        """``customer`` (obligatorio) y ``price`` (+ ``quantity``) o ``items=[{price, quantity}]``;
        ``default_payment_method``, ``trial_days`` o ``trial_end``, ``trial_key`` (una prueba por
        clave: ``409 trial_already_used``), ``billing_cycle_anchor`` (fecha futura: nace ``active``
        sin cobrar ahora), ``coupon``, ``overage_caps``, ``metadata``.
        Las fechas pueden ir como ``datetime`` con zona horaria, ISO 8601 o timestamp Unix."""
        return self._post(self.path, params, idempotency_key=idempotency_key, expand=expand)

    @route("GET", "/subscriptions")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``status``, ``customer``, ``price`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/subscriptions/{id}")
    def retrieve(self, subscription_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(subscription_id), expand=expand)

    @route("PATCH", "/subscriptions/{id}")
    def update(self, subscription_id: str, **params: Any) -> BucliObject:
        """``price``, ``default_payment_method``, ``cancel_at_period_end`` (``False`` deshace una
        cancelación programada), ``overage_caps``, ``metadata``.

        ``proration_behavior="none"`` (por defecto): el cambio aplica ya y se cobra desde el
        siguiente periodo. ``"create_prorations"`` con ``expected_amount`` (lo que dijo
        ``preview``): lo que sube se cobra ya, prorrateado, y aplica cuando Wompi lo aprueba
        (``subscription.updated``; si falla, ``subscription.update_failed``); lo que baja espera
        la renovación. Si el valor cambió: ``409 quote_changed`` con la cotización nueva en
        ``error.quote``. Mientras el cambio espera su cobro responde ``202`` con el
        ``subscription_change`` en vez de la suscripción.

        ``items=[{"id": "si_…", "price": "price_…", "quantity": 2}, …]`` (en vez de ``price``, solo
        con ``create_prorations``): todos los ítems después del cambio; el primero es el plan y
        todos van a su intervalo. Así cambia de ciclo un plan con adicionales."""
        return self._patch(self._item(subscription_id), params)

    @route("POST", "/subscriptions/{id}/preview")
    def preview(
        self,
        subscription_id: str,
        *,
        price: str | None = None,
        items: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Lo que cuesta AHORA un cambio (``subscription_quote``), el mismo cálculo que cobra el
        cambio con ``create_prorations``: ``price`` (suscripción de un ítem) o ``items`` (todos los
        ítems después del cambio: ``[{id?, price, quantity?}]``; uno actual que no esté se quita).
        No cambia nada. Envía su ``amount`` como ``expected_amount``."""
        body = {"price": price, "items": items}
        return self._post(self._item(subscription_id, "/preview"), body, idempotency_key=idempotency_key)

    @route("POST", "/subscriptions/{id}/cancel")
    def cancel(
        self,
        subscription_id: str,
        *,
        at_period_end: bool | None = None,
        reason: str | None = None,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Al instante, o al fin del periodo con ``at_period_end=True``."""
        body = {"at_period_end": at_period_end, "reason": reason, "comment": comment}
        return self._post(self._item(subscription_id, "/cancel"), body, idempotency_key=idempotency_key)

    @route("POST", "/subscriptions/{id}/pause")
    def pause(
        self,
        subscription_id: str,
        *,
        resumes_at: Any = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Pausa el cobro al terminar el periodo pagado (``pause_at_period_end``); con ``resumes_at``
        (después del fin del periodo) se reanuda sola."""
        body = {"resumes_at": resumes_at, "reason": reason}
        return self._post(self._item(subscription_id, "/pause"), body, idempotency_key=idempotency_key)

    @route("POST", "/subscriptions/{id}/resume")
    def resume(
        self, subscription_id: str, *, reason: str | None = None, idempotency_key: str | None = None
    ) -> BucliObject:
        """Deshace una pausa programada, o reanuda una suscripción ``paused`` (abre su periodo y lo cobra)."""
        return self._post(self._item(subscription_id, "/resume"), {"reason": reason}, idempotency_key=idempotency_key)

    @route("POST", "/subscriptions/{id}/extend_period")
    def extend_period(
        self,
        subscription_id: str,
        *,
        days: int | None = None,
        until: Any = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Días gratis: corre el fin del periodo (``days`` o ``until``) sin cobrar."""
        body = {"days": days, "until": until, "reason": reason}
        return self._post(self._item(subscription_id, "/extend_period"), body, idempotency_key=idempotency_key)

    @route("POST", "/subscriptions/{id}/extend_trial")
    def extend_trial(
        self,
        subscription_id: str,
        *,
        trial_end: Any,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Alarga la prueba hasta ``trial_end``."""
        body = {"trial_end": trial_end, "reason": reason}
        return self._post(self._item(subscription_id, "/extend_trial"), body, idempotency_key=idempotency_key)

    @route("POST", "/subscriptions/{id}/end_trial")
    def end_trial(
        self, subscription_id: str, *, reason: str | None = None, idempotency_key: str | None = None
    ) -> BucliObject:
        """Termina la prueba ya: abre el primer periodo y lo cobra."""
        return self._post(
            self._item(subscription_id, "/end_trial"), {"reason": reason}, idempotency_key=idempotency_key
        )

    @route("POST", "/subscriptions/{id}/discount")
    def apply_discount(
        self,
        subscription_id: str,
        *,
        coupon: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Aplica un cupón (``coupon_…``): rebaja las facturas de periodo desde la próxima."""
        body = {"coupon": coupon, "reason": reason}
        return self._post(self._item(subscription_id, "/discount"), body, idempotency_key=idempotency_key)

    @route("DELETE", "/subscriptions/{id}/discount")
    def delete_discount(self, subscription_id: str, *, reason: str | None = None) -> BucliObject:
        """Quita el descuento: las facturas ya abiertas lo conservan."""
        return self._delete(self._item(subscription_id, "/discount"), {"reason": reason})

    @route("GET", "/subscriptions/{id}/actions")
    def list_actions(self, subscription_id: str, **params: Any) -> ListObject:
        """La bitácora de lo que se hizo a mano (pausa, días, cupón, pago manual, devolución…)."""
        return self._list(self._item(subscription_id, "/actions"), params)

    @route("GET", "/subscriptions/{id}/entitlements")
    def entitlements(self, subscription_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        """Lo que dan sus productos con tus ajustes, y su ``access`` (``full``, ``read_only``, ``none``)."""
        return self._get(self._item(subscription_id, "/entitlements"), expand=expand)

    @route("POST", "/subscriptions/{id}/entitlement_overrides")
    def set_entitlement_overrides(
        self, subscription_id: str, *, idempotency_key: str | None = None, **params: Any
    ) -> BucliObject:
        """Tus ajustes completos: ``features_add``, ``features_remove``, ``limits`` (mínimos), ``note``;
        o ``keep_current=True`` (con ``note``) antes de bajar de plan. Sin parámetros los borra."""
        return self._post(
            self._item(subscription_id, "/entitlement_overrides"), params, idempotency_key=idempotency_key
        )


class SubscriptionItems(Resource):
    path = "/subscription_items"

    @route("POST", "/subscription_items")
    def create(
        self, *, idempotency_key: str | None = None, expand: Iterable[str] | None = None, **params: Any
    ) -> BucliObject:
        """``subscription``, ``price`` (obligatorios), ``quantity``, ``metadata``. Con
        ``proration_behavior="create_prorations"`` y ``expected_amount`` se cobra ya, prorrateado
        (ver ``subscriptions.update``); por defecto, desde el siguiente periodo."""
        return self._post(self.path, params, idempotency_key=idempotency_key, expand=expand)

    @route("GET", "/subscription_items")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``subscription`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/subscription_items/{id}")
    def retrieve(self, item_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(item_id), expand=expand)

    @route("PATCH", "/subscription_items/{id}")
    def update(self, item_id: str, **params: Any) -> BucliObject:
        """``price`` y/o ``quantity``; ``proration_behavior`` y ``expected_amount`` como en
        ``subscriptions.update``."""
        return self._patch(self._item(item_id), params)

    @route("DELETE", "/subscription_items/{id}")
    def delete(
        self, item_id: str, *, proration_behavior: str | None = None, expected_amount: int | None = None
    ) -> BucliObject:
        """``none`` (por defecto) lo quita ya; ``create_prorations`` (con ``expected_amount=0``) lo deja
        hasta la renovación. El último ítem no se quita (``400 subscription_item_last``)."""
        body = {"proration_behavior": proration_behavior, "expected_amount": expected_amount}
        return self._delete(self._item(item_id), body)


# ---------------------------------------------------------------------------
# Payment methods
# ---------------------------------------------------------------------------


class PaymentMethods(Resource):
    path = "/payment_methods"

    @route("POST", "/payment_methods")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """Importa una fuente de pago que ya existe en TU cuenta de Wompi:
        ``customer``, ``wompi_payment_source_id`` (obligatorios), ``make_default``, ``metadata``
        (guarda ahí tu ``legacy_id``). Idempotente por fuente."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    def import_wompi_source(
        self,
        *,
        customer: str,
        wompi_payment_source_id: str | int,
        make_default: bool | None = None,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Lo mismo que ``create``, con los nombres explícitos."""
        return self.create(
            customer=customer,
            wompi_payment_source_id=str(wompi_payment_source_id),
            make_default=make_default,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )

    @route("GET", "/payment_methods/{id}")
    def retrieve(self, payment_method_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(payment_method_id), expand=expand)

    @route("POST", "/payment_methods/{id}/default")
    def set_default(self, payment_method_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """Lo hace predeterminado; si el cliente debía, el motor reintenta el cobro."""
        return self._post(self._item(payment_method_id, "/default"), {}, idempotency_key=idempotency_key)

    @route("POST", "/payment_methods/{id}/backup")
    def set_backup(self, payment_method_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """La tarjeta de respaldo: si el banco rechaza la predeterminada, se intenta con esta."""
        return self._post(self._item(payment_method_id, "/backup"), {}, idempotency_key=idempotency_key)

    @route("DELETE", "/payment_methods/{id}")
    def delete(self, payment_method_id: str) -> BucliObject:
        """Lo quita del cliente: responde el método con ``customer: null``."""
        return self._delete(self._item(payment_method_id))

    detach = delete

    def list(self, *, customer: str, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """Los métodos de un cliente (``GET /customers/{id}/payment_methods``)."""
        return self._client.customers.list_payment_methods(customer, expand=expand, **params)


# ---------------------------------------------------------------------------
# Invoices and payments
# ---------------------------------------------------------------------------


class Invoices(Resource):
    path = "/invoices"

    @route("POST", "/invoices")
    def create(
        self, *, idempotency_key: str | None = None, expand: Iterable[str] | None = None, **params: Any
    ) -> BucliObject:
        """Una factura única (un cobro único): ``customer`` y ``lines=[{price, quantity}]`` con precios
        ``one_time``, o ``amount`` + ``description``; ``default_payment_method``, ``auto_charge``
        (por defecto ``True``), ``retry`` (por defecto ``False``), ``metadata``."""
        return self._post(self.path, params, idempotency_key=idempotency_key, expand=expand)

    @route("GET", "/invoices")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``status``, ``customer``, ``subscription`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/invoices/{id}")
    def retrieve(self, invoice_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(invoice_id), expand=expand)

    @route("GET", "/invoices/{id}/lines")
    def list_lines(self, invoice_id: str, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        return self._list(self._item(invoice_id, "/lines"), params, expand)

    @route("POST", "/invoices/{id}/pay")
    def pay(self, invoice_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """Cobra ya una factura ``open`` (``409 invoice_charge_blocked`` si un freno lo detiene)."""
        return self._post(self._item(invoice_id, "/pay"), {}, idempotency_key=idempotency_key)

    @route("POST", "/invoices/{id}/void")
    def void(self, invoice_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """Anula una factura ``draft`` u ``open``."""
        return self._post(self._item(invoice_id, "/void"), {}, idempotency_key=idempotency_key)

    @route("POST", "/invoices/{id}/mark_paid")
    def mark_paid(
        self,
        invoice_id: str,
        *,
        reference: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Pagada fuera de Wompi (una transferencia): ``reference`` de la transferencia, ``reason``."""
        body = {"reference": reference, "reason": reason}
        return self._post(self._item(invoice_id, "/mark_paid"), body, idempotency_key=idempotency_key)


class Payments(Resource):
    path = "/payments"

    @route("GET", "/payments")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """Cada intento de cobro enviado a Wompi: ``invoice``, ``status``."""
        return self._list(self.path, params, expand)

    @route("GET", "/payments/{id}")
    def retrieve(self, payment_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(payment_id), expand=expand)

    @route("POST", "/payments/{id}/refund")
    def refund(
        self,
        payment_id: str,
        *,
        amount: int | None = None,
        reason: str | None = None,
        cancel_subscription: bool | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Devuelve dinero de un pago aprobado (todo lo que queda, o ``amount``). Responde el
        ``refund``: ``pending`` hasta que Wompi lo confirme (``charge.refunded``)."""
        body = {"amount": amount, "reason": reason, "cancel_subscription": cancel_subscription}
        return self._post(self._item(payment_id, "/refund"), body, idempotency_key=idempotency_key)

    @route("POST", "/payments/{id}/void")
    def void(
        self,
        payment_id: str,
        *,
        reason: str | None = None,
        cancel_subscription: bool | None = None,
        idempotency_key: str | None = None,
    ) -> BucliObject:
        """Anula un cobro con tarjeta del mismo día (Bogotá). Responde el ``refund`` (``kind: void``)."""
        body = {"reason": reason, "cancel_subscription": cancel_subscription}
        return self._post(self._item(payment_id, "/void"), body, idempotency_key=idempotency_key)


class Refunds(Resource):
    path = "/refunds"

    @route("GET", "/refunds")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``payment``, ``invoice``, ``status`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/refunds/{id}")
    def retrieve(self, refund_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(refund_id), expand=expand)


# ---------------------------------------------------------------------------
# Coupons and fixed-term offers
# ---------------------------------------------------------------------------


class Coupons(Resource):
    path = "/coupons"

    @route("POST", "/coupons")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``name``, ``percent_off`` o ``amount_off`` (+ ``currency``), ``duration`` (``once``,
        ``repeating`` con ``duration_in_months``, ``forever``), ``applies_to`` (``all``, ``plan``,
        ``addons``), ``max_redemptions``, ``redeem_by``, ``metadata``. Solo rebaja facturas de periodo."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/coupons")
    def list(self, **params: Any) -> ListObject:
        return self._list(self.path, params)

    @route("GET", "/coupons/{id}")
    def retrieve(self, coupon_id: str) -> BucliObject:
        return self._get(self._item(coupon_id))

    @route("PATCH", "/coupons/{id}")
    def update(self, coupon_id: str, **params: Any) -> BucliObject:
        """``name``, ``active``, ``max_redemptions``, ``redeem_by``, ``metadata``; los términos solo sin usos."""
        return self._patch(self._item(coupon_id), params)

    @route("DELETE", "/coupons/{id}")
    def delete(self, coupon_id: str) -> BucliObject:
        """No se aplica a nadie más; los descuentos vigentes siguen."""
        return self._delete(self._item(coupon_id))


class FixedTermOffers(Resource):
    path = "/fixed_term_offers"

    @route("POST", "/fixed_term_offers")
    def create(
        self, *, idempotency_key: str | None = None, expand: Iterable[str] | None = None, **params: Any
    ) -> BucliObject:
        """Un plan por tiempo fijo pagado de una vez con un link de Wompi: ``customer``,
        ``items=[{price, quantity}]``, ``days``, ``amount``, ``units=[{meter, units}]``, ``name``,
        ``link_days``. Pagado, la suscripción corre esos días y termina sin renovar."""
        return self._post(self.path, params, idempotency_key=idempotency_key, expand=expand)

    @route("GET", "/fixed_term_offers")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``customer``, ``status`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/fixed_term_offers/{id}")
    def retrieve(self, offer_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(offer_id), expand=expand)

    @route("POST", "/fixed_term_offers/{id}/cancel")
    def cancel(self, offer_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """Su link de pago deja de funcionar."""
        return self._post(self._item(offer_id, "/cancel"), {}, idempotency_key=idempotency_key)


# ---------------------------------------------------------------------------
# Events and webhook endpoints
# ---------------------------------------------------------------------------


class Events(Resource):
    path = "/events"

    @route("GET", "/events")
    def list(self, **params: Any) -> ListObject:
        """Los de los últimos 30 días: ``type`` (``invoice.paid`` o ``invoice.*``)."""
        return self._list(self.path, params)

    @route("GET", "/events/{id}")
    def retrieve(self, event_id: str) -> BucliObject:
        return self._get(self._item(event_id))

    @route("POST", "/events/{id}/resend")
    def resend(
        self, event_id: str, *, webhook_endpoint: str | None = None, idempotency_key: str | None = None
    ) -> BucliObject:
        """Lo entrega otra vez, con el mismo id (a un ``webhook_endpoint`` o a todos los que lo escuchan)."""
        body = {"webhook_endpoint": webhook_endpoint}
        return self._post(self._item(event_id, "/resend"), body, idempotency_key=idempotency_key)


class WebhookEndpoints(Resource):
    path = "/webhook_endpoints"

    @route("POST", "/webhook_endpoints")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``url`` (HTTPS pública), ``enabled_events`` (obligatorios), ``description``, ``metadata``.
        La respuesta trae ``secret`` (``whsec_…``) UNA sola vez: guárdalo."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/webhook_endpoints")
    def list(self, **params: Any) -> ListObject:
        return self._list(self.path, params)

    @route("GET", "/webhook_endpoints/{id}")
    def retrieve(self, endpoint_id: str) -> BucliObject:
        return self._get(self._item(endpoint_id))

    @route("PATCH", "/webhook_endpoints/{id}")
    def update(self, endpoint_id: str, **params: Any) -> BucliObject:
        """``url``, ``enabled_events``, ``description``, ``disabled``, ``metadata``."""
        return self._patch(self._item(endpoint_id), params)

    @route("DELETE", "/webhook_endpoints/{id}")
    def delete(self, endpoint_id: str) -> BucliObject:
        return self._delete(self._item(endpoint_id))

    @route("POST", "/webhook_endpoints/{id}/roll_secret")
    def roll_secret(self, endpoint_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """Secreto nuevo (una sola vez). Durante 24 h cada entrega trae dos firmas ``v1``."""
        return self._post(self._item(endpoint_id, "/roll_secret"), {}, idempotency_key=idempotency_key)


# ---------------------------------------------------------------------------
# Entitlements and metering
# ---------------------------------------------------------------------------


class Features(Resource):
    """Tu vocabulario de derechos: módulos (``boolean``) y límites (``limit``)."""

    path = "/features"

    @route("POST", "/features")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``key``, ``name``, ``type`` (``boolean``/``limit``); de un límite ``limit_kind``
        (``static``/``metered``) y ``allows_unlimited``; ``description``, ``metadata``."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/features")
    def list(self, **params: Any) -> ListObject:
        """``type``, ``key`` además de la paginación."""
        return self._list(self.path, params)

    @route("GET", "/features/{id}")
    def retrieve(self, feature_id: str) -> BucliObject:
        return self._get(self._item(feature_id))

    @route("PATCH", "/features/{id}")
    def update(self, feature_id: str, **params: Any) -> BucliObject:
        """``name``, ``description``, ``allows_unlimited``, ``metadata`` (la clave no cambia)."""
        return self._patch(self._item(feature_id), params)

    @route("DELETE", "/features/{id}")
    def delete(self, feature_id: str) -> BucliObject:
        """Solo uno que nada usa (``422 feature_in_use``)."""
        return self._delete(self._item(feature_id))


class Meters(Resource):
    """Cupos por ventana. ``meter`` es el id (``mtr_…``) o la clave (``documents``)."""

    path = "/meters"

    @route("POST", "/meters")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``key`` (un límite ``metered`` de tu vocabulario), ``name``, ``window``, ``timezone``,
        ``on_unavailable`` (``allow``/``deny``), ``threshold_percent``, ``active``, ``metadata``."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/meters")
    def list(self, **params: Any) -> ListObject:
        return self._list(self.path, params)

    @route("GET", "/meters/{id}")
    def retrieve(self, meter: str) -> BucliObject:
        return self._get(self._item(meter))

    @route("PATCH", "/meters/{id}")
    def update(self, meter: str, **params: Any) -> BucliObject:
        return self._patch(self._item(meter), params)

    @route("GET", "/meters/{id}/summary")
    def summary(self, meter: str, *, customer: str) -> BucliObject:
        """La ventana en curso: ``allowance``, ``used``, ``remaining`` y las bolsas vivas."""
        return self._client.request("GET", self._item(meter, "/summary"), params={"customer": customer})

    @route("POST", "/meters/{id}/reserve")
    def reserve(
        self,
        meter: str,
        *,
        customer: str,
        idempotency_key: str,
        quantity: int | None = None,
        billable: bool | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> BucliObject:
        """Toma unidades ahora o lanza ``BillingError`` con ``code == "quota_exhausted"``. La misma
        ``idempotency_key`` (el id de tu documento) devuelve la misma reserva. Para el modo
        ``allow`` usa ``bucli.metering.UsageGuard``."""
        body = {
            "customer": customer,
            "idempotency_key": idempotency_key,
            "quantity": quantity,
            "billable": billable,
            "metadata": metadata,
        }
        return self._client.request(
            "POST",
            self._item(meter, "/reserve"),
            body=body,
            idempotency_key=reserve_request_key(meter, customer, idempotency_key),
            timeout=timeout,
            max_retries=max_retries,
        )


class Usage(Resource):
    """El consumo: reportes tardíos y devoluciones."""

    path = "/usage"

    @route("POST", "/usage")
    def report(
        self,
        *,
        meter: str,
        customer: str,
        idempotency_key: str,
        occurred_at: Any,
        quantity: int | None = None,
        billable: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BucliObject:
        """Lo que dejaste pasar mientras Bucli no respondía. ``idempotency_key`` es la de la unidad
        (la misma que habrías usado al reservar); nunca se niega."""
        body = {
            "meter": meter,
            "customer": customer,
            "idempotency_key": idempotency_key,
            "occurred_at": occurred_at,
            "quantity": quantity,
            "billable": billable,
            "metadata": metadata,
        }
        return self._post(self.path, body, idempotency_key=f"report:{idempotency_key}")

    @route("GET", "/usage")
    def list(self, **params: Any) -> ListObject:
        """``meter``, ``customer`` además de la paginación."""
        return self._list(self.path, params)

    @route("GET", "/usage/{id}")
    def retrieve(self, usage_id: str) -> BucliObject:
        return self._get(self._item(usage_id))

    @route("POST", "/usage/{id}/release")
    def release(self, usage_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        """Cada unidad vuelve a su origen, una sola vez (un documento rechazado, un borrador borrado)."""
        return self._post(self._item(usage_id, "/release"), {}, idempotency_key=idempotency_key)


class GrantOffers(Resource):
    """Las bolsas que vendes."""

    path = "/grant_offers"

    @route("POST", "/grant_offers")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``meter``, ``price`` (pago único), ``name``, ``units``, ``validity_months``,
        ``requires_features``, ``active``, ``position``, ``metadata``."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/grant_offers")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``meter``, ``active`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/grant_offers/{id}")
    def retrieve(self, offer_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(offer_id), expand=expand)

    @route("PATCH", "/grant_offers/{id}")
    def update(self, offer_id: str, **params: Any) -> BucliObject:
        return self._patch(self._item(offer_id), params)

    @route("POST", "/grant_offers/{id}/purchase")
    def purchase(
        self, offer_id: str, *, customer: str, payment_method: str | None = None, idempotency_key: str | None = None
    ) -> BucliObject:
        """Cobra la bolsa a la tarjeta del cliente. Responde la factura; la bolsa llega con
        ``invoice.paid`` y ``grant.created``."""
        body = {"customer": customer, "payment_method": payment_method}
        return self._post(self._item(offer_id, "/purchase"), body, idempotency_key=idempotency_key)


class Grants(Resource):
    """Las bolsas de los clientes."""

    path = "/grants"

    @route("POST", "/grants")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """Un regalo: ``customer``, ``meter``, ``units``, ``expires_at``, ``note``; para importar,
        ``imported=True`` y ``remaining``."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/grants")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``customer``, ``meter``, ``status`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/grants/{id}")
    def retrieve(self, grant_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(grant_id), expand=expand)

    @route("POST", "/grants/{id}/withdraw")
    def withdraw(self, grant_id: str, *, note: str | None = None, idempotency_key: str | None = None) -> BucliObject:
        return self._post(self._item(grant_id, "/withdraw"), {"note": note}, idempotency_key=idempotency_key)


# ---------------------------------------------------------------------------
# Checkout, payment links and the portal
# ---------------------------------------------------------------------------

#: A replayed POST whose first answer carried a URL with a token answers this
#: 409 instead of the URL (Bucli never stores it).
URL_UNAVAILABLE_ON_REPLAY = "url_unavailable_on_replay"


class CheckoutSessions(Resource):
    path = "/checkout_sessions"

    @route("POST", "/checkout_sessions")
    def create(
        self, *, idempotency_key: str | None = None, expand: Iterable[str] | None = None, **params: Any
    ) -> BucliObject:
        """``success_url`` (obligatorio) y ``price`` (+ ``quantity``) o ``line_items=[{price, quantity}]``
        (plan base, puestos, adicionales: mismo intervalo, recurrentes); ``cancel_url``, ``customer`` o
        ``customer_email``, ``customer_fields``, ``trial_days``, ``client_reference_id``, ``metadata`` y
        ``subscription_data={metadata, trial_days}`` (la ``metadata`` va a la suscripción que se crea).
        Redirige a ``url``.

        Si un reintento de red repite la petición y Bucli ya la había hecho, el replay no trae la URL
        (``409 url_unavailable_on_replay``, con el id en ``error.object_id``): la librería consulta la
        sesión por ese id para recuperarla."""
        try:
            return self._post(self.path, params, idempotency_key=idempotency_key, expand=expand)
        except InvalidRequestError as error:
            session_id = _replayed_id(error, "checkout.session")
            if session_id is None:
                raise
            return self.retrieve(session_id, expand=expand)

    @route("GET", "/checkout_sessions/{id}")
    def retrieve(self, session_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        """Al volver a ``success_url``: qué suscripción quedó creada (no actives el plan con esto
        sin mirar el estado de la suscripción)."""
        return self._get(self._item(session_id), expand=expand)

    @route("POST", "/checkout_sessions/{id}/expire")
    def expire(self, session_id: str, *, idempotency_key: str | None = None) -> BucliObject:
        return self._post(self._item(session_id, "/expire"), {}, idempotency_key=idempotency_key)


class PaymentLinks(Resource):
    path = "/payment_links"

    @route("POST", "/payment_links")
    def create(self, *, idempotency_key: str | None = None, **params: Any) -> BucliObject:
        """``price`` (+ ``quantity``) o ``line_items=[{price, quantity}]``; ``customer_fields``, ``trial_days``,
        ``success_url``, ``active``, ``metadata``. Cada visita abre un checkout con esas líneas."""
        return self._post(self.path, params, idempotency_key=idempotency_key)

    @route("GET", "/payment_links")
    def list(self, *, expand: Iterable[str] | None = None, **params: Any) -> ListObject:
        """``active``, ``price`` además de la paginación."""
        return self._list(self.path, params, expand)

    @route("GET", "/payment_links/{id}")
    def retrieve(self, link_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        return self._get(self._item(link_id), expand=expand)

    @route("PATCH", "/payment_links/{id}")
    def update(self, link_id: str, **params: Any) -> BucliObject:
        """``active=False`` lo desactiva; el precio no cambia (crea otro enlace)."""
        return self._patch(self._item(link_id), params)


class PortalSessions(Resource):
    path = "/portal_sessions"

    @route("POST", "/portal_sessions")
    def create(
        self, *, customer: str, return_url: str | None = None, idempotency_key: str | None = None
    ) -> BucliObject:
        """La ``url`` del portal del cliente, válida una hora. Solo se responde al crearla: si un
        reintento cae en el replay (``409 url_unavailable_on_replay``) se crea otra con una clave nueva
        (``retrieve`` no la vuelve a mostrar)."""
        body = {"customer": customer, "return_url": return_url}
        try:
            return self._post(self.path, body, idempotency_key=idempotency_key)
        except InvalidRequestError as error:
            if error.code != URL_UNAVAILABLE_ON_REPLAY:
                raise
            return self._post(self.path, body)

    @route("GET", "/portal_sessions/{id}")
    def retrieve(self, session_id: str, *, expand: Iterable[str] | None = None) -> BucliObject:
        """Para qué cliente es y hasta cuándo vale, SIN la ``url`` (``None``): es una credencial que
        solo se responde al crearla."""
        return self._get(self._item(session_id), expand=expand)


def _replayed_id(error: InvalidRequestError, object_type: str) -> str | None:
    """The id a ``url_unavailable_on_replay`` names in ``error.object_id``
    when the replayed object is an ``object_type``, or ``None``."""
    if error.code != URL_UNAVAILABLE_ON_REPLAY or not error.object_id:
        return None
    if error.object_type not in (None, object_type):
        return None
    return error.object_id
