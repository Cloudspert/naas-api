"""Shared pytest fixtures.

Environment is configured *before* the application is imported so that the
cached :class:`Settings` singleton picks up test values. The real Kubernetes
client is swapped for :class:`FakeK8sClient` at app-startup time.
"""

from __future__ import annotations

import json
import os

# Must be set before any `app.*` import (Settings is cached).
os.environ.setdefault("APP_BASIC_AUTH_USERS", json.dumps({"admin": "s3cret"}))
os.environ.setdefault("APP_IN_CLUSTER", "false")
os.environ.setdefault("APP_NAMESPACE_LABEL_SELECTOR", "managed-by=naas-api")
os.environ.setdefault("APP_CACHE_REFRESH_INTERVAL_SECONDS", "3600")  # no background churn

import pytest  # noqa: E402

from tests.fakes import FakeK8sClient  # noqa: E402

AUTH = ("admin", "s3cret")


@pytest.fixture
def fake_k8s() -> FakeK8sClient:
    return FakeK8sClient()


@pytest.fixture
def client(fake_k8s, monkeypatch):
    """A TestClient whose app is wired to the in-memory fake cluster.

    Yields ``(client, fake_k8s)``. The app's lifespan runs on context enter,
    rebuilding ``app.state`` against the fake each test.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()

    import app.main as main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        main, "K8sClient", lambda in_cluster=True, kubeconfig_path=None: fake_k8s
    )
    with TestClient(main.app) as test_client:
        yield test_client, fake_k8s
