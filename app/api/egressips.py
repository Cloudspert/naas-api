"""HTTP endpoints for EgressIP management."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import require_auth, run
from app.models import (
    CreateEgressIPRequest,
    EgressIPListResponse,
    EgressIPSummary,
    MessageResponse,
)

router = APIRouter(prefix="/api/v1/egressips", tags=["egressips"])


def get_egress_manager(request: Request):
    return request.app.state.egress_manager


def parse_labels(raw: Optional[str]) -> dict:
    """Parse a 'k=v,k2=v2' label filter into a dict."""
    result = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise HTTPException(status_code=422, detail=f"invalid label '{part}', expected key=value")
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


@router.post("", response_model=EgressIPSummary, status_code=201,
             summary="Create an EgressIP")
def create_egressip(payload: CreateEgressIPRequest, manager=Depends(get_egress_manager),
                    _=Depends(require_auth)):
    result = run(lambda: manager.create(
        payload.name,
        payload.egress_ips,
        payload.namespace_labels,
        payload.pod_labels,
        payload.labels,
    ))
    return EgressIPSummary(**result)


@router.get("", response_model=EgressIPListResponse,
            summary="List EgressIPs, optionally filtered by labels")
def list_egressips(labels: Optional[str] = Query(None, description="Filter, e.g. ecosystem=payments,tier=frontend"),
                   manager=Depends(get_egress_manager), _=Depends(require_auth)):
    label_filter = parse_labels(labels)
    items = run(lambda: manager.list(label_filter))
    return EgressIPListResponse(items=[EgressIPSummary(**i) for i in items], count=len(items))


@router.get("/{name}", response_model=EgressIPSummary, summary="Get an EgressIP by name")
def get_egressip(name: str, manager=Depends(get_egress_manager), _=Depends(require_auth)):
    result = run(lambda: manager.get(name))
    return EgressIPSummary(**result)


@router.delete("/{name}", response_model=MessageResponse, summary="Delete an EgressIP")
def delete_egressip(name: str, manager=Depends(get_egress_manager), _=Depends(require_auth)):
    run(lambda: manager.delete(name))
    return MessageResponse(message="egressip deleted", details={"name": name})
