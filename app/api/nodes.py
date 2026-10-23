"""HTTP endpoints for cluster nodes (read-only, served from NodeCache)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import parse_labels, require_auth
from app.models import NodeDetail, NodeListResponse, NodeSummary

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


def get_node_cache(request: Request):
    return request.app.state.node_cache


@router.get("", response_model=NodeListResponse, summary="List cluster nodes")
def list_nodes(
    labels: Optional[str] = Query(None, description="Filter, e.g. node-role.kubernetes.io/worker="),
    cache=Depends(get_node_cache),
    _=Depends(require_auth),
):
    details = cache.list(parse_labels(labels))
    items = [NodeSummary(**d.model_dump()) for d in details]
    return NodeListResponse(items=items, count=len(items))


@router.get("/{name}", response_model=NodeDetail, summary="Get a node by name")
def get_node(name: str, cache=Depends(get_node_cache), _=Depends(require_auth)):
    node = cache.get(name)
    if node is None:
        raise HTTPException(status_code=404, detail=f"node '{name}' not found")
    return node
