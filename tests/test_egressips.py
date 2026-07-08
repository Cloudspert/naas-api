"""Unit tests for EgressIPManager and the EgressIP request schema."""

import pytest
from pydantic import ValidationError

from app.services.egressips import EgressIPError, EgressIPManager
from app.models import CreateEgressIPRequest
from tests.fakes import FakeK8sClient

API_VERSION = "k8s.ovn.org/v1"
KIND = "EgressIP"


def _manager(fake):
    return EgressIPManager(fake, API_VERSION, KIND, "managed-by", "naas-api")


# --- manager ----------------------------------------------------------------
def test_create_renders_selectors_and_mirrors_metadata_labels():
    fake = FakeK8sClient()
    result = _manager(fake).create(
        "payments-web",
        ["192.168.1.100", "192.168.1.101"],
        {"ecosystem": "payments", "tier": "frontend"},
        pod_labels={"app": "web"},
        labels={"team": "payments"},
    )
    assert result["egress_ips"] == ["192.168.1.100", "192.168.1.101"]
    assert result["namespace_selector"] == {"ecosystem": "payments", "tier": "frontend"}
    assert result["pod_selector"] == {"app": "web"}
    # metadata labels = managed-by + mirrored namespace labels + extra labels.
    assert result["labels"] == {
        "managed-by": "naas-api",
        "ecosystem": "payments",
        "tier": "frontend",
        "team": "payments",
    }
    stored = fake.dynamic.objects[(API_VERSION, KIND, "payments-web")]
    assert stored["spec"]["egressIPs"] == ["192.168.1.100", "192.168.1.101"]


def test_create_without_pod_labels_omits_pod_selector():
    fake = FakeK8sClient()
    result = _manager(fake).create("eg1", ["10.0.0.1"], {"ecosystem": "x"})
    assert result["pod_selector"] == {}
    stored = fake.dynamic.objects[(API_VERSION, KIND, "eg1")]
    assert "podSelector" not in stored["spec"]


def test_create_duplicate_conflicts():
    fake = FakeK8sClient()
    manager = _manager(fake)
    manager.create("eg1", ["10.0.0.1"], {"ecosystem": "x"})
    with pytest.raises(EgressIPError) as exc:
        manager.create("eg1", ["10.0.0.2"], {"ecosystem": "x"})
    assert exc.value.status_code == 409


def test_get_and_missing():
    fake = FakeK8sClient()
    manager = _manager(fake)
    manager.create("eg1", ["10.0.0.1"], {"ecosystem": "x"})
    assert manager.get("eg1")["name"] == "eg1"
    with pytest.raises(EgressIPError) as exc:
        manager.get("ghost")
    assert exc.value.status_code == 404


def test_list_filters_by_labels():
    fake = FakeK8sClient()
    manager = _manager(fake)
    manager.create("a", ["10.0.0.1"], {"ecosystem": "payments"})
    manager.create("b", ["10.0.0.2"], {"ecosystem": "orders"})
    assert {e["name"] for e in manager.list()} == {"a", "b"}
    assert {e["name"] for e in manager.list({"ecosystem": "payments"})} == {"a"}
    assert manager.list({"ecosystem": "none"}) == []


def test_delete_and_missing():
    fake = FakeK8sClient()
    manager = _manager(fake)
    manager.create("eg1", ["10.0.0.1"], {"ecosystem": "x"})
    manager.delete("eg1")
    assert (API_VERSION, KIND, "eg1") not in fake.dynamic.objects
    with pytest.raises(EgressIPError) as exc:
        manager.delete("eg1")
    assert exc.value.status_code == 404


# --- schema validation ------------------------------------------------------
def test_request_valid():
    req = CreateEgressIPRequest(
        name="eg1", egress_ips=["192.168.1.1"], namespace_labels={"ecosystem": "x"}
    )
    assert req.egress_ips == ["192.168.1.1"]


def test_request_rejects_empty_namespace_labels():
    with pytest.raises(ValidationError):
        CreateEgressIPRequest(name="eg1", egress_ips=["10.0.0.1"], namespace_labels={})


@pytest.mark.parametrize("bad_ips", [[], ["not-an-ip"], ["10.0.0.1", "999.1.1.1"]])
def test_request_rejects_invalid_ips(bad_ips):
    with pytest.raises(ValidationError):
        CreateEgressIPRequest(name="eg1", egress_ips=bad_ips, namespace_labels={"a": "b"})


def test_request_rejects_bad_name():
    with pytest.raises(ValidationError):
        CreateEgressIPRequest(name="Bad_Name", egress_ips=["10.0.0.1"], namespace_labels={"a": "b"})


def test_request_accepts_ipv6():
    req = CreateEgressIPRequest(
        name="eg1", egress_ips=["2001:db8::1"], namespace_labels={"a": "b"}
    )
    assert req.egress_ips == ["2001:db8::1"]
