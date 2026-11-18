"""Django set up once for this folder: the test project in ``tests/django_project``."""

import os
import sys
from pathlib import Path

import django
import pytest

# The test project's apps (``tenants``) live next to its settings.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "django_project"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_project.settings")
django.setup()

from django.test.utils import (  # noqa: E402
    setup_databases,
    setup_test_environment,
    teardown_databases,
    teardown_test_environment,
)


@pytest.fixture(scope="session", autouse=True)
def django_test_database():
    setup_test_environment()
    config = setup_databases(verbosity=0, interactive=False)
    yield
    teardown_databases(config, verbosity=0)
    teardown_test_environment()
