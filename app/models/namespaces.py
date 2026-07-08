"""Namespace request/response models."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.common import NAME_RE


class ResourceLimits(BaseModel):
    # Kubernetes quantity strings, e.g. "4Gi", "2", "500m", "50Gi". All optional
    # so callers can set only the dimensions they care about.
    memory: Optional[str] = None
    cpu: Optional[str] = None
    storage: Optional[str] = None

    def is_empty(self) -> bool:
        return self.memory is None and self.cpu is None and self.storage is None


class CreateNamespaceRequest(BaseModel):
    name: str
    limits: ResourceLimits = Field(default_factory=ResourceLimits)

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
    limits: ResourceLimits

    @field_validator("limits")
    @classmethod
    def not_empty(cls, value):
        if value.is_empty():
            raise ValueError("at least one of memory, cpu, storage must be provided")
        return value


class NamespaceSummary(BaseModel):
    name: str
    status: Optional[str] = None
    labels: dict = Field(default_factory=dict)
    marked_for_deletion_at: Optional[str] = None


class NamespaceListResponse(BaseModel):
    items: list[NamespaceSummary]
    count: int
    cached_at: Optional[str] = None


class NamespaceStatusResponse(BaseModel):
    namespace: str
    exists: bool
