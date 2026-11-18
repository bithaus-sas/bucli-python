"""A minimal SaaS: ``tenants.Tenant`` owns the subscription (sqlite, in memory)."""

SECRET_KEY = "test-only"
DEBUG = False
USE_TZ = True
TIME_ZONE = "America/Bogota"
ALLOWED_HOSTS = ["testserver"]
ROOT_URLCONF = "django_project.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "tenants",
    "bucli.contrib.django",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:", "ATOMIC_REQUESTS": True}}

BUCLI_SECRET_KEY = "sk_test_saas"
BUCLI_WEBHOOK_SECRET = "whsec_saas"
BUCLI_PRICE_PLANS = {"price_basic": "basic", "price_pro": "pro"}
BUCLI_ACCOUNT_MODEL = "tenants.Tenant"
BUCLI_MAX_RETRIES = 0
