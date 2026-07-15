"""Functional tests exercising the full HTTP stack via TestClient.

The app runs its real lifespan (cache started, auth wired) but against the
in-memory fake cluster injected by the ``client`` fixture.
"""

from __future__ import annotations

from tests.conftest import AUTH
from tests.fakes import _Namespace

BASE = "/api/v1/namespaces"


# ---------------------------------------------------------------- health / docs
def test_healthz_is_public(client):
    test_client, _ = client
    resp = test_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_openapi_advertises_active_auth_module(client):
    test_client, _ = client
    schema = test_client.get("/openapi.json").json()
    assert "basicAuth" in schema["components"]["securitySchemes"]
    assert schema["paths"][BASE]["post"]["security"] == [{"basicAuth": []}]
    # Public endpoints are not marked secured.
    assert "security" not in schema["paths"]["/healthz"]["get"]


# ------------------------------------------------------------------------- auth
def test_protected_endpoint_requires_auth(client):
    test_client, _ = client
    resp = test_client.get(BASE)
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_wrong_credentials_rejected(client):
    test_client, _ = client
    resp = test_client.get(BASE, auth=("admin", "wrong"))
    assert resp.status_code == 401


# ----------------------------------------------------------------------- create
def test_create_namespace(client):
    test_client, fake = client
    resp = test_client.post(
        BASE,
        json={"name": "team-a", "limits": {"memory": "8Gi", "cpu": "4", "storage": "50Gi"}},
        auth=AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["namespace"] == "team-a"
    assert "team-a" in fake.core_v1.namespaces
    hard = body["details"]["quota"]
    assert hard["requests.memory"] == "8Gi"


def test_create_bare_numbers_get_gi_suffix(client):
    test_client, fake = client
    resp = test_client.post(
        BASE,
        json={"name": "team-a", "limits": {"memory": "8", "cpu": "4", "storage": "50"}},
        auth=AUTH,
    )
    assert resp.status_code == 201
    hard = resp.json()["details"]["quota"]
    assert hard["requests.memory"] == "8Gi"
    assert hard["requests.storage"] == "50Gi"
    assert hard["requests.cpu"] == "4"  # cpu untouched


def test_create_with_labels(client):
    test_client, fake = client
    resp = test_client.post(
        BASE,
        json={
            "name": "team-a",
            "limits": {"memory": "8Gi"},
            "labels": {"env": "prod", "team": "payments"},
        },
        auth=AUTH,
    )
    assert resp.status_code == 201
    assert resp.json()["details"]["labels"] == {"env": "prod", "team": "payments"}
    ns_labels = fake.core_v1.namespaces["team-a"].metadata.labels
    assert ns_labels["env"] == "prod"
    # Inherited by the quota too.
    quota_labels = fake.core_v1.quotas[("team-a", "naas-api-quota")]["metadata"]["labels"]
    assert quota_labels["env"] == "prod"


def test_create_with_annotations_for_contact(client):
    test_client, fake = client
    resp = test_client.post(
        BASE,
        json={
            "name": "team-a",
            "annotations": {"contact-email": "dl-payments@example.com", "owner": "payments"},
        },
        auth=AUTH,
    )
    assert resp.status_code == 201
    assert resp.json()["details"]["annotations"]["contact-email"] == "dl-payments@example.com"
    ns = fake.core_v1.namespaces["team-a"]
    assert ns.metadata.annotations["contact-email"] == "dl-payments@example.com"


def test_list_returns_annotations(client):
    test_client, fake = client
    fake.core_v1.namespaces["team-a"] = _Namespace(
        "team-a",
        labels={"managed-by": "naas-api"},
        annotations={"contact-email": "dl@example.com"},
    )
    test_client.app.state.cache.refresh()

    body = test_client.get(BASE, auth=AUTH).json()
    assert body["items"][0]["annotations"]["contact-email"] == "dl@example.com"


def test_create_duplicate_returns_409(client):
    test_client, fake = client
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    resp = test_client.post(BASE, json={"name": "team-a"}, auth=AUTH)
    assert resp.status_code == 409


def test_create_invalid_name_returns_422(client):
    test_client, _ = client
    resp = test_client.post(BASE, json={"name": "Bad_Name"}, auth=AUTH)
    assert resp.status_code == 422


# ----------------------------------------------------------------------- status
def test_status_exists_true(client):
    test_client, fake = client
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    resp = test_client.get(f"{BASE}/team-a/status", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"namespace": "team-a", "exists": True}


def test_status_exists_false(client):
    test_client, _ = client
    resp = test_client.get(f"{BASE}/ghost/status", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"namespace": "ghost", "exists": False}


def test_status_requires_auth(client):
    test_client, _ = client
    resp = test_client.get(f"{BASE}/team-a/status")
    assert resp.status_code == 401


# ------------------------------------------------------------------------- list
def test_list_namespaces_served_from_cache(client):
    test_client, fake = client
    fake.core_v1.namespaces = {
        "managed": _Namespace("managed", labels={"managed-by": "naas-api"}),
        "other": _Namespace("other", labels={"team": "x"}),
    }
    # Force a refresh (interval is long in tests).
    test_client.app.state.cache.refresh()

    resp = test_client.get(BASE, auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    names = {i["name"] for i in body["items"]}
    assert names == {"managed"}
    assert body["count"] == 1
    assert body["cached_at"] is not None


# ----------------------------------------------------------------- update quota
def test_update_quota_only_memory(client):
    test_client, fake = client
    test_client.post(
        BASE, json={"name": "team-a", "limits": {"memory": "8Gi", "cpu": "4"}}, auth=AUTH
    )
    resp = test_client.patch(
        f"{BASE}/team-a/quota", json={"limits": {"memory": "16Gi"}}, auth=AUTH
    )
    assert resp.status_code == 200
    hard = fake.core_v1.quotas[("team-a", "naas-api-quota")]["spec"]["hard"]
    assert hard["requests.memory"] == "16Gi"
    assert hard["requests.cpu"] == "4"


def test_update_quota_empty_payload_returns_422(client):
    test_client, _ = client
    resp = test_client.patch(f"{BASE}/team-a/quota", json={"limits": {}}, auth=AUTH)
    assert resp.status_code == 422


def test_update_quota_missing_namespace_404(client):
    test_client, _ = client
    resp = test_client.patch(
        f"{BASE}/ghost/quota", json={"limits": {"memory": "1Gi"}}, auth=AUTH
    )
    assert resp.status_code == 404


# ----------------------------------------------------------------------- delete
def test_soft_delete_marks_annotation(client):
    test_client, fake = client
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    resp = test_client.delete(f"{BASE}/team-a", auth=AUTH)
    assert resp.status_code == 200
    assert "marked for deletion" in resp.json()["message"]
    assert "naas-api/marked-for-deletion-at" in fake.core_v1.namespaces["team-a"].metadata.annotations
    # Still present (soft delete).
    assert "team-a" in fake.core_v1.namespaces


def test_force_delete_removes_namespace_and_resources(client):
    test_client, fake = client
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    fake.dynamic.add_resource("ConfigMap", "v1", ["cm1"])
    resp = test_client.delete(f"{BASE}/team-a?force=true", auth=AUTH)
    assert resp.status_code == 200
    assert "force-deleted" in resp.json()["message"]
    assert "team-a" not in fake.core_v1.namespaces
    assert ("ConfigMap", "cm1") in fake.dynamic.deleted


def test_delete_missing_namespace_404(client):
    test_client, _ = client
    resp = test_client.delete(f"{BASE}/ghost", auth=AUTH)
    assert resp.status_code == 404
