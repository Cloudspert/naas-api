"""Namespace request/response models."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.common import NAME_RE

# A bare number, e.g. "8" — no Kubernetes unit suffix. Inputs are integers.
_BARE_NUMBER_RE = re.compile(r"^\d+$")
# A signed bare number for deltas, e.g. "8" or "-2".
_SIGNED_BARE_RE = re.compile(r"^-?\d+$")


class ResourceLimits(BaseModel):
    """Absolute quota values (used on create)."""

    # memory/storage are integer Gi; cpu is integer cores; pvc_count is a count.
    memory: Optional[str] = None
    cpu: Optional[str] = None
    storage: Optional[str] = None
    pvc_count: Optional[int] = None

    @field_validator("memory", "storage", mode="before")
    @classmethod
    def default_gi_suffix(cls, value):
        # A bare number for memory/storage gets a "Gi" suffix (e.g. 8 -> "8Gi").
        # Values that already carry a unit (8Gi, ...) are left untouched.
        # CPU and pvc_count are never touched.
        if value is None:
            return None
        text = str(value).strip()
        if _BARE_NUMBER_RE.match(text):
            return f"{text}Gi"
        return text

    def is_empty(self) -> bool:
        return (
            self.memory is None
            and self.cpu is None
            and self.storage is None
            and self.pvc_count is None
        )


class QuotaDelta(BaseModel):
    """Signed deltas added to the current quota (used on update).

    memory/storage are integer Gi (±), cpu is integer cores (±), pvc_count is a
    count (±). At least one field must be set. A negative result is rejected
    (422) by the service, except cpu which is floored at 1 core.
    """

    memory: Optional[str] = None
    cpu: Optional[int] = None
    storage: Optional[str] = None
    pvc_count: Optional[int] = None

    @field_validator("memory", "storage", mode="before")
    @classmethod
    def default_gi_suffix(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        if _SIGNED_BARE_RE.match(text):  # bare (signed) number -> Gi
            return f"{text}Gi"
        return text

    def is_empty(self) -> bool:
        return (
            self.memory is None
            and self.cpu is None
            and self.storage is None
            and self.pvc_count is None
        )


class CreateNamespaceRequest(BaseModel):
    name: str
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    # Extra labels for the namespace; also inherited by its ResourceQuota.
    # Keys may get a configured prefix (see APP_KEY_PREFIX).
    labels: Optional[dict[str, str]] = None
    # Free-form annotations on the namespace, for values labels cannot hold
    # (emails/DLs contain "@", which is illegal in a label value). Same key
    # prefixing as labels. Namespace-level only.
    annotations: Optional[dict[str, str]] = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if len(value) > 63 or not NAME_RE.match(value):
            raise ValueError(
                "name must be a valid RFC 1123 label (lowercase alphanumeric "
                "and '-', max 63 chars)"
            )
        return value


class UpdateQuotaRequest(BaseModel):
    # limits here are DELTAS added to the current quota, not absolute values.
    limits: QuotaDelta

    @field_validator("limits")
    @classmethod
    def not_empty(cls, value):
        if value.is_empty():
            raise ValueError("at least one of memory, cpu, storage, pvc_count must be provided")
        return value


class NamespaceSummary(BaseModel):
    name: str
    status: Optional[str] = None
    labels: dict = Field(default_factory=dict)
    annotations: dict = Field(default_factory=dict)
    marked_for_deletion_at: Optional[str] = None


class NamespaceListResponse(BaseModel):
    items: list[NamespaceSummary]
    count: int
    cached_at: Optional[str] = None


class NamespaceStatusResponse(BaseModel):
    namespace: str
    exists: bool
