# bucli-python

La librería oficial de **Bucli** para Python. Sirve para que un SaaS cobre sus suscripciones con Bucli en vez de hablar directo con Wompi, y trae una integración completa para proyectos Django.

Trae tres piezas:

- **`bucli.Bucli`**: el cliente HTTP de la API pública `/v1`. Sigue las convenciones de Stripe: llaves `sk_`, `Idempotency-Key`, `Bucli-Version`, paginación por cursor, `expand[]` y errores tipados.
- **`bucli.Webhook`**: la verificación de la firma `Bucli-Signature` de los webhooks.
- **`bucli.contrib.django`** (opcional): la app `bucli_billing`. Trae los modelos, la vista del webhook, los servicios (suscribirse, portal, cambiar de plan, cancelar, sincronizar), la regla de acceso y el comando `bucli_sync`.

La referencia de la API está en OpenAPI 3.1 en `https://api.bucli.co/v1/openapi.json`.

- Python ≥ 3.11.
- La única dependencia del núcleo es `requests`. La parte de Django pide Django ≥ 4.2.
- Versión de la API: `2026-11-01`.

---

## Instalación

```bash
pip install "bucli[django]"

# En el requirements de tu proyecto, fija la versión:
bucli[django]==0.6.0
```

Sin Django basta con `pip install bucli`, sin el extra.

---

## El cliente

```python
import bucli

client = bucli.Bucli("sk_test_…")  # la llave decide el modo: sk_test_ → sandbox de Wompi, sk_live_ → producción
client.livemode  # False
```

| Parámetro | Por defecto | Para qué |
|---|---|---|
| `api_key` | — | Una llave **secreta** (`sk_test_…`/`sk_live_…`). Una `pk_` se rechaza: solo sirve en el navegador. |
| `base_url` | `https://api.bucli.co` | El host. El prefijo `/v1` lo pone la librería. |
| `api_version` | `2026-11-01` | Se envía en `Bucli-Version` en cada petición. |
| `timeout` | `30` | Segundos por intento. |
| `max_retries` | `2` | Reintentos con backoff exponencial y jitter. |
| `max_retry_after` | `20` | Un `Retry-After` más largo no se espera: se lanza el error. |

### Recursos

Cada método lleva los nombres de parámetro de la API como argumentos con nombre. Los `None` no se envían.

| Recurso | Métodos |
|---|---|
| `client.account` | `retrieve()` |
| `client.customers` | `create`, `list`, `retrieve`, `update`, `delete`, `list_payment_methods` |
| `client.products` | `create`, `list`, `retrieve`, `update`, `delete` |
| `client.prices` | `create` (`recurring` u `one_time`), `list`, `retrieve`, `update` |
| `client.subscriptions` | `create` (`items`, `billing_cycle_anchor`, `trial_end`, `trial_key`, `coupon`), `list`, `retrieve`, `update`, `preview`, `cancel`, `pause`, `resume`, `extend_period`, `extend_trial`, `end_trial`, `apply_discount`, `delete_discount`, `list_actions` |
| `client.subscription_items` | `create`, `list`, `retrieve`, `update`, `delete` |
| `client.payment_methods` | `create` / `import_wompi_source`, `retrieve`, `set_default`, `set_backup`, `delete` (`detach`), `list(customer=…)` |
| `client.invoices` | `create` (factura única), `list`, `retrieve`, `list_lines`, `pay`, `void`, `mark_paid` |
| `client.payments` | `list`, `retrieve`, `refund`, `void` |
| `client.refunds` | `list`, `retrieve` |
| `client.coupons` | `create`, `list`, `retrieve`, `update`, `delete` |
| `client.fixed_term_offers` | `create`, `list`, `retrieve`, `cancel` |
| `client.events` | `list`, `retrieve`, `resend` |
| `client.webhook_endpoints` | `create`, `list`, `retrieve`, `update`, `delete`, `roll_secret` |
| `client.checkout_sessions` | `create`, `retrieve`, `expire` |
| `client.payment_links` | `create`, `list`, `retrieve`, `update` |
| `client.portal_sessions` | `create` |

La librería tiene **solo** lo que la API documenta. Las actualizaciones son `PATCH`, como en Bucli. `resume` es lo contrario de `pause`, no de `cancel`: para deshacer una cancelación programada se usa `subscriptions.update(id, cancel_at_period_end=False)`. Una prueba de contrato compara cada ruta contra el registro de la API (ver «Pruebas»).

```python
# Un plan: el producto se crea en el mismo paso
price = client.prices.create(product={"name": "Plan Pro"}, amount=89900, recurring={"interval": "month"})

# Un paquete de pago único
pack = client.prices.create(product="prod_…", amount=50000, type="one_time")

# Cliente y checkout hospedado
customer = client.customers.create(email="ana@tienda.co", name="Ana Pérez", metadata={"tenant": "42"})
session = client.checkout_sessions.create(
    price=price.id,
    customer=customer.id,
    success_url="https://app.co/facturacion/listo",
    cancel_url="https://app.co/planes",
    client_reference_id="42",
)
# → redirige a session.url

# Checkout de varios ítems (plan base + puestos, mismo intervalo) y metadata de la suscripción
session = client.checkout_sessions.create(
    line_items=[{"price": "price_PlanPro", "quantity": 1}, {"price": "price_Puesto", "quantity": 3}],
    customer=customer.id,
    success_url="https://app.co/facturacion/listo",
    subscription_data={"metadata": {"tenant": "42"}},
)
# session.line_items, session.amount_total

# Suscripción con plan base + puestos, que nace active y cobra en el ancla (migración)
import datetime
sub = client.subscriptions.create(
    customer=customer.id,
    items=[{"price": "price_PlanPro", "quantity": 1}, {"price": "price_Puesto", "quantity": 3}],
    billing_cycle_anchor=datetime.datetime(2026, 11, 15, 5, tzinfo=datetime.UTC),  # con zona horaria
    metadata={"legacy_id": "klopi-sub-1042"},
)

# Cambiar el plan sin prorrateo (aplica ya y se cobra desde el próximo periodo)
client.subscriptions.update(sub.id, price="price_PlanScale", proration_behavior="none")

# Con prorrateo: la vista previa dice cuánto se cobra AHORA y el cambio envía ese valor
quote = client.subscriptions.preview(sub.id, price="price_PlanScale")
try:
    client.subscriptions.update(
        sub.id, price="price_PlanScale", proration_behavior="create_prorations", expected_amount=quote.amount
    )
except bucli.InvalidRequestError as e:
    if e.code == "quote_changed":
        quote = e.quote  # la cotización nueva (como la de preview): muéstrala y confirma otra vez

client.subscriptions.cancel(sub.id, at_period_end=True, reason="requested_by_customer")

# Un cupón de una vez para un cliente, aplicado a su suscripción
coupon = client.coupons.create(name="Crédito Ana", amount_off=120000, duration="once", max_redemptions=1)
client.subscriptions.apply_discount(sub.id, coupon=coupon.id)

# Devolver parte de un pago
client.payments.refund("py_…", amount=20000, reason="Cobro doble")

# Factura única cobrada de una a la tarjeta guardada
client.invoices.create(customer=customer.id, lines=[{"price": pack.id, "quantity": 2}])

# Importar una tarjeta que ya está en TU cuenta de Wompi
client.payment_methods.import_wompi_source(customer=customer.id, wompi_payment_source_id=48213, make_default=True)

# El portal del cliente (válido una hora)
url = client.portal_sessions.create(customer=customer.id, return_url="https://app.co/cuenta").url
```

### Objetos

Cada respuesta es un `BucliObject`. Es **el dict** que respondió la API (`json.dumps(obj)` funciona y `obj["status"]` también), y además se lee por atributo: `sub.status`, `sub.cancellation_details.reason`. Para guardarlo, `obj.to_dict()` devuelve una copia hecha solo de `dict` y `list` planos. `obj.last_response` trae `http_status`, `request_id`, `headers`, `idempotency_key` y `replayed`.

> Una clave que choca con un método de `dict` se lee con corchetes. La que importa es la de los ítems de una suscripción: **`sub["items"]`**, porque `sub.items` es `dict.items`.

### Idempotencia y reintentos

- **Cada POST** lleva un `Idempotency-Key` (un uuid4 si no le pasas `idempotency_key=`) y repite **la misma** clave en cada reintento. Un POST reintentado nunca hace la cosa dos veces: Bucli responde la respuesta guardada (`obj.last_response.replayed`).
- **Se reintenta solo** ante errores de red (conexión, timeout), `409 idempotency_key_in_use`, `429` (se respeta `Retry-After`) y `5xx`. **Nunca** ante otro 4xx.
- Un replay que traía una URL con token (checkout o portal) responde `409 url_unavailable_on_replay`, con el id del objeto en `error.object_id` (`e.object_id`, y su tipo en `e.object_type`). La librería lo resuelve sola: si fue un checkout, consulta la sesión por ese id; si fue el portal, crea otra sesión con una clave nueva (`portal_sessions.retrieve` no vuelve a mostrar la URL).

### Paginación y `expand[]`

```python
page = client.subscriptions.list(status="active", limit=100)  # una página
page.data, page.has_more

for sub in client.subscriptions.list(customer="cus_…", limit=100).auto_paging_iter():  # todas
    ...

client.subscriptions.retrieve("sub_…", expand=["customer", "latest_invoice"])
client.invoices.list(expand=["data.customer"])
```

`auto_paging_iter()` conserva los filtros y el `expand[]`, y avanza con `starting_after`. Si la página se pidió con `ending_before`, recorre hacia atrás. También sirve en listas incrustadas, como `sub["items"]`.

### Errores

```python
try:
    client.customers.create(email="ana@tienda.co")
except bucli.InvalidRequestError as e:
    if e.code == "resource_already_exists":
        ...
    print(e.http_status, e.type, e.code, e.message, e.param, e.request_id)
```

| Clase | Cuándo |
|---|---|
| `AuthenticationError` | 401: la llave falta, no existe o fue revocada |
| `PermissionError` | 403: el plan, el tipo de llave o el rol no lo permite |
| `InvalidRequestError` | 400/404/409/413/422: la petición está mal (`param` dice dónde) |
| `IdempotencyError` | 409 `idempotency_key_in_use` / `idempotency_key_reused` |
| `RateLimitError` | 429 (`e.retry_after`) |
| `BillingError` / `CardError` | 402: el plan de Bucli del comercio, o `payment_method_rejected` (Wompi rechazó el método) |
| `APIError` | 5xx o una respuesta que no es JSON |
| `APIConnectionError` | No se llegó a Bucli (DNS, TLS, timeout), ya agotados los reintentos |

Todas heredan de `bucli.BucliError`. El `code` es estable y es lo que conviene comparar en el código. El `message` está en español, para mostrárselo a una persona.

### Webhooks

```python
event = bucli.Webhook.construct_event(
    payload=request.body,                          # el cuerpo CRUDO, en bytes
    sig_header=request.headers["Bucli-Signature"],
    secret="whsec_…",                              # o una lista durante tu propia rotación
    tolerance=300,
)
```

La función:
- verifica `HMAC-SHA256(secreto, "<t>.<cuerpo crudo>")` en tiempo constante;
- acepta **cualquiera** de las firmas `v1`. Durante las 24 horas que siguen a `roll_secret`, Bucli manda dos: la del secreto anterior y la del nuevo;
- rechaza un `t` de hace más de `tolerance` segundos o que venga del futuro;
- rechaza un cuerpo que no sea un objeto JSON.

Si algo de eso falla, lanza `SignatureVerificationError`. Para tus pruebas, `bucli.Webhook.generate_test_header(body, secret)` arma una cabecera válida.

---

## Integrar un SaaS Django paso a paso

### 1. Settings

```python
INSTALLED_APPS = [
    # …
    "tenants",
    "bucli.contrib.django",          # app label: bucli_billing
]

BUCLI_SECRET_KEY = env("BUCLI_SECRET_KEY")          # sk_test_… en staging, sk_live_… en producción
BUCLI_WEBHOOK_SECRET = env("BUCLI_WEBHOOK_SECRET")  # el whsec_… del endpoint (una lista durante una rotación)
BUCLI_ACCOUNT_MODEL = "tenants.Tenant"              # el modelo dueño de la suscripción
BUCLI_PRICE_PLANS = {                               # precios de Bucli → planes internos
    "price_AbC…": "basic",
    "price_DeF…": "pro",
}

# Opcionales
BUCLI_GRACE = None             # past_due mantiene el acceso mientras Bucli reintenta; con timedelta/días, hasta ese plazo tras el fin del periodo
BUCLI_LAPSED_ACCESS = "free"   # o "read_only": qué reciben paused / canceled / incomplete_expired / sin suscripción
BUCLI_FREE_PLAN = "free"
# BUCLI_API_BASE_URL, BUCLI_API_VERSION, BUCLI_TIMEOUT (15), BUCLI_MAX_RETRIES (2), BUCLI_WEBHOOK_TOLERANCE (300)
```

Las llaves viven solo en el servidor. **`BUCLI_ACCOUNT_MODEL` se fija antes del primer `migrate` y no se cambia después**, igual que `AUTH_USER_MODEL`.

### 2. URL del webhook y migraciones

```python
# urls.py
urlpatterns = [
    path("webhooks/bucli/", include("bucli.contrib.django.urls")),   # POST /webhooks/bucli/
]
```

```bash
python manage.py migrate bucli_billing
```

Se crean dos tablas:
- `BillingAccount`: una fila por cuenta del SaaS, con `bucli_customer_id`, `bucli_subscription_id`, `price_id`, `plan`, `status`, `current_period_end`, `cancel_at_period_end`, `items`, `bucli_updated_at` (el `updated` de Bucli de la copia), `last_event_sequence` y `updated_at`. Es la copia del estado de Bucli que sirve para decidir el acceso sin llamar a Bucli. Se lee con `tenant.bucli_billing`.
- `ProcessedEvent`: los eventos ya procesados.

### 3. El botón «Suscribirme»

```python
from django.shortcuts import redirect
from bucli.contrib.django import services
from bucli.contrib.django.exceptions import AlreadySubscribed

def subscribe(request, price_id):
    tenant = request.tenant
    try:
        session = services.start_checkout(
            tenant,
            price_id,
            success_url=request.build_absolute_uri("/facturacion/procesando"),
            cancel_url=request.build_absolute_uri("/planes"),
            email=request.user.email,
            name=tenant.name,
            external_id=str(tenant.pk),          # un cliente por cuenta, nunca por correo
            phone="+573001234567",               # E.164, o None
            fiscal=billing_fiscal(tenant),       # sus datos para la factura electrónica, o None
        )
    except AlreadySubscribed:
        return redirect("billing-portal")   # ya paga: que cambie de plan o use el portal
    return redirect(session.url)
```

`start_checkout` hace tres cosas:
1. Crea el cliente de Bucli, o reusa el vinculado o el que ya tenga ese `external_id` (sin él, ese correo).
2. Guarda el vínculo en `BillingAccount`.
3. Abre la sesión con `client_reference_id = tenant.pk` y `metadata.bucli_account = "tenants.tenant:<pk>"`, y la suscripción que crea lleva lo mismo en su metadata (`subscription_data`).

Con varios ítems (plan base + puestos + adicionales), en vez de `price_id`:

```python
session = services.start_checkout(
    tenant,
    items=["price_PlanPro", ("price_Puesto", 3)],   # o {"price": ..., "quantity": ...}
    success_url=request.build_absolute_uri("/facturacion/procesando"),
    email=request.user.email,
)
```

Al menos un precio debe estar en `BUCLI_PRICE_PLANS` (el que fija el plan).

**Los datos de la factura electrónica (`fiscal`).** Cuando el comercio tiene activa la facturación electrónica, Bucli emite la factura de cada cobro a nombre de quien paga, así que el cliente lleva sus datos fiscales completos: tipo de persona, documento con su DV, razón social o nombres, dirección y municipio DANE, y las responsabilidades del RUT. `fiscal` va entero; si la cuenta todavía no los tiene completos, no lo mandes. Cuando cambian los datos de la cuenta, mándalos otra vez desde una tarea, después del commit:

```python
services.sync_customer(tenant, name=tenant.name, email=owner_email, phone=phone, fiscal=billing_fiscal(tenant))
```

`sync_customer` envía solo lo que no sea `None` y no hace nada si la cuenta aún no tiene cliente en Bucli. `start_checkout` de una cuenta ya vinculada también manda el `phone` y el `fiscal` que reciba. Si `email` cambia, puede responder `409 resource_already_exists` cuando otro cliente sin `external_id` ya tiene ese correo.

**Si ya tenías clientes sin `external_id`:** con `external_id` el cliente se busca solo por él. Una cuenta sin vincular cuyo cliente viejo no tiene `external_id` recibe uno nuevo. Antes de pasar a `external_id`, vincúlalas con `services.link` o ponles su `external_id` (`client.customers.update(id, external_id=…)`).

**La página de `success_url` no activa nada.** Muestra «Estamos confirmando tu pago» y consulta `has_access(tenant)` cada pocos segundos. La activación llega por el webhook, o por `services.sync(tenant)` si quieres forzarla.

### 4. Portal, cambio de plan y cancelación

```python
return redirect(services.portal_url(tenant, return_url=request.build_absolute_uri("/cuenta")))  # tarjeta, facturas, datos

services.change_plan(tenant, "price_DeF…")       # aplica ya y se cobra desde el próximo periodo

quote = services.preview_plan_change(tenant, "price_DeF…")   # lo que cuesta AHORA, prorrateado
services.change_plan(tenant, "price_DeF…", proration_behavior="create_prorations", expected_amount=quote["amount"])
# subir se cobra ya y aplica cuando Wompi lo aprueba (llega por webhook); bajar espera la renovación
services.cancel(tenant)                          # al fin del periodo; cancel(tenant, at_period_end=False) = ya
services.reactivate(tenant)                      # deshace la cancelación programada
services.sync(tenant)                            # vuelve a leer de Bucli
```

Con varios ítems (plan base + puestos), `change_plan` cambia el ítem cuyo precio está en `BUCLI_PRICE_PLANS`. Los puestos se manejan con `client.subscription_items` (`bucli.contrib.django.conf.get_client()` da el cliente configurado).

### 5. La regla de acceso

```python
from bucli.contrib.django import access

access.has_access(tenant)          # True con FULL o WARNING
access.access_level(tenant)        # "full" | "warning" | "free" | "read_only" | "none"
access.current_plan(tenant)        # "pro" | "free" | None → con esto aplicas TUS límites
access.needs_payment_update(tenant)  # mostrar «actualiza tu tarjeta»
```

| Estado en Bucli | `access_level` | Acceso |
|---|---|---|
| `trialing`, `active` | `full` | Completo |
| `past_due` | `warning` | Completo mientras Bucli reintenta, con el aviso de «actualiza tu tarjeta». Si definiste `BUCLI_GRACE` y ya pasó ese plazo desde el fin del periodo, cae al acceso de suscripción vencida |
| `paused`, `canceled`, `incomplete_expired`, sin suscripción | `free` o `read_only` | Plan gratis o solo lectura, según `BUCLI_LAPSED_ACCESS` |
| `incomplete` | `none` | Nada nuevo: todavía no pagó. Se queda con el plan gratis si lo hay, nunca con el plan que compró |

Solo lee la copia local. Los límites de cada plan (usuarios, mensajes, documentos) siguen en el SaaS: Bucli solo dice qué plan tiene la cuenta y si está al día.

> Mientras migras, usa la regla solo para las cuentas que **cobra Bucli**. Una cuenta que todavía cobra el sistema viejo no tiene `BillingAccount` y la regla la vería como «sin suscripción».

### 6. Señales

```python
from django.dispatch import receiver
from bucli.contrib.django.signals import subscription_changed, event_processed

@receiver(subscription_changed)
def on_change(sender, account, billing_account, previous_status, status, previous_plan, plan, **kwargs):
    ...  # recalcular límites, avisar al equipo

@receiver(event_processed)
def on_event(sender, event, account, billing_account, invoice, outcome, **kwargs):
    if event["type"] == "invoice.fiscal_document.issued":
        ...  # invoice["fiscal_document"]["pdf_url"]
    elif event["type"] == "notice.sent":
        ...  # a la campana de account: event["data"]["object"]["subject"], ["message"]
    elif event["type"] == "subscription.update_failed":
        ...  # el cambio prorrateado no se cobró: la suscripción quedó como estaba
    elif event["type"] == "charge.refunded":
        ...  # event["data"]["object"] es el refund; invoice, su factura
```

Las dos señales se envían **después del commit** con `send_robust`: si un receptor falla, se registra en el log y el webhook no se rompe.

### 7. El webhook en Bucli

Crea el endpoint desde el dashboard («Desarrolladores → Webhooks») o con la API. Necesita un plan con webhooks (Pro o superior).

```python
endpoint = client.webhook_endpoints.create(
    url="https://api.tu-saas.co/webhooks/bucli/",
    enabled_events=[
        "checkout.session.completed",
        "subscription.created", "subscription.updated", "subscription.canceled",
        "subscription.past_due", "subscription.update_failed", "subscription.trial_will_end",
        "invoice.paid", "invoice.payment_failed", "charge.refunded", "notice.sent",
        "invoice.fiscal_document.issued", "invoice.fiscal_document.rejected",
    ],
    description="Acceso del SaaS",
)
endpoint.secret   # whsec_… — se muestra UNA vez: va a BUCLI_WEBHOOK_SECRET
```

`charge.refunded` hace que la app lea el refund y su factura: la llave necesita poder leer devoluciones y facturas (las llaves secretas completas pueden).

Ojo: Bucli no usa los nombres de Stripe. Sus eventos son **`subscription.*`**, no `customer.subscription.*`. La lista que la app procesa está en `bucli.contrib.django.webhooks.HANDLED_EVENTS`.

Qué hace la vista con cada entrega:
1. **Verifica la firma** sobre el cuerpo crudo. Si no pasa, responde `400`.
2. **Deduplica** por `event.id`. Una entrega repetida responde `200 duplicate`.
3. **No confía en el orden ni en el contenido del evento.** Vuelve a pedir el objeto (`GET /v1/subscriptions/{id}`; para `invoice.*` y `checkout.session.completed`, antes la factura o la sesión) y guarda **ese** estado. Una copia con `updated` más viejo que la guardada nunca la sobrescribe (`stale`), venga de un evento o de `sync`; cuando `updated` no lo decide (el mismo segundo, otra suscripción), un evento con `sequence` menor que el último aplicado tampoco.
4. **Una sola transacción** registra el evento y actualiza `BillingAccount`. Corre fuera de `ATOMIC_REQUESTS`.
5. Responde `200` rápido. Si Bucli no contesta al pedir el objeto, responde `503` sin registrar nada, y la reentrega de Bucli lo procesa después.
6. Ignora los eventos del otro modo, por ejemplo uno de test que llegue a producción (`wrong_mode`).

Para encontrar la cuenta busca, en este orden:
1. la suscripción ya vinculada;
2. el `client_reference_id` del checkout;
3. `metadata.bucli_account` de la suscripción;
4. el cliente, solo si una sola cuenta lo tiene. Bucli guarda un cliente por correo, así que dos empresas de la misma persona comparten cliente.

**Rotar el secreto:** `client.webhook_endpoints.roll_secret("we_…")` y despliega el nuevo antes de 24 horas. Mientras tanto, cada entrega trae dos `v1` y la verificación acepta cualquiera. Si prefieres desplegar primero, pon temporalmente `BUCLI_WEBHOOK_SECRET = ["whsec_viejo", "whsec_nuevo"]`.

### 8. Resincronizar

```bash
python manage.py bucli_sync 42 43      # cuentas por pk
python manage.py bucli_sync --all      # todas las que tienen cliente en Bucli
python manage.py bucli_sync --discover # recorre las suscripciones de Bucli y vincula las que traen metadata.bucli_account
```

Conviene correr `bucli_sync --all` una vez al día como red de seguridad.

### 9. Pruebas en el SaaS

Para probar el webhook firma el cuerpo con `bucli.Webhook.generate_test_header(body, settings.BUCLI_WEBHOOK_SECRET)` y simula la API con `responses`. Así lo hacen las pruebas de esta librería (`tests/django_tests/`).

---

## Derechos y medidores

Bucli calcula los derechos de cada cliente (módulos, límites y `access`) y cuenta los cupos medidos. La copia local de
tu SaaS se actualiza con `entitlements.updated` y, de respaldo, con `client.customers.entitlements("cus_…")`.

Para gastar una unidad justo antes del acto que no se deshace, usa el guardián:

```python
from bucli.contrib.django.metering import guard

reservation = guard().reserve("documents", customer=account.bucli_customer_id, idempotency_key=f"doc-{document.pk}")
if not reservation.allowed:
    raise QuotaExhausted(reservation.refusal)       # "quota_exhausted" o "entitlement_access_denied"
...
if dian_rejected:
    guard().release(reservation.usage_id)            # vuelve a su origen (si no fue offline)
```

- `BUCLI_METER_ON_UNAVAILABLE = {"documents": "allow"}`: si Bucli no responde en 2 s (`BUCLI_METER_TIMEOUT`), la unidad
  pasa, queda en `PendingUsage` y `manage.py bucli_flush_usage` (cada minuto) la reporta con su fecha. Bucli nunca la
  niega: lo que pase del cupo queda como exceso en su tablero.
- Cada respuesta trae `remaining`; con el saldo en cero el guardián niega sin llamar durante `BUCLI_METER_CACHE_SECONDS`.
  Llama `guard().forget(customer_id)` al recibir `grant.created` o `entitlements.updated`.

## Migrar los clientes existentes

Si tu SaaS ya cobra con Wompi, puedes pasar a Bucli por fases, sin pedirles la tarjeta otra vez a tus clientes.

**Fase 0. Un solo webhook de Wompi, sin tocar el código.**
1. En Bucli crea tu organización y conecta las mismas llaves de Wompi.
2. En «Cobros en línea → Reenvío de eventos» crea una regla por **prefijo de referencia** que lleve los eventos de tus cobros actuales a tu webhook de hoy (más una ruta por defecto).
3. Cambia la URL de eventos en el panel de Wompi por la de Bucli.

Tu SaaS sigue recibiendo los eventos de sus cobros viejos con el cuerpo y el `X-Event-Checksum` originales.

**Fase 1. Los clientes nuevos entran por Bucli.** Integra esta librería (pasos 1 a 8). Los clientes antiguos siguen con el cobro viejo.

**Fase 2. Los clientes antiguos pasan a Bucli.** Bucli puede importar sus suscripciones con la fuente de pago que ya tienen en Wompi: nacen `active`, con la próxima fecha de cobro de tu sistema como `billing_cycle_anchor`, y no cobran en el acto. Escríbele al soporte de Bucli para hacerlo.

Para que tu SaaS reconozca a cada cliente importado, pon en la `metadata` de cada suscripción el `bucli_account` de su cuenta (`{"bucli_account": "tenants.tenant:42"}`) y corre `python manage.py bucli_sync --discover`. También el primer webhook de esa suscripción la vincula. Sin esa metadata, vincúlala con sus ids: `services.link(tenant, customer_id="cus_…", subscription_id="sub_…")`.

**El corte, cliente por cliente y en este orden:**
1. la suscripción ya está en Bucli;
2. marca «cobra Bucli» en tu SaaS. Desde ahí la regla de acceso de esa cuenta sale de `BillingAccount`;
3. apaga ese cliente en el cobro viejo.

Nunca deben cobrar los dos sistemas al mismo cliente. Si el sistema viejo cobra antes del ancla, Bucli cobra igual en el ancla: alinea las fechas antes de pasar.

---

## Desarrollo

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest          # sin red: responses simula la API y un guard corta cualquier socket
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

- `tests/test_client.py`, `test_resources.py` y `test_objects_and_pagination.py` prueban el cliente: cabeceras, idempotencia, reintentos, errores, cada recurso y la paginación.
- `tests/test_webhook.py` prueba la firma: válida, doble `v1`, vieja, del futuro, alterada y cuerpos que no son JSON.
- `tests/django_tests/` corre un proyecto mínimo en sqlite. Prueba la vista, la deduplicación, el orden invertido, los servicios, `sync`, el comando, `has_access`, que las migraciones coincidan con los modelos y los checks de Django.
- `tests/test_contract.py` es la prueba de contrato contra el registro de documentación de la API de Bucli (un repositorio privado). Lo lee con `ast`, sin Django, y falla si la librería llama un método y una ruta que la API no documenta, o si la API documenta una que la librería no tiene. También falla si cambian la versión, los tipos de evento, la firma, los tipos de error o los estados. Busca ese repositorio en `$BUCLI_API_DIR` o en `../bucli-api`; si no está, se salta.

## Publicar una versión

Se publica en PyPI sola al subir su etiqueta `vX.Y.Z` (`.github/workflows/publish.yml`); los detalles están en los comentarios de los workflows.

## Licencia

MIT. © 2026 Bithaus SAS. Ver [`LICENSE`](https://github.com/bithaus-sas/bucli-python/blob/main/LICENSE).
