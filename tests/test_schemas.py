"""Unit tests for the Pydantic request/response schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    CreateNamespaceRequest,
    ResourceLimits,
    UpdateQuotaRequest,
)


def test_resource_limits_is_empty():
    assert ResourceLimits().is_empty() is True
    assert ResourceLimits(memory="1Gi").is_empty() is False
    assert ResourceLimits(cpu="2").is_empty() is False
    assert ResourceLimits(storage="10Gi").is_empty() is False


def test_create_request_accepts_valid_name_and_subset_of_limits():
    req = CreateNamespaceRequest(name="team-a", limits=ResourceLimits(memory="8Gi"))
    assert req.name == "team-a"
    assert req.limits.memory == "8Gi"
    assert req.limits.cpu is None


def test_create_request_defaults_to_empty_limits():
    req = CreateNamespaceRequest(name="team-a")
    assert req.limits.is_empty()


@pytest.mark.parametrize("bad", ["Bad_Name", "UPPER", "ends-", "-starts", "a" * 64, "white space"])
def test_create_request_rejects_invalid_names(bad):
    with pytest.raises(ValidationError):
        CreateNamespaceRequest(name=bad)


def test_update_quota_rejects_empty_limits():
    with pytest.raises(ValidationError):
        UpdateQuotaRequest(limits=ResourceLimits())


def test_update_quota_accepts_single_dimension():
    req = UpdateQuotaRequest(limits=ResourceLimits(memory="16Gi"))
    assert req.limits.memory == "16Gi"
    assert req.limits.cpu is None and req.limits.storage is None
