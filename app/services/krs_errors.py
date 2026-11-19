class KRSError(Exception):
    """Base domain error untuk modul KRS dengan HTTP status code polimorfik."""

    status_code: int = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class KRSValidationError(KRSError):
    status_code = 400


class KRSForbiddenError(KRSError):
    status_code = 403


class KRSNotFoundError(KRSError):
    status_code = 404


class KRSTransitionError(KRSError):
    status_code = 409


class KRSLockedError(KRSError):
    status_code = 409
