from typing import Any, Dict

from fastapi.responses import JSONResponse


class ProxyValidationError(Exception):
    def __init__(self, status_code: int, message: str, error_type: str = "invalid_request_error"):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


def anthropic_error_payload(message: str, error_type: str = "invalid_request_error") -> Dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def error_response(status_code: int, message: str, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=anthropic_error_payload(message, error_type=error_type),
    )
