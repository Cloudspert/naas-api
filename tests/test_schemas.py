"""Unit tests for the Pydantic request/response schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    CreateNamespaceRequest,
    QuotaDelta,
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
        UpdateQuotaRequest(limits=QuotaDelta())


def test_update_quota_accepts_single_delta():
    req = UpdateQuotaRequest(limits=QuotaDelta(memory="16"))
    assert req.limits.memory == "16Gi"
    assert req.limits.cpu is None and req.limits.storage is None


def test_quota_delta_allows_negative_memory():
    assert QuotaDelta(memory="-2").memory == "-2Gi"
    assert QuotaDelta(storage="-5").storage == "-5Gi"


def test_quota_delta_cpu_and_pvc_are_signed_ints():
    delta = QuotaDelta(cpu=-1, pvc_count=5)
    assert delta.cpu == -1
    assert delta.pvc_count == 5


# --- bare-number -> Gi normalization (memory & storage only) ----------------
def test_bare_number_memory_and_storage_get_gi_suffix():
    limits = ResourceLimits(memory="8", storage="50")
    assert limits.memory == "8Gi"
    assert limits.storage == "50Gi"


def test_bare_int_input_is_accepted_and_suffixed():
    limits = ResourceLimits(memory=8, storage=50)
    assert limits.memory == "8Gi"
    assert limits.storage == "50Gi"


def test_decimal_is_not_suffixed():
    # Integer-only contract: a decimal is not a bare integer, left untouched.
    assert ResourceLimits(memory="1.5").memory == "1.5"


def test_existing_units_are_preserved():
    limits = ResourceLimits(memory="8Gi", storage="10Gi")
    assert limits.memory == "8Gi"
    assert limits.storage == "10Gi"


def test_cpu_is_never_suffixed():
    assert ResourceLimits(cpu="4").cpu == "4"


def test_pvc_count_is_an_int_and_counts_toward_non_empty():
    limits = ResourceLimits(pvc_count=10)
    assert limits.pvc_count == 10
    assert limits.is_empty() is False


def test_update_quota_bare_number_normalized():
    req = UpdateQuotaRequest(limits=QuotaDelta(storage="20"))
    assert req.limits.storage == "20Gi"
