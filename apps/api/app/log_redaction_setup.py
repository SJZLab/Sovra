"""Install FTF-0904 log redaction on the live API process (FTF-0904-W1).

`packages.security.log_redaction` is certified but had no caller: nothing in
the served app installed it, so uvicorn's access log printed every request
line verbatim, query string included (`GET /x?access_token=... HTTP/1.1`).
`create_app()` calls `install_api_log_redaction()` first, before any router
is built, so every record from then on is scrubbed at ingress (the record
factory and `Logger.makeRecord`) and again at each known sink.

Under `uvicorn app.main:create_app --factory` uvicorn has already applied its
logging config when the factory runs, so its handlers exist here. Ingress
scrubbing replaces `record.args` with `()` after rendering the redacted
message; uvicorn's `AccessFormatter` unpacks exactly five args, so it would
fail on every request and the redacting sink would print only the failure
marker. Its handlers therefore get uvicorn's `DefaultFormatter` with the
same level prefix and the (already redacted) rendered message: the same
access line, minus the request-line styling.

Some exception text never passes through logging: an exception that escapes
the factory (e.g. a `ConfigurationError` quoting a bad setting) is printed by
`sys.excepthook`, one that escapes a thread by `threading.excepthook`, and one
raised in `__del__` by `sys.unraisablehook`, and a warning by
`warnings.showwarning`. Each is replaced with a writer that prints only
redacted text to stderr (or the warning's own file), or the constant failure
marker if redaction itself fails. (`logging.captureWarnings` is not used: with
no root handler it attaches a NullHandler and warnings would vanish.)

`traceback.print_exception` / `print_exc` are replaced the same way: under
`uvicorn --workers N` or `--reload` the factory runs in a spawned child, and an
exception escaping it there is printed by multiprocessing's `_bootstrap` via
`traceback.print_exc()`, never reaching `sys.excepthook`.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
import warnings
from types import TracebackType
from typing import Any

from packages.security.log_redaction import (
    REDACTION_FAILED_MARKER,
    attach_sink,
    install_redaction,
    redact_exception,
    redact_text,
)
from uvicorn.logging import AccessFormatter, DefaultFormatter

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_MARK = "_ftf0904_w1_redacting"


def _write_redacted(header: str, exc: BaseException | None, file: Any = None) -> None:
    try:
        text = redact_text(header) if header else ""
        if exc is not None:
            text += redact_exception(exc)
    except Exception:
        text = REDACTION_FAILED_MARKER + "\n"
    try:
        stream = file if file is not None else sys.stderr
        stream.write(text)
        stream.flush()
    except Exception:
        pass


_UNSET = object()
_format_exception = traceback.format_exception


def _print_exception(
    exc: Any,
    value: Any = _UNSET,
    tb: Any = _UNSET,
    limit: int | None = None,
    file: Any = None,
    chain: bool = True,
) -> None:
    args = [exc] + [a for a in (value, tb) if a is not _UNSET]
    try:
        text = redact_text("".join(_format_exception(*args, limit=limit, chain=chain)))
    except Exception:
        text = REDACTION_FAILED_MARKER + "\n"
    try:
        stream = file if file is not None else sys.stderr
        stream.write(text)
    except Exception:
        pass


def _print_exc(limit: int | None = None, file: Any = None, chain: bool = True) -> None:
    exc_type, exc, tb = sys.exc_info()
    _print_exception(exc_type, exc, tb, limit=limit, file=file, chain=chain)


def _excepthook(
    exc_type: type[BaseException], exc: BaseException | None, tb: TracebackType | None
) -> None:
    _write_redacted("", exc)


def _thread_excepthook(args: Any) -> None:
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", None) or "unknown"
    _write_redacted(f"Exception in thread {name}:\n", args.exc_value)


def _unraisablehook(args: Any) -> None:
    try:
        header = f"{args.err_msg or 'Exception ignored in'}: {args.object!r}\n"
    except Exception:
        header = "Exception ignored in: <unrepresentable object>\n"
    _write_redacted(header, args.exc_value)


def _showwarning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    try:
        header = warnings.formatwarning(message, category, filename, lineno, line)
    except Exception:
        header = f"{getattr(category, '__name__', 'Warning')}: <unformattable warning>\n"
    _write_redacted(header, None, file)


def _mark(hook: Any) -> Any:
    setattr(hook, _MARK, True)
    return hook


def _install_exception_hooks() -> None:
    if not getattr(sys.excepthook, _MARK, False):
        sys.excepthook = _mark(_excepthook)
    if not getattr(threading.excepthook, _MARK, False):
        threading.excepthook = _mark(_thread_excepthook)
    if not getattr(sys.unraisablehook, _MARK, False):
        sys.unraisablehook = _mark(_unraisablehook)
    if not getattr(warnings.showwarning, _MARK, False):
        warnings.showwarning = _mark(_showwarning)
    if not getattr(traceback.print_exception, _MARK, False):
        traceback.print_exception = _mark(_print_exception)
    if not getattr(traceback.print_exc, _MARK, False):
        traceback.print_exc = _mark(_print_exc)


def install_api_log_redaction() -> None:
    """Ingress + sink redaction for the root, uvicorn and last-resort handlers,
    plus redacting hooks for exceptions printed outside logging. Idempotent."""
    install_redaction()
    _install_exception_hooks()
    for name in UVICORN_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            formatter = handler.formatter
            if isinstance(formatter, AccessFormatter):
                handler.setFormatter(
                    DefaultFormatter(
                        "%(levelprefix)s %(message)s",
                        datefmt=formatter.datefmt,
                        use_colors=formatter.use_colors,
                    )
                )
            attach_sink(handler)
    if logging.lastResort is not None:
        attach_sink(logging.lastResort)
