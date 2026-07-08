"""Pluggable authentication. HTTP Basic today; add more modules in AUTH_MODULES."""

from app.auth.base import AuthError, Principal
from app.auth.basic import BasicAuth
from app.core.config import Settings

__all__ = ["AuthError", "Principal", "BasicAuth", "build_auth", "AUTH_MODULES"]

# Register additional auth modules here (name -> factory).
AUTH_MODULES = {
    "basic": lambda settings: BasicAuth(settings.basic_auth_users),
}


def build_auth(settings: Settings):
    if settings.auth_module not in AUTH_MODULES:
        raise ValueError(f"unknown auth module '{settings.auth_module}'")
    return AUTH_MODULES[settings.auth_module](settings)
