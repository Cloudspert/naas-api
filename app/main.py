"""FastAPI application: startup wiring, logging, health checks, Swagger."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app import __version__
from app.api import api_router
from app.auth import build_auth
from app.core.config import get_settings
from app.k8s.client import K8sClient
from app.services.cache import NamespaceCache
from app.services.egressips import EgressIPManager
from app.services.namespaces import NamespaceManager

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

DESCRIPTION = (
    "NaaS API - Namespace as a Service. Create namespaces with quotas, list the "
    "managed ones, update quotas, and delete them (soft-mark or force). All "
    "/api/v1 endpoints are protected by the active auth module (HTTP Basic by "
    "default) - use the Authorize button."
)


def configure_logging(level):
    """Send logs (including lifecycle events) to stdout. Idempotent."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        if getattr(handler, "_naas_stdout", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler._naas_stdout = True
    root.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    k8s = K8sClient(in_cluster=settings.in_cluster, kubeconfig_path=settings.kubeconfig_path)
    app.state.settings = settings
    app.state.k8s = k8s
    app.state.manager = NamespaceManager(
        k8s=k8s,
        managed_label_key=settings.managed_label_key,
        managed_label_value=settings.managed_label_value,
        deletion_annotation_key=settings.deletion_annotation_key,
    )
    app.state.cache = NamespaceCache(
        k8s=k8s,
        label_selector=settings.namespace_label_selector,
        refresh_interval=settings.cache_refresh_interval_seconds,
        deletion_annotation_key=settings.deletion_annotation_key,
    )
    app.state.egress_manager = EgressIPManager(
        k8s=k8s,
        api_version=settings.egressip_api_version,
        kind=settings.egressip_kind,
        managed_label_key=settings.managed_label_key,
        managed_label_value=settings.managed_label_value,
    )
    app.state.auth = build_auth(settings)

    app.state.cache.start()
    logging.getLogger("app").info(
        "%s v%s started (auth=%s)", settings.app_name, __version__, settings.auth_module
    )
    try:
        yield
    finally:
        app.state.cache.stop()


def build_openapi(app: FastAPI):
    """Add the active auth module's security scheme to the OpenAPI schema."""

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title="NaaS API", version=__version__, description=DESCRIPTION, routes=app.routes
        )
        scheme_name, scheme = build_auth(get_settings()).openapi_scheme()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})[scheme_name] = scheme
        for path, item in schema.get("paths", {}).items():
            if not path.startswith("/api/"):
                continue
            for method, operation in item.items():
                if method in HTTP_METHODS:
                    operation.setdefault("security", []).append({scheme_name: []})
        app.openapi_schema = schema
        return schema

    return openapi


def create_app():
    app = FastAPI(title="NaaS API", description=DESCRIPTION, version=__version__, lifespan=lifespan)

    @app.get("/healthz", tags=["health"], summary="Liveness probe")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"], summary="Readiness probe")
    def readyz():
        _, cached_at = app.state.cache.get()
        return {"status": "ok" if cached_at else "warming", "cached_at": cached_at}

    app.include_router(api_router)
    app.openapi = build_openapi(app)
    return app


app = create_app()
