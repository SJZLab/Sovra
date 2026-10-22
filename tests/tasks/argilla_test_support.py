# mypy: disable-error-code="index,attr-defined,assignment,arg-type"
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
from urllib.parse import parse_qs, urlsplit

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
        # FTF-R006-P2 state: datasets, records and responses, plus an owner identity.
        self.owner_key = "owner-provision-key"
        self.datasets: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.responses: dict[str, dict[str, Any]] = {}
        # (method, path, api_key) of every dataset/record/response request, in arrival order.
        self.review_calls: list[tuple[str, str, str | None]] = []
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
                if server._review_route(self, "GET"):
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
                if server._review_route(self, "POST", body):
                    return
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

            def do_PUT(self) -> None:  # noqa: N802
                server.requests.append(("PUT", self.path))
                if not server.healthy:
                    self._detail(503, "server stopped")
                    return
                if server._review_route(self, "PUT", self._body()):
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
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------------ FTF-R006-P2 surface

    OWNER_USER_ID = "00000000-0000-4000-8000-0000000000aa"

    def _whoami(self, api_key: str | None) -> dict[str, Any] | None:
        if api_key is None:
            return None
        if api_key == self.owner_key:
            return {"id": self.OWNER_USER_ID, "username": "owner", "role": "owner"}
        for user in self.users.values():
            if user.get("api_key") == api_key:
                return user
        return None

    def _is_member(self, user: dict[str, Any], workspace_id: str) -> bool:
        return user["role"] in ("owner", "admin") or (workspace_id, user["id"]) in self.memberships

    @staticmethod
    def _record_view(
        record: dict[str, Any], responses: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        view = {k: v for k, v in record.items() if k != "responses"}
        if responses is not None:
            view["responses"] = responses
        return view

    def _responses_of(self, record_id: str) -> list[dict[str, Any]]:
        return [r for r in self.responses.values() if r["record_id"] == record_id]

    def _review_route(self, h: Any, method: str, body: dict[str, Any] | None = None) -> bool:
        """The pinned v2.8.0 dataset / record / response surface P2's adapter calls. Returns True
        when it answered. Authentication is by the real `X-Argilla-Api-Key` header: a missing or
        unknown key is 401, an annotator outside the dataset's workspace is 403."""
        parts = urlsplit(h.path)
        segments = [s for s in parts.path.split("/") if s]
        query = parse_qs(parts.query)
        if segments[:2] != ["api", "v1"]:
            return False
        route = segments[2:]
        review_roots = {"datasets", "records", "responses"}
        is_me_datasets = route[:2] == ["me", "datasets"]
        if not route or (route[0] not in review_roots and not is_me_datasets):
            return False
        key = h.headers.get("X-Argilla-Api-Key")
        self.review_calls.append((method, parts.path, key))
        user = self._whoami(key)
        if user is None:
            h._detail(401, "unauthorized")
            return True
        body = body or {}
        with self._lock:
            return self._review_locked(h, method, route, query, body, user)

    def _review_locked(
        self,
        h: Any,
        method: str,
        route: list[str],
        query: dict[str, list[str]],
        body: dict[str, Any],
        user: dict[str, Any],
    ) -> bool:
        is_admin = user["role"] in ("owner", "admin")
        # -- datasets
        if method == "GET" and route == ["me", "datasets"]:
            items = []
            for ds in self.datasets.values():
                if "workspace_id" in query and ds["workspace_id"] != query["workspace_id"][0]:
                    continue
                if "name" in query and ds["name"] != query["name"][0]:
                    continue
                if not self._is_member(user, ds["workspace_id"]):
                    continue
                items.append({k: ds[k] for k in ("id", "name", "status", "workspace_id")})
            h._reply(200, {"items": items})
            return True
        if method == "POST" and route == ["datasets"]:
            if not is_admin:
                h._detail(403, "forbidden")
                return True
            name, workspace_id = body.get("name"), body.get("workspace_id")
            if not isinstance(name, str) or not name or not isinstance(workspace_id, str):
                h._detail(422, "invalid dataset")
                return True
            if not any(w["id"] == workspace_id for w in self.workspaces.values()):
                h._detail(404, "workspace not found")
                return True
            if any(
                d["name"] == name and d["workspace_id"] == workspace_id
                for d in self.datasets.values()
            ):
                h._detail(409, "dataset already exists")
                return True
            dataset = {
                "id": str(uuid.uuid4()),
                "name": name,
                "workspace_id": workspace_id,
                "status": "draft",
                "fields": [],
                "questions": [],
            }
            self.datasets[dataset["id"]] = dataset
            h._reply(201, {k: dataset[k] for k in ("id", "name", "status", "workspace_id")})
            return True
        if route[:1] == ["datasets"] and len(route) >= 2:
            dataset = self.datasets.get(route[1])
            if dataset is None:
                h._detail(404, "dataset not found")
                return True
            tail = route[2:]
            if tail in (["fields"], ["questions"]):
                bucket = tail[0]
                if method == "GET":
                    if not self._is_member(user, dataset["workspace_id"]):
                        h._detail(403, "forbidden")
                        return True
                    h._reply(200, {"items": dataset[bucket]})
                    return True
                if method == "POST":
                    if not is_admin:
                        h._detail(403, "forbidden")
                        return True
                    if dataset["status"] != "draft":
                        h._detail(422, "a published dataset cannot be modified")
                        return True
                    entry_name = body.get("name")
                    settings = body.get("settings")
                    if not isinstance(entry_name, str) or not isinstance(settings, dict):
                        h._detail(422, "invalid settings")
                        return True
                    if any(e["name"] == entry_name for e in dataset[bucket]):
                        h._detail(409, "name already exists")
                        return True
                    dataset[bucket].append(
                        {
                            "id": str(uuid.uuid4()),
                            "name": entry_name,
                            "title": body.get("title"),
                            "required": bool(body.get("required")),
                            "settings": settings,
                        }
                    )
                    h._reply(201, dataset[bucket][-1])
                    return True
            if method == "PUT" and tail == ["publish"]:
                if not is_admin:
                    h._detail(403, "forbidden")
                    return True
                if not dataset["fields"] or not dataset["questions"]:
                    h._detail(422, "a dataset needs fields and questions to be published")
                    return True
                dataset["status"] = "ready"
                h._reply(200, {k: dataset[k] for k in ("id", "name", "status", "workspace_id")})
                return True
            if method == "POST" and tail == ["records", "bulk"]:
                if not is_admin:
                    h._detail(403, "forbidden")
                    return True
                if dataset["status"] != "ready":
                    h._detail(422, "dataset is not published")
                    return True
                items = body.get("items")
                if not isinstance(items, list) or not 1 <= len(items) <= 1000:
                    h._detail(422, "items must hold 1..1000 records")
                    return True
                field_names = {f["name"] for f in dataset["fields"]}
                external_ids = [i.get("external_id") for i in items if isinstance(i, dict)]
                if len(set(external_ids)) != len(external_ids):
                    h._detail(422, "External IDs must be unique")
                    return True
                existing = {
                    r.get("external_id")
                    for r in self.records.values()
                    if r["dataset_id"] == dataset["id"]
                }
                created = []
                for item in items:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("fields"), dict)
                        or set(item["fields"]) != field_names
                    ):
                        h._detail(422, "record fields do not match the dataset")
                        return True
                    if item.get("external_id") in existing:
                        # The pinned v2.8.0 `_validate_external_ids_are_not_present_in_db` is a
                        # 422 UnprocessableEntityError, not a 409.
                        h._detail(422, "found records with the same external ids in the dataset")
                        return True
                    created.append(item)
                out = []
                for item in created:
                    record = {
                        "id": str(uuid.uuid4()),
                        "dataset_id": dataset["id"],
                        "status": "pending",
                        "fields": item["fields"],
                        "metadata": item.get("metadata"),
                        "external_id": item.get("external_id"),
                    }
                    self.records[record["id"]] = record
                    out.append(self._record_view(record, None))
                h._reply(201, {"items": out})
                return True
            if method == "GET" and tail == ["records"]:
                if not is_admin:
                    h._detail(403, "forbidden")
                    return True
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["50"])[0])
                rows = [r for r in self.records.values() if r["dataset_id"] == dataset["id"]]
                page = rows[offset : offset + limit]
                with_responses = "responses" in query.get("include", [])
                h._reply(
                    200,
                    {
                        "items": [
                            self._record_view(
                                r, self._responses_of(r["id"]) if with_responses else None
                            )
                            for r in page
                        ],
                        "total": len(rows),
                    },
                )
                return True
            return False
        # -- records
        if route[:1] == ["records"] and len(route) >= 2:
            record = self.records.get(route[1])
            if record is None:
                h._detail(404, "record not found")
                return True
            dataset = self.datasets[record["dataset_id"]]
            if not self._is_member(user, dataset["workspace_id"]):
                h._detail(403, "forbidden")
                return True
            if method == "GET" and len(route) == 2:
                h._reply(200, self._record_view(record, self._responses_of(record["id"])))
                return True
            if method == "POST" and route[2:] == ["responses"]:
                values, status = body.get("values"), body.get("status")
                problem = self._response_problem(dataset, values, status)
                if problem:
                    h._detail(422, problem)
                    return True
                if any(
                    r["record_id"] == record["id"] and r["user_id"] == user["id"]
                    for r in self.responses.values()
                ):
                    h._detail(409, "user already has a response for this record")
                    return True
                response = {
                    "id": str(uuid.uuid4()),
                    "record_id": record["id"],
                    "user_id": user["id"],
                    "status": status,
                    "values": values,
                }
                self.responses[response["id"]] = response
                h._reply(201, response)
                return True
            return False
        # -- responses
        if route[:1] == ["responses"] and len(route) == 2 and method == "PUT":
            response = self.responses.get(route[1])
            if response is None:
                h._detail(404, "response not found")
                return True
            if response["user_id"] != user["id"]:
                h._detail(403, "only the author can change a response")
                return True
            dataset = self.datasets[self.records[response["record_id"]]["dataset_id"]]
            values, status = body.get("values"), body.get("status")
            problem = self._response_problem(dataset, values, status)
            if problem:
                h._detail(422, problem)
                return True
            response["values"], response["status"] = values, status
            h._reply(200, response)
            return True
        return False

    @staticmethod
    def _response_problem(dataset: dict[str, Any], values: Any, status: Any) -> str | None:
        if status not in ("submitted", "draft", "discarded"):
            return "invalid status"
        if not isinstance(values, dict):
            return "values are required"
        names = {q["name"] for q in dataset["questions"]}
        if not set(values) <= names:
            return "unknown question"
        for name, entry in values.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
                return "invalid response value"
            question = next(q for q in dataset["questions"] if q["name"] == name)
            settings = question["settings"]
            if settings.get("type") == "label_selection" and entry["value"] not in {
                o["value"] for o in settings.get("options", [])
            }:
                return "value is not an option"
            if settings.get("type") == "text" and not entry["value"]:
                return "empty text value"
        required = [q["name"] for q in dataset["questions"] if q.get("required")]
        if status == "submitted" and any(name not in values for name in required):
            return "a required question is missing"
        return None

    # -- test hooks: what an Argilla-side actor could do behind the Factory's back
    def edit_record_fields(self, record_id: str, field: str, value: str) -> None:
        with self._lock:
            self.records[record_id]["fields"][field] = value

    def edit_record_metadata(self, record_id: str, key: str, value: Any) -> None:
        with self._lock:
            self.records[record_id]["metadata"][key] = value

    def delete_record(self, record_id: str) -> None:
        with self._lock:
            self.records.pop(record_id, None)

    def inject_response(
        self,
        record_id: str,
        user_id: str,
        decision: str,
        rationale: str = "",
        status: str = "submitted",
    ) -> str:
        values: dict[str, Any] = {"decision": {"value": decision}}
        if rationale:
            values["rationale"] = {"value": rationale}
        response_id = str(uuid.uuid4())
        with self._lock:
            self.responses[response_id] = {
                "id": response_id,
                "record_id": record_id,
                "user_id": user_id,
                "status": status,
                "values": values,
            }
        return response_id

    def delete_response(self, response_id: str) -> None:
        with self._lock:
            self.responses.pop(response_id, None)

    def holds_bytes(self, needle: bytes) -> bool:
        """True if `needle` appears anywhere in the server's dataset / record / response state."""
        with self._lock:
            blob = json.dumps(
                [self.datasets, self.records, self.responses], sort_keys=True, default=str
            ).encode()
        return needle in blob

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
