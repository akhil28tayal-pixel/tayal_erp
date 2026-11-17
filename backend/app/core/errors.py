"""Consistent RFC 7807 problem+json errors with stable machine-readable codes."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger()


class AppError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        errors: list[dict[str, Any]] | None = None,
        **extra: Any,
    ):
        self.status, self.code, self.detail, self.errors, self.extra = status, code, detail, errors or [], extra
        super().__init__(detail)


def not_found(entity: str) -> AppError:
    return AppError(404, "NOT_FOUND", f"{entity} not found")


def forbidden(detail: str = "You do not have permission for this action", code: str = "FORBIDDEN") -> AppError:
    return AppError(403, code, detail)


def conflict(code: str, detail: str, **extra: Any) -> AppError:
    return AppError(409, code, detail, **extra)


def _problem(request: Request, status: int, code: str, detail: str, errors=None, **extra) -> JSONResponse:
    body = {
        "type": f"https://erp.local/errors/{code.lower()}",
        "title": code.replace("_", " ").title(),
        "status": status,
        "code": code,
        "detail": detail,
        "errors": errors or [],
        "request_id": getattr(request.state, "request_id", None),
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _problem(request, exc.status, exc.code, exc.detail, exc.errors, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "code": e["type"], "message": e["msg"]}
            for e in exc.errors()
        ]
        return _problem(request, 422, "VALIDATION_ERROR", "Request validation failed", errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            exc.status_code, "HTTP_ERROR"
        )
        return _problem(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        log.warning("integrity_error", constraint=constraint)
        return _problem(
            request, 409, "CONFLICT", "The change conflicts with existing data", constraint=constraint
        )
