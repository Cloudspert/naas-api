"""Shared FastAPI dependencies and helpers for the routers."""

from fastapi import HTTPException, Request

from app.auth import AuthError
from app.core.errors import ApiError


def get_manager(request: Request):
    return request.app.state.manager


def get_cache(request: Request):
    return request.app.state.cache


def require_auth(request: Request):
    try:
        return request.app.state.auth.authenticate(request)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message, headers=exc.headers)


def run(func):
    """Run a manager call and turn an ApiError into an HTTP error."""
    try:
        return func()
    except ApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
