class ServiceError(Exception):
    """A recoverable external-service error with a user-safe message."""

    def __init__(self, message: str, *, retryable: bool = True, kind: str = "service",
                 status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.retryable = retryable
        self.kind = kind  # search | fetch | llm | rate_limit | config | timeout
