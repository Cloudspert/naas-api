"""HTTP endpoints for namespace management."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_cache, get_manager, require_auth, run
from app.models import (
    CreateNamespaceRequest,
    MessageResponse,
    NamespaceListResponse,
    NamespaceStatusResponse,
    UpdateQuotaRequest,
)

router = APIRouter(prefix="/api/v1/namespaces", tags=["namespaces"])


@router.post("", response_model=MessageResponse, status_code=201,
             summary="Create a namespace with resource quotas")
def create_namespace(payload: CreateNamespaceRequest, manager=Depends(get_manager),
                     _=Depends(require_auth)):
    result = run(lambda: manager.create_namespace(payload.name, payload.limits))
    return MessageResponse(message="namespace created", namespace=payload.name, details=result)


@router.get("", response_model=NamespaceListResponse,
            summary="List managed namespaces (served from cache)")
def list_namespaces(cache=Depends(get_cache), _=Depends(require_auth)):
    items, cached_at = cache.get()
    return NamespaceListResponse(items=items, count=len(items), cached_at=cached_at)


@router.get("/{name}/status", response_model=NamespaceStatusResponse,
            summary="Report whether a namespace exists")
def namespace_status(name: str, manager=Depends(get_manager), _=Depends(require_auth)):
    exists = run(lambda: manager.namespace_exists(name))
    return NamespaceStatusResponse(namespace=name, exists=exists)


@router.patch("/{name}/quota", response_model=MessageResponse,
              summary="Update quotas for a namespace (only supplied dimensions change)")
def update_quota(name: str, payload: UpdateQuotaRequest, manager=Depends(get_manager),
                 _=Depends(require_auth)):
    result = run(lambda: manager.update_quota(name, payload.limits))
    return MessageResponse(message="quota updated", namespace=name, details=result)


@router.delete("/{name}", response_model=MessageResponse,
               summary="Mark a namespace for deletion, or force-delete it")
def delete_namespace(name: str, force: bool = Query(False), manager=Depends(get_manager),
                     _=Depends(require_auth)):
    if force:
        result = run(lambda: manager.force_delete(name))
        return MessageResponse(message="namespace force-deleted", namespace=name, details=result)
    result = run(lambda: manager.mark_for_deletion(name))
    return MessageResponse(message="namespace marked for deletion", namespace=name, details=result)
