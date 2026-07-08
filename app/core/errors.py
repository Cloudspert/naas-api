"""Shared error type for API operations."""


class ApiError(Exception):
    """An expected, reportable failure. status_code becomes the HTTP status."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code
