"""Tests for NamespaceManager against the in-memory fake cluster."""

import pytest

from app.services.namespaces import QUOTA_NAME, NamespaceError, NamespaceManager
from app.models import ResourceLimits
from tests.fakes import FakeK8sClient, _Namespace

ANNOTATION = "naas-api/marked-for-deletion-at"


def _manager(fake, key_prefix=None):
    return NamespaceManager(
        k8s=fake,
        managed_label_key="managed-by",
        managed_label_value="naas-api",
        deletion_annotation_key=ANNOTATION,
        key_prefix=key_prefix,
    )


def test_create_namespace_creates_ns_and_quota():
    fake = FakeK8sClient()
    result = _manager(fake).create_namespace(
        "team-a", ResourceLimits(memory="8Gi", cpu="4", storage="50Gi")
    )

    assert "team-a" in fake.core_v1.namespaces
    assert fake.core_v1.namespaces["team-a"].metadata.labels["managed-by"] == "naas-api"
    hard = fake.core_v1.quotas[("team-a", QUOTA_NAME)]["spec"]["hard"]
    assert hard["requests.memory"] == "8Gi"
    assert hard["requests.cpu"] == "4"
    assert hard["requests.storage"] == "50Gi"
    assert result["namespace"] == "team-a"


def test_create_duplicate_namespace_conflicts():
    fake = FakeK8sClient()
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    with pytest.raises(NamespaceError) as exc:
        _manager(fake).create_namespace("team-a", ResourceLimits())
    assert exc.value.status_code == 409


def test_create_without_limits_skips_quota():
    fake = FakeK8sClient()
    _manager(fake).create_namespace("team-a", ResourceLimits())
    assert fake.core_v1.quotas == {}


# --- labels + key prefix ----------------------------------------------------
def test_create_labels_applied_to_namespace_and_inherited_by_quota():
    fake = FakeK8sClient()
    _manager(fake).create_namespace(
        "team-a", ResourceLimits(memory="8Gi"), labels={"env": "prod", "team": "payments"}
    )

    ns_labels = fake.core_v1.namespaces["team-a"].metadata.labels
    assert ns_labels["env"] == "prod"
    assert ns_labels["team"] == "payments"
    assert ns_labels["managed-by"] == "naas-api"  # managed label still there

    quota_labels = fake.core_v1.quotas[("team-a", QUOTA_NAME)]["metadata"]["labels"]
    assert quota_labels["env"] == "prod"  # inherited
    assert quota_labels["team"] == "payments"
    assert quota_labels["managed-by"] == "naas-api"


def test_create_labels_get_configured_key_prefix():
    fake = FakeK8sClient()
    result = _manager(fake, key_prefix="company.example.io").create_namespace(
        "team-a", ResourceLimits(memory="8Gi"), labels={"env": "prod"}
    )

    assert result["labels"] == {"company.example.io/env": "prod"}
    ns_labels = fake.core_v1.namespaces["team-a"].metadata.labels
    assert ns_labels["company.example.io/env"] == "prod"
    assert "env" not in ns_labels
    quota_labels = fake.core_v1.quotas[("team-a", QUOTA_NAME)]["metadata"]["labels"]
    assert quota_labels["company.example.io/env"] == "prod"
    # The managed label is never prefixed (the cache selector depends on it).
    assert ns_labels["managed-by"] == "naas-api"


def test_key_that_already_has_a_prefix_is_not_double_prefixed():
    fake = FakeK8sClient()
    result = _manager(fake, key_prefix="company.example.io").create_namespace(
        "team-a", ResourceLimits(), labels={"other.io/env": "prod", "team": "x"}
    )
    assert result["labels"] == {"other.io/env": "prod", "company.example.io/team": "x"}


def test_no_prefix_configured_keeps_keys_verbatim():
    fake = FakeK8sClient()
    result = _manager(fake).create_namespace("team-a", ResourceLimits(), labels={"env": "prod"})
    assert result["labels"] == {"env": "prod"}


def test_create_without_labels_still_works():
    fake = FakeK8sClient()
    result = _manager(fake).create_namespace("team-a", ResourceLimits(memory="8Gi"))
    assert result["labels"] == {}
    assert fake.core_v1.namespaces["team-a"].metadata.labels == {"managed-by": "naas-api"}


# --- annotations (contact info) ---------------------------------------------
def test_create_annotations_applied_to_namespace_only():
    fake = FakeK8sClient()
    result = _manager(fake).create_namespace(
        "team-a",
        ResourceLimits(memory="8Gi"),
        annotations={"contact-email": "dl-payments@example.com"},
    )

    assert result["annotations"] == {"contact-email": "dl-payments@example.com"}
    ns_annotations = fake.core_v1.namespaces["team-a"].metadata.annotations
    assert ns_annotations["contact-email"] == "dl-payments@example.com"
    # Annotations are namespace-level: the quota does NOT inherit them.
    quota = fake.core_v1.quotas[("team-a", QUOTA_NAME)]["metadata"]
    assert "annotations" not in quota


def test_create_annotations_get_configured_key_prefix():
    fake = FakeK8sClient()
    result = _manager(fake, key_prefix="company.example.io").create_namespace(
        "team-a", ResourceLimits(), annotations={"contact-email": "dl@example.com"}
    )
    assert result["annotations"] == {"company.example.io/contact-email": "dl@example.com"}


def test_annotation_value_may_contain_characters_labels_cannot():
    # The whole point: "@" is illegal in a label value but fine in an annotation.
    fake = FakeK8sClient()
    _manager(fake).create_namespace(
        "team-a", ResourceLimits(), annotations={"contact": "a@b.com, c@d.com"}
    )
    assert (
        fake.core_v1.namespaces["team-a"].metadata.annotations["contact"] == "a@b.com, c@d.com"
    )


def test_reserved_deletion_annotation_is_rejected():
    fake = FakeK8sClient()
    with pytest.raises(NamespaceError) as exc:
        _manager(fake).create_namespace(
            "team-a", ResourceLimits(), annotations={ANNOTATION: "2020-01-01"}
        )
    assert exc.value.status_code == 422
    assert "team-a" not in fake.core_v1.namespaces  # nothing created


def test_create_without_annotations_still_works():
    fake = FakeK8sClient()
    result = _manager(fake).create_namespace("team-a", ResourceLimits())
    assert result["annotations"] == {}
    assert fake.core_v1.namespaces["team-a"].metadata.annotations == {}


def test_namespace_exists_true_and_false():
    fake = FakeK8sClient()
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    manager = _manager(fake)
    assert manager.namespace_exists("team-a") is True
    assert manager.namespace_exists("ghost") is False


def test_update_quota_only_changes_supplied_dimension():
    fake = FakeK8sClient()
    manager = _manager(fake)
    manager.create_namespace("team-a", ResourceLimits(memory="8Gi", cpu="4"))

    manager.update_quota("team-a", ResourceLimits(memory="16Gi"))

    hard = fake.core_v1.quotas[("team-a", QUOTA_NAME)]["spec"]["hard"]
    assert hard["requests.memory"] == "16Gi"  # changed
    assert hard["requests.cpu"] == "4"  # untouched


def test_update_quota_on_missing_namespace_404():
    with pytest.raises(NamespaceError) as exc:
        _manager(FakeK8sClient()).update_quota("ghost", ResourceLimits(memory="1Gi"))
    assert exc.value.status_code == 404


def test_mark_for_deletion_sets_annotation():
    fake = FakeK8sClient()
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")

    result = _manager(fake).mark_for_deletion("team-a")

    annotations = fake.core_v1.namespaces["team-a"].metadata.annotations
    assert ANNOTATION in annotations
    assert result["marked_for_deletion_at"] == annotations[ANNOTATION]
    assert "team-a" in fake.core_v1.namespaces  # soft delete keeps the namespace


def test_force_delete_removes_resources_then_namespace():
    fake = FakeK8sClient()
    fake.core_v1.namespaces["team-a"] = _Namespace("team-a")
    fake.dynamic.add_resource("ConfigMap", "v1", ["cm1", "cm2"])
    fake.dynamic.add_resource("Deployment", "apps/v1", ["web"])
    fake.dynamic.add_resource("Node", "v1", ["node1"], namespaced=False)  # must be skipped

    result = _manager(fake).force_delete("team-a")

    assert "team-a" not in fake.core_v1.namespaces
    assert {kind for kind, _ in fake.dynamic.deleted} == {"ConfigMap", "Deployment"}
    assert "ConfigMap/cm1" in result["deleted_resources"]
    assert len(result["deleted_resources"]) == 3
