from django.urls import include, path

urlpatterns = [
    path("webhooks/bucli/", include("bucli.contrib.django.urls")),
]
