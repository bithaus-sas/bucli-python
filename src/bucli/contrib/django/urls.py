"""``path("webhooks/bucli/", include("bucli.contrib.django.urls"))`` → ``POST /webhooks/bucli/``."""

from django.urls import path

from bucli.contrib.django.views import BucliWebhookView

app_name = "bucli_billing"

urlpatterns = [
    path("", BucliWebhookView.as_view(), name="webhook"),
]
