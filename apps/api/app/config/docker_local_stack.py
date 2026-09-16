"""Docker local stack readiness (FTF-0004).

`infra/docker/docker-compose.yml` boots real PostgreSQL, self-hosted
Temporal and an S3-compatible (SeaweedFS) service for local development.
This module is the stdlib-only health-bootstrap process the compose
stack's `api` service runs: it proves readiness with genuine TCP-level
connectivity checks against each dependency, never a hardcoded "healthy"
response. A stopped or unreachable dependency fails its check for real
(connection refused/timeout) — the same code path used for a real outage
and for a deliberately stopped container in a restart/persistence test.

No third-party runtime dependency is added here — CLAUDE.md's "new
runtime deps" is an ask-first hard stop, so this uses only
`socket`/`http.server`/`dataclasses` (same rationale as FTF-0005's
configuration_system.py). It is not the product API: FastAPI is the
frozen target stack (Section 4) for request handlers, added when a task
actually builds them.

Run as a server: `python3 -m app.config.docker_local_stack --serve`
"""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_API_PORT = 8080


@dataclass(frozen=True)
class ServiceEndpoint:
    service: str
    host: str
    port: int


@dataclass(frozen=True)
class StackConfig:
    postgres: ServiceEndpoint
    temporal: ServiceEndpoint
    s3: ServiceEndpoint

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StackConfig:
        source = os.environ if env is None else env
        return cls(
            postgres=ServiceEndpoint(
                "postgres",
                source.get("SOVRA_STACK_POSTGRES_HOST", "postgres"),
                int(source.get("SOVRA_STACK_POSTGRES_PORT", "5432")),
            ),
            temporal=ServiceEndpoint(
                "temporal",
                source.get("SOVRA_STACK_TEMPORAL_HOST", "temporal"),
                int(source.get("SOVRA_STACK_TEMPORAL_PORT", "7233")),
            ),
            s3=ServiceEndpoint(
                "s3",
                source.get("SOVRA_STACK_S3_HOST", "s3"),
                int(source.get("SOVRA_STACK_S3_PORT", "8333")),
            ),
        )

    def endpoints(self) -> tuple[ServiceEndpoint, ...]:
        return (self.postgres, self.temporal, self.s3)


@dataclass(frozen=True)
class ReadinessResult:
    service: str
    healthy: bool
    detail: str


def check_tcp_reachable(
    endpoint: ServiceEndpoint, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> ReadinessResult:
    """Actually opens a TCP connection — no fallback path reports healthy
    without a successful `connect()`."""
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout):
            return ReadinessResult(
                endpoint.service, True, f"tcp connect {endpoint.host}:{endpoint.port} ok"
            )
    except OSError as exc:
        return ReadinessResult(
            endpoint.service, False, f"tcp connect {endpoint.host}:{endpoint.port} failed: {exc}"
        )


def check_stack_readiness(config: StackConfig) -> tuple[ReadinessResult, ...]:
    return tuple(check_tcp_reachable(endpoint) for endpoint in config.endpoints())


def is_ready(results: tuple[ReadinessResult, ...]) -> bool:
    return all(result.healthy for result in results)


def _make_handler(config: StackConfig) -> type[BaseHTTPRequestHandler]:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API name)
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            results = check_stack_readiness(config)
            ready = is_ready(results)
            body = json.dumps(
                {
                    "ready": ready,
                    "services": [
                        {"service": r.service, "healthy": r.healthy, "detail": r.detail}
                        for r in results
                    ],
                }
            ).encode("utf-8")
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_: str, *args: object) -> None:
            return  # content-free: no request bodies/secrets in stdout logs

    return HealthHandler


def serve(config: StackConfig, port: int = DEFAULT_API_PORT) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), _make_handler(config))  # noqa: S104
    server.serve_forever()


def main() -> None:
    if "--serve" not in sys.argv:
        print("usage: python3 -m app.config.docker_local_stack --serve", file=sys.stderr)
        raise SystemExit(2)
    config = StackConfig.from_env()
    port = int(os.environ.get("SOVRA_STACK_API_PORT", str(DEFAULT_API_PORT)))
    serve(config, port)


if __name__ == "__main__":
    main()
