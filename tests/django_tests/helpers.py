import json

import responses
from django.test import TestCase

from bucli import Webhook

from tenants.models import Tenant

WEBHOOK_URL = "/webhooks/bucli/"
SECRET = "whsec_saas"


class BucliTestCase(TestCase):
    """A tenant, a ``responses`` mock of the API (nothing leaves the machine)."""

    def setUp(self):
        super().setUp()
        self.api = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.api.start()
        self.addCleanup(self.api.stop)
        self.addCleanup(self.api.reset)
        self.tenant = Tenant.objects.create(name="Tienda Ana")

    def api_calls(self, method=None, path=None):
        return [
            call
            for call in self.api.calls
            if (method is None or call.request.method == method)
            and (path is None or call.request.path_url.split("?")[0] == "/v1" + path)
        ]

    def post_event(self, event, *, secret=SECRET, timestamp=None, header=None):
        body = json.dumps(event).encode()
        signature = header if header is not None else Webhook.generate_test_header(body, secret, timestamp=timestamp)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                WEBHOOK_URL, data=body, content_type="application/json", HTTP_BUCLI_SIGNATURE=signature
            )
