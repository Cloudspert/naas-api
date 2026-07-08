"""EgressIP request/response models."""

import ipaddress
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.common import NAME_RE


class CreateEgressIPRequest(BaseModel):
    name: str
    egress_ips: list[str]
    # namespace_labels is required and non-empty: it becomes the EgressIP's
    # namespaceSelector, and an empty selector would match every namespace.
    namespace_labels: dict[str, str]
    pod_labels: Optional[dict[str, str]] = None
    labels: Optional[dict[str, str]] = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if len(value) > 63 or not NAME_RE.match(value):
            raise ValueError(
                "name must be a valid RFC 1123 label (lowercase alphanumeric "
                "and '-', max 63 chars)"
            )
        return value

    @field_validator("egress_ips")
    @classmethod
    def valid_ips(cls, value):
        if not value:
            raise ValueError("at least one egress IP is required")
        for ip in value:
            ipaddress.ip_address(ip)  # raises ValueError on an invalid IP
        return value

    @field_validator("namespace_labels")
    @classmethod
    def non_empty_namespace_labels(cls, value):
        if not value:
            raise ValueError("namespace_labels must not be empty")
        return value


class EgressIPSummary(BaseModel):
    name: str
    egress_ips: list[str] = Field(default_factory=list)
    namespace_selector: dict = Field(default_factory=dict)
    pod_selector: dict = Field(default_factory=dict)
    status: list = Field(default_factory=list)
    labels: dict = Field(default_factory=dict)


class EgressIPListResponse(BaseModel):
    items: list[EgressIPSummary]
    count: int
