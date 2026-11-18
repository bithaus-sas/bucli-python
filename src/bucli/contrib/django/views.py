"""The webhook the SaaS exposes to Bucli: ``path("webhooks/bucli/", include("bucli.contrib.django.urls"))``."""

from __future__ import annotations

import logging

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from bucli.constants import SIGNATURE_HEADER
from bucli.contrib.django import conf
from bucli.contrib.django.webhooks import handle_event
from bucli.errors import BucliError, SignatureVerificationError
from bucli.webhook import Webhook

logger = logging.getLogger("bucli.contrib.django")


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(transaction.non_atomic_requests, name="dispatch")
class BucliWebhookView(View):
    """Verifies ``Bucli-Signature`` on the RAW body, deduplicates and stores.

    * 400: bad signature, too old, not JSON (Bucli will not fix it by retrying).
    * 503: Bucli could not be asked for the fresh object — nothing recorded, the
      redelivery will do it.
    * 200 otherwise (also for duplicates and events it ignores)."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args, **kwargs) -> JsonResponse:
        header = request.headers.get(SIGNATURE_HEADER, "")
        try:
            event = Webhook.construct_event(
                request.body, header, conf.webhook_secrets(), tolerance=conf.webhook_tolerance()
            )
        except SignatureVerificationError as error:
            logger.warning("Bucli webhook refused: %s", error.message)
            return JsonResponse({"error": "invalid_signature"}, status=400)
        if not event.get("id") or not event.get("type"):
            return JsonResponse({"error": "invalid_event"}, status=400)
        try:
            outcome = handle_event(event)
        except BucliError as error:
            logger.warning("Bucli webhook %s deferred: %r", event.get("id"), error)
            return JsonResponse({"error": "bucli_unavailable"}, status=503)
        return JsonResponse({"received": True, "outcome": outcome})
