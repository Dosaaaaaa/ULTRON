"""Application errors and their HTTP mapping. Error bodies never contain stack traces."""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("ultron.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, extra: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra or {}


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class ModelsUnavailable(AppError):
    status_code = 503
    code = "models_unavailable"


def _body(code: str, message: str, request: Request, extra: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
            **(extra or {}),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, Unauthorized) else None
        if isinstance(exc, RateLimited) and "retry_after" in exc.extra:
            headers = {"Retry-After": str(exc.extra["retry_after"])}
        return JSONResponse(
            _body(exc.code, exc.message, request, exc.extra),
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never log the exception *message*: libraries (SQLAlchemy, drivers) can embed request
        # data in it, including the user's message text. Log the type and the stack frames only.
        log.error(
            "unhandled_error",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "exc_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "frames": "".join(traceback.format_tb(exc.__traceback__)),
            },
        )
        return JSONResponse(_body("internal_error", "Internal error.", request), status_code=500)
