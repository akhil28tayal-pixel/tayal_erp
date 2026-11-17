import time
import uuid

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.modules.collections.router import router as collections_router
from app.modules.dispatch.router import router as dispatch_router
from app.modules.identity.router import auth_router, users_router
from app.modules.integrations.tally.router import router as tally_router
from app.modules.masters.router import router as masters_router
from app.modules.orders.router import router as orders_router
from app.modules.platform.router import health_router
from app.modules.platform.router import router as platform_router
from app.modules.purchases.router import router as purchases_router
from app.modules.reports.router import router as reports_router
from app.modules.wa_orders.router import router as wa_orders_router

log = structlog.get_logger()


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_runtime()
    configure_logging(settings.env)

    from contextlib import asynccontextmanager

    from app.modules.platform import jobs

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.start()  # automatic Tally sync + nightly backup (not in tests)
        yield
        jobs.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="Tayal ERP API",
        version="0.1.0",
        # the API docs page is for development; it is switched off when the ERP is on the internet
        openapi_url=None if settings.env == "production" else "/api/v1/openapi.json",
        docs_url=None if settings.env == "production" else "/api/v1/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/v1/") and not request.url.path.startswith("/api/v1/docs") \
                and request.url.path != "/api/v1/openapi.json":
            response.headers.setdefault("Cache-Control", "no-store")  # dealer balances etc. must not be cached
        log.info("request", method=request.method, path=request.url.path, status=response.status_code,
                 ms=round((time.perf_counter() - start) * 1000, 1))
        structlog.contextvars.clear_contextvars()
        return response

    install_error_handlers(app)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_router)
    api.include_router(users_router)
    api.include_router(masters_router)
    api.include_router(platform_router)
    api.include_router(orders_router)
    api.include_router(dispatch_router)
    api.include_router(collections_router)
    api.include_router(tally_router)
    api.include_router(purchases_router)
    api.include_router(reports_router)
    api.include_router(wa_orders_router)
    app.include_router(api)
    app.include_router(health_router)
    return app


app = create_app()
