"""Stable validation errors; messages never echo untrusted input."""


class MetadataURLValidationError(ValueError):
    """A source URL cannot be normalized; inspect ``code`` for UI/API use."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class MetadataRetrievalError(Exception):
    """Sanitized provider failure. ``retryable`` permits a later explicit retry.

    Neither provider response bodies nor request URLs/credentials are exposed.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)
