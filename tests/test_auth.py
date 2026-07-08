"""Tests for the authentication layer."""

import base64

import pytest
from starlette.requests import Request

from app.auth import AuthError, BasicAuth, build_auth
from app.core.config import Settings


def _request(authorization):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "headers": headers})


def _basic(user, pw):
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Basic {token}"


@pytest.fixture
def backend():
    return BasicAuth({"admin": "s3cret"})


def test_valid_credentials_return_principal(backend):
    principal = backend.authenticate(_request(_basic("admin", "s3cret")))
    assert principal.username == "admin"
    assert principal.module == "basic"


@pytest.mark.parametrize("header", [None, "", "Bearer abc", "Basic", "Basic !!!not-base64"])
def test_malformed_headers_rejected(backend, header):
    with pytest.raises(AuthError):
        backend.authenticate(_request(header))


def test_wrong_password_rejected(backend):
    with pytest.raises(AuthError):
        backend.authenticate(_request(_basic("admin", "nope")))


def test_unknown_user_rejected(backend):
    with pytest.raises(AuthError):
        backend.authenticate(_request(_basic("ghost", "s3cret")))


def test_auth_error_carries_challenge(backend):
    with pytest.raises(AuthError) as exc:
        backend.authenticate(_request(None))
    assert "WWW-Authenticate" in exc.value.headers


def test_openapi_scheme_is_basic(backend):
    name, scheme = backend.openapi_scheme()
    assert name == "basicAuth"
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "basic"


def test_build_auth_basic():
    backend = build_auth(Settings(basic_auth_users={"u": "p"}, auth_module="basic"))
    assert isinstance(backend, BasicAuth)
    assert backend.name == "basic"


def test_build_auth_unknown_module():
    with pytest.raises(ValueError):
        build_auth(Settings(auth_module="does-not-exist"))
