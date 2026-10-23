"""Aggregates the route modules into a single router."""

from fastapi import APIRouter

from app.api import egressips, namespaces, nodes

api_router = APIRouter()
api_router.include_router(namespaces.router)
api_router.include_router(egressips.router)
api_router.include_router(nodes.router)

__all__ = ["api_router"]
