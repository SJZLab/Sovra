"""Shared stateful fake Argilla server for FTF-R006-P1b tests
(`test_ftf_r006_p1b.py`, `tests/integration/test_argilla_identity_scope.py`).

A real loopback HTTP server (`ThreadingHTTPServer`/`BaseHTTPRequestHandler`,
real sockets, real bytes on the wire) that implements exactly the pinned
Argilla v2.8.0 `/api/v1` surface `packages/integrations/argilla/client.py`
calls (`/status`, `/me/workspaces`, `/workspaces`, `/workspaces/{id}/users`,
`/users`), with real in-memory state (workspaces, users, memberships) so a
create/list/add/remove sequence behaves exactly as the real server would,
not a scripted reply queue. This is the same real-socket, real-HTTP
`Stub`/`RawServer` recipe `tests/tasks/test_ftf_r006_p1_client.py` already
established for FTF-R006-P1a (and the same class of locally-built-service
precedent this repository already uses for OpenBao/Postgres); it is a
faithful re-implementation of the pinned contract, not the upstream
`argilla-server` binary itself -- the real binary additionally requires a
live OpenSearch/Elasticsearch search engine at startup for endpoints this
task never calls (confirmed by a real `pip install argilla-server==2.8.0`
attempt during this task's own development), a materially heavier,
separately-scoped infrastructure investment disclosed in this task's own
submission rather than silently worked around.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from packages.integrations.argilla.client import ArgillaClient
from packages.security.url_policy import ConnectionPolicy

_API = "/api/v1"


class FakeArgillaServer:
    def __init__(self) -> None:
        self.workspaces: dict[str, dict[str, str]] = {}  # name -> {id, name}
        self.users: dict[str, dict[str, Any]] = {}  # id -> {id, username, role, api_key}
        self.memberships: set[tuple[str, str]] = set()  # (workspace_id, user_id)
        self.healthy = True
        self.requests: list[tuple[str, str]] = []
        self._lock = threading.Lock()  # real server state is mutated by many request threads.
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                return json.loads(raw) if raw else {}

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _detail(self, status: int, message: str) -> None:
                self._reply(status, {"detail": message})

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append(("GET", self.path))
                if not server.healthy:
                    self._detail(503, "server stopped")
                    return
                if self.path == f"{_API}/status":
                    self._reply(
                        200,
                        {"version": "2.8.0", "search_engine": "opensearch-fake", "memory": {}},
                    )
                    return
                if self.path == f"{_API}/me/workspaces":
                    self._reply(200, {"items": list(server.workspaces.values())})
                    return
                if self.path.startswith(f"{_API}/workspaces/") and self.path.endswith("/users"):
                    workspace_id = self.path.split("/")[-2]
                    if not any(w["id"] == workspace_id for w in server.workspaces.values()):
                        self._detail(404, "workspace not found")
                        return
                    members = [
                        server.users[uid]
                        for (wid, uid) in server.memberships
                        if wid == workspace_id
                    ]
                    self._reply(200, {"items": members})
                    return
                if self.path.startswith(f"{_API}/users/"):
                    user_id = self.path.rsplit("/", 1)[-1]
                    user = server.users.get(user_id)
                    if user is None:
                        self._detail(404, "user not found")
                        return
                    self._reply(200, user)
                    return
                self._detail(404, "not found")

            def do_POST(self) -> None:  # noqa: N802
                server.requests.append(("POST", self.path))
                if not server.healthy:
                    self._detail(503, "server stopped")
                    return
                body = self._body()
                if self.path == f"{_API}/workspaces":
                    name = body.get("name")
                    if not isinstance(name, str) or not name:
                        self._detail(422, "name must be a non-empty string")
                        return
                    with server._lock:
                        if name in server.workspaces:
                            self._detail(409, "workspace already exists")
                            return
                        workspace = {"id": str(uuid.uuid4()), "name": name}
                        server.workspaces[name] = workspace
                    self._reply(201, workspace)
                    return
                if self.path == f"{_API}/users":
                    username = body.get("username")
                    role = body.get("role")
                    if not isinstance(username, str) or not username:
                        self._detail(422, "username must be a non-empty string")
                        return
                    if role != "annotator":
                        self._detail(422, "only the annotator role may be created")
                        return
                    with server._lock:
                        if any(u["username"] == username for u in server.users.values()):
                            self._detail(409, "username already exists")
                            return
                        user_id = str(uuid.uuid4())
                        user: dict[str, Any] = {
                            "id": user_id,
                            "username": username,
                            "role": role,
                            "api_key": f"argilla-fake-key-{uuid.uuid4().hex}",
                        }
                        server.users[user_id] = user
                    self._reply(201, user)
                    return
                if self.path.startswith(f"{_API}/workspaces/") and self.path.endswith("/users"):
                    workspace_id = self.path.split("/")[-2]
                    member_user_id = body.get("user_id")
                    if not isinstance(member_user_id, str) or not member_user_id:
                        self._detail(422, "user_id must be a non-empty string")
                        return
                    with server._lock:
                        if not any(w["id"] == workspace_id for w in server.workspaces.values()):
                            self._detail(404, "workspace not found")
                            return
                        if member_user_id not in server.users:
                            self._detail(404, "user not found")
                            return
                        if (workspace_id, member_user_id) in server.memberships:
                            self._detail(409, "user is already a member of this workspace")
                            return
                        server.memberships.add((workspace_id, member_user_id))
                    self._reply(201, {"id": str(uuid.uuid4())})
                    return
                self._detail(404, "not found")

            def do_DELETE(self) -> None:  # noqa: N802
                server.requests.append(("DELETE", self.path))
                if not server.healthy:
                    self._detail(503, "server stopped")
                    return
                parts = self.path.split("/")
                # ['', 'api', 'v1', 'workspaces', '{wid}', 'users', '{uid}']
                if len(parts) == 7 and parts[3] == "workspaces" and parts[5] == "users":
                    workspace_id, user_id = parts[4], parts[6]
                    with server._lock:
                        if (workspace_id, user_id) not in server.memberships:
                            self._detail(404, "not a member")
                            return
                        server.memberships.discard((workspace_id, user_id))
                    self._reply(200, {})
                    return
                if self.path.startswith(f"{_API}/users/"):
                    user_id = self.path.rsplit("/", 1)[-1]
                    with server._lock:
                        if user_id not in server.users:
                            self._detail(404, "user not found")
                            return
                        del server.users[user_id]
                    self._reply(200, {})
                    return
                self._detail(404, "not found")

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def client(
        self, *, api_key: str = "owner-provision-key", timeout: float = 5.0
    ) -> ArgillaClient:
        policy = ConnectionPolicy(
            name="fake-argilla",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({self.port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )
        return ArgillaClient(
            f"http://127.0.0.1:{self.port}", policy, api_key=api_key, timeout=timeout
        )

    def stop(self) -> None:
        self.healthy = False

    def start(self) -> None:
        self.healthy = True

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
