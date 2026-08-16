"""Structured JSON logging with a per-request correlation id.

JSON because App Service / Log Analytics can query fields; a request id because
a failed AI pipeline spans several log lines across two tasks and they need to
be tied together.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

from app.core.config import settings

#: Set by the middleware, read by the log filter. A ContextVar rather than a
#: global because request handling is concurrent.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # Access logs duplicate what the request middleware already records.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").handlers = [handler]

    # These log full request/response bodies at DEBUG, which would put blob
    # contents and bearer tokens into the log stream.
    for noisy in ("azure.core.pipeline.policies.http_logging_policy", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
