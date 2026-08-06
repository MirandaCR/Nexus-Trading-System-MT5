"""
Nexus Trader — structured logging via structlog.

Call ``setup_logger`` once at service startup, then use
``get_logger(__name__)`` in every module.

  * **Development** → coloured, human-readable console output.
  * **Production**  → new-line-delimited JSON (for ELK / Loki / etc.).
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

_CONFIGURED = False


# ── Public API ───────────────────────────────────────────────────────────────

def setup_logger(
    service_name: str,
    level: str | int = "INFO",
    *,
    json_output: bool | None = None,
    log_file: str | None = None,
) -> None:
    """
    Configure structlog + stdlib logging for the entire process.

    Parameters
    ----------
    service_name:
        Injected into every log line as ``service``.
    level:
        Root log level (``DEBUG``, ``INFO``, ``WARNING``, …).
    json_output:
        Force JSON (True) or console (False). ``None`` auto-detects from
        the ``NEXUS_LOG_JSON`` env var, falling back to console if a TTY
        is attached.
    log_file:
        Optional path to a file that receives **all** JSON logs regardless
        of the console renderer choice.
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if json_output is None:
        env = os.getenv("NEXUS_LOG_JSON", "").lower()
        if env in ("1", "true", "yes"):
            json_output = True
        elif env in ("0", "false", "no"):
            json_output = False
        else:
            json_output = not sys.stderr.isatty()

    numeric_level = _resolve_level(level)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_service_name(service_name),
        _inject_correlation_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # SECURITY: structlog's rich traceback formatter defaults to
        # show_locals=True, which dumps every frame's local variables into the
        # log. The MT5 connector keeps the broker payload (login + password +
        # server) in a local, so any failure inside it printed the account
        # password in clear text — observed 27 times in a single day's log.
        # Locals stay off; the traceback itself is unaffected.
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.RichTracebackFormatter(
                show_locals=False,
            ),
        )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [console_handler]

    if log_file:
        json_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(json_formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(numeric_level)

    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structlog logger (call *after* ``setup_logger``)."""
    return structlog.get_logger(name)


# ── Correlation ID helpers ───────────────────────────────────────────────────

def new_correlation_id() -> str:
    cid = uuid.uuid4().hex[:12]
    _correlation_id_var.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    _correlation_id_var.set(cid)


def get_correlation_id() -> str:
    return _correlation_id_var.get()


# ── Internal processors ─────────────────────────────────────────────────────

def _inject_service_name(
    service_name: str,
) -> structlog.types.Processor:
    def processor(
        _logger: Any,
        _method: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        event_dict["service"] = service_name
        return event_dict

    return processor


def _inject_correlation_id(
    _logger: Any,
    _method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    cid = _correlation_id_var.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), logging.INFO)
