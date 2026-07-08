"""Auth primitives shared by all modules."""

from dataclasses import dataclass

BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="naas-api"'}


@dataclass
class Principal:
    username: str
    module: str


class AuthError(Exception):
    def __init__(self, message, headers=None):
        super().__init__(message)
        self.message = message
        self.headers = headers or {}
