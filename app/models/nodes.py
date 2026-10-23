"""Node response models."""

from typing import Optional

from pydantic import BaseModel, Field


class NodeSummary(BaseModel):
    name: str
    ready: Optional[bool] = None
    roles: list[str] = Field(default_factory=list)
    internal_ip: Optional[str] = None
    labels: dict = Field(default_factory=dict)


class NodeDetail(NodeSummary):
    unschedulable: bool = False
    addresses: dict = Field(default_factory=dict)
    annotations: dict = Field(default_factory=dict)
    capacity: dict = Field(default_factory=dict)
    allocatable: dict = Field(default_factory=dict)
    kubelet_version: Optional[str] = None
    os_image: Optional[str] = None
    architecture: Optional[str] = None


class NodeListResponse(BaseModel):
    items: list[NodeSummary]
    count: int
