#!/usr/bin/env python3
"""FTF-0103-F1 -- RBAC middleware request-body cap (security hardening).

`require_role` (apps/api/app/auth/rbac_middleware.py) reads a mutating request's body to
cross-check a body-supplied tenant. Before this follow-up it read the whole body with no limit,
so a chunked upload without Content-Length was read to exhaustion before any route-level cap.
`MAX_JSON_BODY_BYTES` (32 MiB, the largest existing route cap) now bounds that read.

Clause -> test mapping:
* F1-A1 a chunked body one byte over the cap -> 413 `request_too_large`, route handler never runs:
  test_chunked_one_byte_over_cap_is_refused
* F1-A2 exactly the cap (chunked and Content-Length) is accepted, the route still reads the body:
  test_body_exactly_at_cap_is_accepted
* F1-A3 a Content-Length over the cap -> 413 without reading the body, handler never runs:
  test_declared_length_over_cap_is_refused_without_reading
* F1-N1 the cap is not a way to skip the tenant check; a normal body still goes through the
  tenant cross-check and a route can read the body afterwards:
  test_small_bodies_still_cross_checked_and_readable
* F1-N3 a route with its own published oversize error contract (FTF-0602-P1: 400 `body_too_large`)
  keeps it via `oversize_error`; routes without the option keep 413:
  test_route_can_keep_its_own_oversize_error_contract
* F1-N2 the constant is the documented 32 MiB: test_cap_constant_is_32_mib

Real uvicorn server over a real local socket (the pattern of tests/tasks/test_ftf_0103.py).
Run directly: python3 tests/tasks/test_ftf_0103_f1.py
"""

from __future__ import annotations

import contextlib
import http.client
import json
import pathlib
import socket
import sys
import threading
import time
import uuid
from collections.abc import Iterator

import uvicorn
from fastapi import Depends, FastAPI, Request

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.auth import rbac_middleware  # noqa: E402
from app.auth.rbac_middleware import (  # noqa: E402
    AuthenticatedPrincipal,
    AuthorizedContext,
    Role,
    require_role,
)

FAILURES: list[str] = []
CAP = 33554432
ORG = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_ORG = uuid.UUID("22222222-2222-4222-8222-222222222222")
ISSUER = "https://issuer.example"
HANDLER_RUNS: list[int] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _principal(request: Request) -> AuthenticatedPrincipal | None:
    if request.headers.get("x-test-principal") == "member":
        return AuthenticatedPrincipal(ISSUER, "member")
    return None


def _role(issuer: str, subject: str, organization_id: uuid.UUID) -> Role | None:
    return Role.ML_ENGINEER if (subject, organization_id) == ("member", ORG) else None


def _build_app() -> FastAPI:
    app = FastAPI()
    guard = Depends(
        require_role(
            frozenset({Role.ML_ENGINEER}), principal_resolver=_principal, role_lookup=_role
        )
    )

    custom_guard = Depends(
        require_role(
            frozenset({Role.ML_ENGINEER}),
            principal_resolver=_principal,
            role_lookup=_role,
            oversize_error=(400, "body_too_large"),
        )
    )

    @app.post("/api/v1/orgs/{organization_id}/custom-things")
    async def create_custom(request: Request, ctx: AuthorizedContext = custom_guard) -> dict:
        HANDLER_RUNS.append(1)
        return {"organization_id": str(ctx.organization_id)}

    @app.post("/api/v1/orgs/{organization_id}/things")
    async def create(request: Request, ctx: AuthorizedContext = guard) -> dict:
        HANDLER_RUNS.append(1)
        body = await request.body()
        return {"organization_id": str(ctx.organization_id), "bytes": len(body)}

    return app


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _running_app() -> Iterator[int]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(_build_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _json_payload(total: int, org: uuid.UUID | None = None) -> bytes:
    """A valid JSON object of exactly `total` bytes."""
    head = b'{"organization_id":"%s","pad":"' % str(org or ORG).encode()
    tail = b'"}'
    pad = total - len(head) - len(tail)
    assert pad >= 0
    return head + b"a" * pad + tail


def _chunks(payload: bytes, size: int = 1 << 20) -> Iterator[bytes]:
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


def _post_chunked(
    port: int, payload: bytes, org: uuid.UUID = ORG, route: str = "things"
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request(
            "POST",
            f"/api/v1/orgs/{org}/{route}",
            body=_chunks(payload),
            headers={"X-Test-Principal": "member", "Content-Type": "application/json"},
            encode_chunked=True,
        )
        response = conn.getresponse()
        return response.status, response.read()
    except (ConnectionError, http.client.HTTPException) as exc:
        # The server may close the socket once it has refused the oversized upload.
        return -1, repr(exc).encode()
    finally:
        conn.close()


def _post_length(port: int, payload: bytes, org: uuid.UUID = ORG) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request(
            "POST",
            f"/api/v1/orgs/{org}/things",
            body=payload,
            headers={"X-Test-Principal": "member", "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _post_lying_length(port: int, declared: int) -> tuple[int, bytes]:
    """Headers declare `declared` bytes; no body byte is ever sent."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=15)
    try:
        sock.sendall(
            (
                f"POST /api/v1/orgs/{ORG}/things HTTP/1.1\r\nHost: x\r\n"
                f"X-Test-Principal: member\r\nContent-Type: application/json\r\n"
                f"Content-Length: {declared}\r\n\r\n"
            ).encode()
        )
        data = b""
        while b"\r\n\r\n" not in data:
            piece = sock.recv(65536)
            if not piece:
                break
            data += piece
        status = int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/") else -1
        return status, data
    finally:
        sock.close()


def test_route_can_keep_its_own_oversize_error_contract() -> None:
    with _running_app() as port:
        HANDLER_RUNS.clear()
        status, body = _post_chunked(port, _json_payload(CAP + 1), route="custom-things")
        check("FTF-0103-F1-N3", f"custom oversize_error status (got {status})", status == 400)
        check(
            "FTF-0103-F1-N3",
            "custom oversize_error detail",
            status != 400 or json.loads(body) == {"detail": "body_too_large"},
        )
        check("FTF-0103-F1-N3", "the handler never ran", HANDLER_RUNS == [])
        status, _ = _post_chunked(port, _json_payload(CAP), route="custom-things")
        check("FTF-0103-F1-N3", f"exactly the cap is still accepted (got {status})", status == 200)
        default, _ = _post_chunked(port, _json_payload(CAP + 1))
        check(
            "FTF-0103-F1-N3", f"routes without the option keep 413 (got {default})", default == 413
        )


def test_cap_constant_is_32_mib() -> None:
    check(
        "FTF-0103-F1-N2",
        "MAX_JSON_BODY_BYTES is 32 MiB",
        rbac_middleware.MAX_JSON_BODY_BYTES == CAP,
    )


def test_chunked_one_byte_over_cap_is_refused() -> None:
    with _running_app() as port:
        HANDLER_RUNS.clear()
        status, body = _post_chunked(port, _json_payload(CAP + 1))
        check("FTF-0103-F1-A1", f"chunked cap+1 -> 413 (got {status})", status == 413)
        check(
            "FTF-0103-F1-A1",
            "the 413 carries the stable code",
            status != 413 or json.loads(body) == {"detail": "request_too_large"},
        )
        check("FTF-0103-F1-A1", "the route handler never ran", HANDLER_RUNS == [])
        much_bigger, _ = _post_chunked(port, _json_payload(CAP * 2))
        check("FTF-0103-F1-A1", f"chunked 2x cap -> 413 (got {much_bigger})", much_bigger == 413)
        check("FTF-0103-F1-A1", "no handler run for 2x cap", HANDLER_RUNS == [])


def test_body_exactly_at_cap_is_accepted() -> None:
    with _running_app() as port:
        HANDLER_RUNS.clear()
        status, body = _post_chunked(port, _json_payload(CAP))
        check("FTF-0103-F1-A2", f"chunked exactly cap -> 200 (got {status})", status == 200)
        check(
            "FTF-0103-F1-A2",
            "the handler still reads every byte after the middleware read",
            status != 200 or json.loads(body)["bytes"] == CAP,
        )
        status, _ = _post_length(port, _json_payload(CAP))
        check("FTF-0103-F1-A2", f"Content-Length exactly cap -> 200 (got {status})", status == 200)


def test_declared_length_over_cap_is_refused_without_reading() -> None:
    with _running_app() as port:
        HANDLER_RUNS.clear()
        status, data = _post_lying_length(port, CAP + 1)
        check(
            "FTF-0103-F1-A3", f"declared cap+1, no body sent -> 413 (got {status})", status == 413
        )
        check("FTF-0103-F1-A3", "the handler never ran", HANDLER_RUNS == [])
        status, _ = _post_length(port, _json_payload(CAP + 1))
        check(
            "FTF-0103-F1-A3",
            f"full body with Content-Length cap+1 -> 413 (got {status})",
            status == 413,
        )
        check(
            "FTF-0103-F1-A3", "the handler never ran for the full body either", HANDLER_RUNS == []
        )


def test_small_bodies_still_cross_checked_and_readable() -> None:
    with _running_app() as port:
        HANDLER_RUNS.clear()
        payload = _json_payload(200)
        status, body = _post_length(port, payload)
        check("FTF-0103-F1-N1", f"small body -> 200 (got {status})", status == 200)
        check(
            "FTF-0103-F1-N1",
            "the route handler reads the same bytes the middleware read",
            status != 200 or json.loads(body)["bytes"] == len(payload),
        )
        mismatch, _ = _post_length(port, _json_payload(200, OTHER_ORG))
        check(
            "FTF-0103-F1-N1", f"body naming another tenant -> 403 (got {mismatch})", mismatch == 403
        )
        before = len(HANDLER_RUNS)
        mismatch_chunked, _ = _post_chunked(port, _json_payload(200, OTHER_ORG))
        check(
            "FTF-0103-F1-N1",
            f"chunked body naming another tenant -> 403 (got {mismatch_chunked})",
            mismatch_chunked == 403 and len(HANDLER_RUNS) == before,
        )
        empty, _ = _post_length(port, b"")
        check("FTF-0103-F1-N1", f"empty body -> 200 (got {empty})", empty == 200)
        garbage, _ = _post_length(port, b"not json")
        check(
            "FTF-0103-F1-N1", f"unparsable body -> 200, as before (got {garbage})", garbage == 200
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                FAILURES.append(f"FAIL [FTF-0103-F1] {name} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"FTF-0103-F1 body cap tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0103-F1 body cap tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
