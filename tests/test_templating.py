"""Tests for the Jinja2 manifest rendering."""

import pytest

from app.k8s.render import render_manifest


def test_namespace_template():
    manifest = render_manifest(
        "namespace.yaml.j2",
        name="team-a",
        managed_label_key="managed-by",
        managed_label_value="naas-api",
        extra_labels=None,
    )
    assert manifest["kind"] == "Namespace"
    assert manifest["metadata"]["name"] == "team-a"
    assert manifest["metadata"]["labels"]["managed-by"] == "naas-api"


def test_quota_only_memory_renders_only_memory_keys():
    manifest = render_manifest(
        "resourcequota.yaml.j2",
        quota_name="naas-api-quota",
        namespace="team-a",
        managed_label_key="managed-by",
        managed_label_value="naas-api",
        extra_labels=None,
        memory="16Gi",
        cpu=None,
        storage=None,
    )
    assert manifest["spec"]["hard"] == {"requests.memory": "16Gi", "limits.memory": "16Gi"}
    assert manifest["metadata"]["labels"] == {"managed-by": "naas-api"}


def test_quota_renders_inherited_labels():
    manifest = render_manifest(
        "resourcequota.yaml.j2",
        quota_name="naas-api-quota",
        namespace="team-a",
        managed_label_key="managed-by",
        managed_label_value="naas-api",
        extra_labels={"company.example.io/env": "prod"},
        memory="16Gi",
        cpu=None,
        storage=None,
    )
    assert manifest["metadata"]["labels"] == {
        "managed-by": "naas-api",
        "company.example.io/env": "prod",
    }


def test_quota_all_dimensions_are_strings():
    manifest = render_manifest(
        "resourcequota.yaml.j2",
        quota_name="naas-api-quota",
        namespace="team-a",
        managed_label_key="managed-by",
        managed_label_value="naas-api",
        extra_labels=None,
        memory="8Gi",
        cpu="4",
        storage="50Gi",
    )
    hard = manifest["spec"]["hard"]
    # CPU must stay a string, not be parsed as a YAML int.
    assert hard["requests.cpu"] == "4"
    assert isinstance(hard["requests.cpu"], str)
    assert hard["requests.storage"] == "50Gi"


def test_missing_variable_raises():
    with pytest.raises(Exception):
        render_manifest("namespace.yaml.j2", name="team-a")
