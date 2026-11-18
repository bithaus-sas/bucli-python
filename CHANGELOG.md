# Changelog

Todos los cambios de esta librería. Formato [Keep a Changelog](https://keepachangelog.com/); versiones [SemVer](https://semver.org/).

## [Unreleased]

## [0.6.0] - 2026-11-17

### Added
- La librería se publica en PyPI: `pip install "bucli[django]"`. Cada etiqueta `vX.Y.Z` la publica con *trusted publishing* (`.github/workflows/publish.yml`, sin token guardado) y crea la release en GitHub; `ci.yml` corre ruff, las pruebas en Python 3.11–3.13 con Django 4.2, 5.2 y la última, y revisa el paquete con `twine check`.
- Workflow `saas-bump.yml`: cuando una versión queda publicada en PyPI, abre un PR en cada SaaS que usa la librería que mueve su pin de `requirements/base.txt` a `bucli[django]==X.Y.Z` (un pin desde git pasa a PyPI) y trae las notas de la versión. Necesita el secreto `SAAS_REPOS_TOKEN`.

### Changed
- Licencia MIT (era propietaria). La versión sale de `src/bucli/_version.py`.
- El README es público: instalación desde PyPI, sin enlaces a documentos internos.

## [0.5.0] - 2026-11-12

### Added
- Eventos `product.created` y `product.updated` en `EVENT_TYPES` (`product.updated` trae `previous_attributes`): un SaaS que copia su catálogo se entera de los cambios hechos en el panel de Bucli.
- Constantes de la vitrina: `PLACEMENT_MAIN` / `PLACEMENT_SECONDARY` (`storefront.placement` de un producto: tarjeta entre los planes principales o línea bajo ellos) y `PRODUCT_KIND_PLAN` / `_ADDON` / `_ONE_TIME` / `_TRIAL` (`kind`, que Bucli lee de las reglas y los precios del producto). `products.create/update` ya aceptan `placement`; `products.list` filtra por `placement` y `kind`.

## [0.4.0] - 2026-11-11

### Added
- `customers.create/update` documentan `fiscal`: los datos del cliente para la factura electrónica.
- Evento `invoice.updated` en `EVENT_TYPES` (cambió el valor de un cobro abierto: un cupón aplicado mientras estaba pendiente).
- Django: `services.sync_customer(account, email=…, name=…, phone=…, fiscal=…)` envía los datos actuales de la cuenta a su cliente de Bucli (solo lo que no sea `None`; sin cliente vinculado no hace nada).

### Changed
- Django: `ensure_customer` y `start_checkout` aceptan `external_id`, `phone` y `fiscal`. Con `external_id` el cliente se busca por él y nunca por el correo (dos empresas del mismo dueño ya no se mezclan).

## [0.3.0] - 2026-11-09

### Added
- Recursos de F3–F8 de la API: `subscriptions.preview` (prorrateo), `pause`, `resume`, `extend_period`, `extend_trial`, `end_trial`, `apply_discount`, `delete_discount`, `list_actions`; `payment_methods.set_backup`; `invoices.mark_paid`; `payments.refund` y `void`; `refunds`, `coupons` y `fixed_term_offers`.
- `error.quote`: la cotización nueva de un `409 quote_changed`.
- Eventos `subscription.update_failed`, `notice.sent`, `charge.refunded`, `payment_method.suspended` y `payment_method.expiring` en `EVENT_TYPES`.
- Django: `services.preview_plan_change` y `change_plan(..., proration_behavior="create_prorations", expected_amount=…)`; el webhook procesa `subscription.update_failed`, `charge.refunded` (resuelve la cuenta por la factura y la suscripción del refund) y `notice.sent` (por su cliente o suscripción), y `event_processed` los entrega con su cuenta.

### Changed
- `subscription_items.delete` acepta `expected_amount`; los docstrings de `subscriptions.create/update` y de los ítems describen `trial_key`, `coupon`, `overage_caps` y el prorrateo.
- El docstring de `subscriptions.update` describe `items` (cambio de ciclo de un plan con adicionales, solo prorrateado) y el `202` con el `subscription_change` mientras el cambio espera su cobro.

## [0.2.1] - 2026-11-07

### Fixed
- La prueba de las cabeceras lee la versión del paquete (fallaba en v0.2.0 con la versión fija `0.1.0`).

### Changed
- `customers.create`/`list` documentan `external_id` (un cliente por empresa).

## [0.2.0] - 2026-11-07

### Added
- Recursos de derechos y medidores: `features`, `meters` (`summary`, `reserve`), `usage` (`report`, `release`), `grant_offers` (`purchase`), `grants` (`withdraw`), `customers.entitlements`, `subscriptions.entitlements` y `subscriptions.set_entitlement_overrides`.
- `bucli.metering.UsageGuard`: el modo `on_unavailable: allow` — si Bucli no responde en 2 s deja pasar la unidad, la encola con su `idempotency_key` y `flush()` la reporta después; guarda el saldo de cada respuesta y niega sin llamar cuando está en cero.
- Django: `bucli.contrib.django.metering.guard()` con la cola en la base (`PendingUsage`, migración 0003) y los saldos en la caché; `manage.py bucli_flush_usage`.
- `request()` acepta `timeout` y `max_retries` por llamada.

## [0.1.0] - 2026-11-02

### Added
- `bucli.Bucli`: cliente de la API `/v1` (versión `2026-11-01`). El modo lo decide la llave (`sk_test_`/`sk_live_`) y una llave `pk_` se rechaza.
- `Idempotency-Key` automático en cada POST, reutilizado en los reintentos.
- Reintentos con backoff y jitter solo ante errores de red, `409 idempotency_key_in_use`, `429` (con `Retry-After`) y `5xx`.
- Recursos, cada uno con solo los endpoints que documenta la API:
  - `account`, `customers`, `products`, `prices` (`recurring` y `one_time`);
  - `subscriptions` (`items`, `billing_cycle_anchor`, `trial_end`, `cancel`) y `subscription_items`;
  - `payment_methods`, con la importación de `wompi_payment_source_id`;
  - `invoices` (factura única, `pay`, `void`, `lines`) y `payments`;
  - `events` (`resend`) y `webhook_endpoints` (`roll_secret`);
  - `checkout_sessions`, `payment_links` y `portal_sessions`.
- Recuperación automática de `409 url_unavailable_on_replay` en el checkout y en el portal.
- Objetos que son `dict` y además se leen por atributo (`BucliObject`, `ListObject`), con `last_response`.
- Paginación: `list()` devuelve una página y `auto_paging_iter()` recorre todas, hacia adelante o hacia atrás, también en listas incrustadas. `expand[]` en todas las lecturas.
- Errores tipados: `AuthenticationError`, `PermissionError`, `InvalidRequestError`, `IdempotencyError`, `RateLimitError`, `BillingError`/`CardError`, `APIError` y `APIConnectionError`, con `type`, `code`, `message`, `param`, `request_id` y `http_status`.
- `bucli.Webhook.construct_event`:
  - verifica la firma HMAC-SHA256 en tiempo constante;
  - acepta cualquiera de las firmas `v1` durante una rotación;
  - rechaza un timestamp viejo o del futuro y un cuerpo que no sea JSON;
  - `generate_test_header` arma una cabecera válida para pruebas.
- `bucli.contrib.django` (app `bucli_billing`, extra `[django]`, Django ≥ 4.2):
  - modelos `BillingAccount` y `ProcessedEvent`, con migración;
  - la vista del webhook: verifica, deduplica, vuelve a pedir el objeto, se protege de los eventos fuera de orden con `sequence` y escribe en una transacción;
  - las señales `subscription_changed` y `event_processed`;
  - los servicios `start_checkout`, `portal_url`, `change_plan`, `cancel`, `reactivate`, `sync`, `link` y `discover`;
  - la regla de acceso: `has_access`, `access_level` y `current_plan`;
  - el comando `bucli_sync [pks] [--all] [--discover]`.
- Pruebas sin red (`responses`), entre ellas una prueba de contrato contra el registro de documentación de la API de Bucli.
- `checkout_sessions.create` y `payment_links.create` documentan y envían `line_items=[{price, quantity}]` (plan base + puestos + adicionales), `quantity` y, en el checkout, `subscription_data={metadata, trial_days}`.
- `services.start_checkout(account, items=[...])` en la integración de Django: `"price_…"`, `("price_…", 3)` o `{"price", "quantity"}`, con al menos un precio de `BUCLI_PRICE_PLANS`; `quantity` y `subscription_metadata`. La suscripción creada lleva `metadata.bucli_account`.
- `portal_sessions.retrieve(id)` (`GET /v1/portal_sessions/{id}`): la sesión sin su `url`.
- `BucliError.object_id` y `object_type`: el objeto que nombra un `409 url_unavailable_on_replay` o `webhook_secret_unavailable_on_replay`.
- `BillingAccount.bucli_updated_at` (migración `0002`): el `updated` de Bucli de la copia local.
- La recuperación de `409 url_unavailable_on_replay` del checkout usa `error.object_id` en vez de buscar el id en el mensaje (sin `object_id`, el error se propaga).
- `store_subscription` ordena las copias de una suscripción por su `updated`: una más vieja que la guardada es `stale` (también en `sync`) y una más nueva se aplica aunque su evento tenga un `sequence` menor; `sequence` decide cuando `updated` no puede. `last_event_sequence` ya no retrocede.
