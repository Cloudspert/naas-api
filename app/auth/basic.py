"""HTTP Basic authentication module."""

import base64
import binascii
import hmac

from starlette.requests import Request

from app.auth.base import BASIC_CHALLENGE, AuthError, Principal


class BasicAuth:
    """HTTP Basic auth backed by a username -> password dict."""

    name = "basic"

    def __init__(self, users):
        self.users = users or {}

    def authenticate(self, request: Request) -> Principal:
        header = request.headers.get("Authorization", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            raise AuthError("missing or invalid Authorization header", BASIC_CHALLENGE)

        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            raise AuthError("malformed basic credentials", BASIC_CHALLENGE)

        username, sep, password = decoded.partition(":")
        if not sep:
            raise AuthError("malformed basic credentials", BASIC_CHALLENGE)

        expected = self.users.get(username)
        if expected is None or not hmac.compare_digest(expected, password):
            raise AuthError("invalid username or password", BASIC_CHALLENGE)

        return Principal(username=username, module=self.name)

    def openapi_scheme(self):
        # Advertised in the OpenAPI schema so Swagger shows an Authorize button.
        return "basicAuth", {
            "type": "http",
            "scheme": "basic",
            "description": "HTTP Basic authentication.",
        }
