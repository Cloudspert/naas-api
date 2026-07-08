"""Functional tests for the EgressIP endpoints (full HTTP stack)."""

from tests.conftest import AUTH

BASE = "/api/v1/egressips"

_UNSET = object()


def _create(test_client, name="payments-web", ips=None, ns_labels=_UNSET, **extra):
    body = {
        "name": name,
        "egress_ips": ips or ["192.168.1.100"],
        "namespace_labels": {"ecosystem": "payments"} if ns_labels is _UNSET else ns_labels,
        **extra,
    }
    return test_client.post(BASE, json=body, auth=AUTH)


def test_create_egressip(client):
    test_client, fake = client
    resp = _create(
        test_client,
        ips=["192.168.1.100", "192.168.1.101"],
        ns_labels={"ecosystem": "payments", "tier": "frontend"},
        pod_labels={"app": "web"},
        labels={"team": "payments"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "payments-web"
    assert body["namespace_selector"] == {"ecosystem": "payments", "tier": "frontend"}
    assert body["pod_selector"] == {"app": "web"}
    assert body["labels"]["managed-by"] == "naas-api"
    assert body["labels"]["ecosystem"] == "payments"
    assert ("k8s.ovn.org/v1", "EgressIP", "payments-web") in fake.dynamic.objects


def test_create_requires_auth(client):
    test_client, _ = client
    resp = test_client.post(BASE, json={"name": "x", "egress_ips": ["10.0.0.1"],
                                        "namespace_labels": {"a": "b"}})
    assert resp.status_code == 401


def test_create_invalid_ip_422(client):
    test_client, _ = client
    resp = _create(test_client, ips=["not-an-ip"])
    assert resp.status_code == 422


def test_create_empty_namespace_labels_422(client):
    test_client, _ = client
    resp = _create(test_client, ns_labels={})
    assert resp.status_code == 422


def test_create_duplicate_409(client):
    test_client, _ = client
    assert _create(test_client).status_code == 201
    assert _create(test_client).status_code == 409


def test_list_filter_by_labels(client):
    test_client, _ = client
    _create(test_client, name="a", ns_labels={"ecosystem": "payments"})
    _create(test_client, name="b", ns_labels={"ecosystem": "orders"})

    resp = test_client.get(f"{BASE}?labels=ecosystem=payments", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["items"][0]["name"] == "a"

    # No filter returns all managed EgressIPs.
    assert test_client.get(BASE, auth=AUTH).json()["count"] == 2


def test_list_invalid_label_filter_422(client):
    test_client, _ = client
    resp = test_client.get(f"{BASE}?labels=notakeyvalue", auth=AUTH)
    assert resp.status_code == 422


def test_get_by_name(client):
    test_client, _ = client
    _create(test_client, name="eg1")
    resp = test_client.get(f"{BASE}/eg1", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["name"] == "eg1"


def test_get_missing_404(client):
    test_client, _ = client
    resp = test_client.get(f"{BASE}/ghost", auth=AUTH)
    assert resp.status_code == 404


def test_delete_egressip(client):
    test_client, fake = client
    _create(test_client, name="eg1")
    resp = test_client.delete(f"{BASE}/eg1", auth=AUTH)
    assert resp.status_code == 200
    assert "egressip deleted" in resp.json()["message"]
    assert ("k8s.ovn.org/v1", "EgressIP", "eg1") not in fake.dynamic.objects


def test_delete_missing_404(client):
    test_client, _ = client
    resp = test_client.delete(f"{BASE}/ghost", auth=AUTH)
    assert resp.status_code == 404
