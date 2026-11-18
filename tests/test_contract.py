"""The library against Bucli's API documentation registry (a private repository).

Reads ``bucli-api/apps/developers/docs/reference.py`` WITHOUT Django (it is
parsed with ``ast``: it imports the billing models) and imports the two
constant modules that import nothing (``apps/developers/constants.py``,
``apps/webhooks/constants.py``) from their paths. Fails when the library
calls a method + route the API does not document, when the API documents one
the library lacks, or when the version / event types / error types drift.

The API checkout is looked up at ``$BUCLI_API_DIR`` or ``../bucli-api``.
"""

import ast
import inspect
import os
import re
import types
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import responses

import bucli
from bucli import constants, errors, resources
from bucli.client import RETRYABLE_CODES

from factories import API
from test_resources import CASES

ROOT = Path(__file__).resolve().parents[1]
API_DIR = Path(os.environ.get("BUCLI_API_DIR", ROOT.parent / "bucli-api"))
REFERENCE = API_DIR / "apps" / "developers" / "docs" / "reference.py"

pytestmark = pytest.mark.skipif(not REFERENCE.exists(), reason=f"No está bucli-api en {API_DIR}")

#: Resource methods that call another documented method instead of declaring a route.
ALIASES = {
    "PaymentMethods.import_wompi_source": "PaymentMethods.create",
    "PaymentMethods.list": "Customers.list_payment_methods",
    "PaymentMethods.detach": "PaymentMethods.delete",
}


def _load(path: Path, name: str):
    """Executes a module that imports nothing, from its path, without writing
    ``__pycache__`` into bucli-api."""
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


@pytest.fixture(scope="module")
def reference_tree():
    return ast.parse(REFERENCE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def documented(reference_tree) -> set[tuple[str, str]]:
    """``{(method, "/customers/{id}"), ...}`` from ``ENDPOINTS = [E(id, method, path, ...)]``."""
    routes = set()
    for call in _calls_named(reference_tree, "E"):
        method, path = call.args[1], call.args[2]
        assert isinstance(method, ast.Constant) and isinstance(path, ast.Constant)
        routes.add((method.value, path.value))
    assert len(routes) > 40, "No se pudo leer ENDPOINTS de reference.py"
    return routes


def _matcher(documented):
    patterns = [
        (method, re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(path)) + "$"))
        for method, path in documented
    ]

    def is_documented(method: str, path: str) -> bool:
        return any(m == method and pattern.match(path) for m, pattern in patterns)

    return is_documented


def test_every_declared_route_is_documented(documented):
    declared = {(method, path) for method, path, _ in resources.ROUTES}
    assert declared - documented == set(), "La librería llama rutas que la API no documenta"


def test_every_documented_route_is_in_the_library(documented):
    declared = {(method, path) for method, path, _ in resources.ROUTES}
    assert documented - declared == set(), "La API documenta rutas que la librería no expone"


def test_every_public_resource_method_declares_a_route_or_is_an_alias():
    for _, cls in inspect.getmembers(resources, inspect.isclass):
        if not issubclass(cls, resources.Resource) or cls is resources.Resource:
            continue
        for name, member in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_"):
                continue
            qualname = f"{cls.__name__}.{name}"
            route = getattr(member, "__bucli_route__", None)
            assert route is not None or qualname in ALIASES, f"{qualname} no declara @route"


@pytest.mark.parametrize(("name", "call"), [(case[0], case[1]) for case in CASES], ids=[case[0] for case in CASES])
@responses.activate
def test_what_is_actually_sent_is_documented(client, documented, name, call):
    """Executes each resource method and checks the request that left."""
    for method in ("GET", "POST", "PATCH", "DELETE"):
        responses.add(method, re.compile(re.escape(API) + ".*"), json={"id": "x_1", "object": "thing"})
    call(client)
    is_documented = _matcher(documented)
    for sent in responses.calls:
        path = urlsplit(sent.request.url).path
        assert path.startswith("/v1/")
        assert is_documented(sent.request.method, path[len("/v1") :]), f"{sent.request.method} {path}"


def test_every_resource_method_is_exercised():
    """The dynamic check above covers every declared route."""
    names = {case[0].split(" ")[0] for case in CASES}
    for _, _path, qualname in resources.ROUTES:
        cls, method = qualname.split(".")
        attr = {
            "Account": "account",
            "Customers": "customers",
            "Products": "products",
            "Prices": "prices",
            "Subscriptions": "subscriptions",
            "SubscriptionItems": "subscription_items",
            "PaymentMethods": "payment_methods",
            "Invoices": "invoices",
            "Payments": "payments",
            "Refunds": "refunds",
            "Coupons": "coupons",
            "FixedTermOffers": "fixed_term_offers",
            "Events": "events",
            "WebhookEndpoints": "webhook_endpoints",
            "CheckoutSessions": "checkout_sessions",
            "PaymentLinks": "payment_links",
            "PortalSessions": "portal_sessions",
            "Features": "features",
            "Meters": "meters",
            "Usage": "usage",
            "GrantOffers": "grant_offers",
            "Grants": "grants",
        }[cls]
        assert f"{attr}.{method}" in names, f"{qualname} no tiene caso en test_resources.CASES"


def test_api_version_matches():
    developers = _load(API_DIR / "apps" / "developers" / "constants.py", "bucli_api_developers_constants")
    assert developers.API_VERSION == constants.API_VERSION == bucli.API_VERSION
    assert developers.VERSION_HEADER == constants.VERSION_HEADER
    assert developers.IDEMPOTENCY_HEADER == constants.IDEMPOTENCY_HEADER
    assert developers.REPLAYED_HEADER == constants.REPLAYED_HEADER


def test_event_types_and_signature_match():
    webhooks = _load(API_DIR / "apps" / "webhooks" / "constants.py", "bucli_api_webhooks_constants")
    assert tuple(webhooks.EVENT_TYPE_KEYS) == constants.EVENT_TYPES
    assert webhooks.SIGNATURE_HEADER == constants.SIGNATURE_HEADER
    assert webhooks.SIGNATURE_SCHEME == constants.SIGNATURE_SCHEME
    assert webhooks.SIGNATURE_TOLERANCE_SECONDS == constants.SIGNATURE_TOLERANCE_SECONDS


def test_every_error_type_has_a_class(reference_tree):
    """``ERROR_CODES = [(code, status, type, when), ...]``."""
    node = next(
        n
        for n in ast.walk(reference_tree)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ERROR_CODES" for t in n.targets)
    )
    rows = [tuple(elt.value for elt in row.elts[:3]) for row in node.value.elts]
    assert rows
    for code, status, error_type in rows:
        assert error_type in errors.ERROR_TYPES, f"{code}: tipo {error_type} sin clase"
        cls = errors._class_for(error_type, code, status)
        assert issubclass(cls, errors.BucliError)
    codes = {code for code, _, _ in rows}
    assert set(errors.ERROR_CODES) <= codes
    assert set(RETRYABLE_CODES) <= codes
    assert resources.URL_UNAVAILABLE_ON_REPLAY in codes


def test_subscription_statuses_match():
    """``Subscription.Status`` values, read from the billing models' source."""
    source = (API_DIR / "apps" / "billing" / "models" / "__init__.py").read_text(encoding="utf-8")
    block = source.split("class Subscription(", 1)[1].split("class CancellationReason", 1)[0]
    values = set(re.findall(r'^\s+[A-Z_]+ = "([a-z_]+)", ', block, flags=re.MULTILINE))
    assert values == set(constants.SUBSCRIPTION_STATUSES)
