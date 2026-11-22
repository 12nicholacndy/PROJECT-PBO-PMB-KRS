from typing import Any


class FinanceError(Exception):
    """Base domain error untuk Modul Keuangan SPP (Minggu 11)."""

    status_code: int = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def body(self) -> dict[str, Any]:
        return {"detail": self.message}


class FinanceValidationError(FinanceError):
    status_code = 400


class WebhookSignatureError(FinanceError):
    """Dilempar ketika signature HMAC webhook payment gateway tidak valid atau hilang."""

    status_code = 401


class FinanceForbiddenError(FinanceError):
    status_code = 403


class FinanceNotFoundError(FinanceError):
    status_code = 404


class FinanceConflictError(FinanceError):
    status_code = 409
