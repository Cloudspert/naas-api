"""Shared model helpers and common response models."""

import re
from typing import Optional

from pydantic import BaseModel, Field

# RFC 1123 label (lowercase alphanumeric and '-').
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class MessageResponse(BaseModel):
    message: str
    namespace: Optional[str] = None
    details: dict = Field(default_factory=dict)
