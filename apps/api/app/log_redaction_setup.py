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
"""

from __future__ import annotations

import logging

from packages.security.log_redaction import attach_sink, install_redaction
from uvicorn.logging import AccessFormatter, DefaultFormatter

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def install_api_log_redaction() -> None:
    """Ingress + sink redaction for the root, uvicorn and last-resort handlers. Idempotent."""
    install_redaction()
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
