"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import StorageBackend, settings
from app.core.database import check_database, dispose_engine
from app.core.exceptions import AppError
from app.core.logging import configure_logging, request_id_var
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Starting %s (%s)", settings.project_name, settings.environment)

    if settings.storage_backend is StorageBackend.AZURE:
        from app.storage.azure_blob import AzureBlobStorage

        await AzureBlobStorage().ensure_container()

    yield

    # Return every pooled connection cleanly so a redeploy does not leave
    # sockets open against the database.
    await dispose_engine()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.project_name,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

# Order matters: middleware added last runs first.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# --- error handling ----------------------------------------------------------
@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """The single place domain errors become HTTP responses.

    Business logic raises typed exceptions and never imports a status code.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
            request_id=request_id_var.get(),
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Flatten Pydantic's error list into one message the UI can display."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "The request payload is invalid.")
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error_code="validation_error",
            message=f"{field}: {message}" if field else message,
            details={"errors": exc.errors()},
            request_id=request_id_var.get(),
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Nothing internal reaches the client — the request id is the bridge
    between what the user sees and what the logs hold."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="internal_error",
            message="Something went wrong. Please try again.",
            request_id=request_id_var.get(),
        ).model_dump(),
    )


# --- health ------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness — is the process up? Must not touch dependencies."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    """Readiness — can it actually serve? Checks the database."""
    await check_database()
    return {"status": "ready", "database": "ok"}


app.include_router(api_router, prefix=settings.api_v1_prefix)
