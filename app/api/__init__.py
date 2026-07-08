"""Aggregates the route modules into a single router."""

from fastapi import APIRouter

from app.api import egressips, namespaces

api_router = APIRouter()
api_router.include_router(namespaces.router)
api_router.include_router(egressips.router)

__all__ = ["api_router"]
