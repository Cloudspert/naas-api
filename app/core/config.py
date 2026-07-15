"""Application settings, loaded from APP_* environment variables."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    app_name: str = "naas-api"
    log_level: str = "INFO"

    # Cluster connection. Uses the in-cluster service account by default; set
    # in_cluster=false and a kubeconfig_path for local development.
    in_cluster: bool = True
    kubeconfig_path: Optional[str] = None

    # The list endpoint returns namespaces matching this label; the cache
    # refreshes every cache_refresh_interval_seconds.
    namespace_label_selector: str = "managed-by=naas-api"
    cache_refresh_interval_seconds: int = 30

    # Annotation written when a namespace is soft-deleted.
    deletion_annotation_key: str = "naas-api/marked-for-deletion-at"

    # config.py lives in app/core/, templates in app/templates/ -> parents[1].
    template_dir: str = str(Path(__file__).resolve().parents[1] / "templates")
    # Label stamped on created resources, as "key=value".
    managed_label: str = "managed-by=naas-api"
    # Optional key prefix (DNS subdomain) for caller-supplied labels AND
    # annotations, e.g. "company.example.io" turns {env: prod} into
    # {company.example.io/env: prod}. Left null/empty -> keys used verbatim.
    key_prefix: Optional[str] = None

    # EgressIP CRD coordinates (configurable per platform).
    egressip_api_version: str = "k8s.ovn.org/v1"
    egressip_kind: str = "EgressIP"

    # Active auth module and its credentials.
    auth_module: str = "basic"
    basic_auth_users: dict[str, str] = {}

    @field_validator("basic_auth_users", mode="before")
    @classmethod
    def parse_users(cls, value):
        # Allow the users map to be passed as a JSON string (env var).
        if isinstance(value, str):
            value = value.strip()
            return json.loads(value) if value else {}
        return value

    @property
    def managed_label_key(self) -> str:
        return self.managed_label.split("=", 1)[0]

    @property
    def managed_label_value(self) -> str:
        parts = self.managed_label.split("=", 1)
        return parts[1] if len(parts) > 1 else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
