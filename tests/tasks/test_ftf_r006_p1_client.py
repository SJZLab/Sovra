#!/usr/bin/env python3
"""FTF-R006-P1 -- Argilla REST adapter (no-network unit/fault tests).

Runs without an Argilla server, with a loopback HTTP stub scripted to the
real argilla-server v2.8.0 response shapes (route paths, the
`X-Argilla-Api-Key` header, `{"detail": "<message>"}` / FastAPI validation
`{"detail": [...]}}` error bodies -- verified against the pinned upstream
source, not guessed; see `packages/integrations/argilla/client.py`'s own
module docstring for the exact files read).

This is **only** the `client.py` slice of FTF-R006-P1 (QUEUE.md Backlog,
Control-plane design 2026-10-15T14:14Z). `argilla_identity_store.py`, the
`00NN_argilla_review_identity` migration, the real Argilla+OpenSearch+
Redis+Postgres compose stack and `tests/integration/
test_argilla_identity_scope.py` are **not** built yet -- disclosed, not
silently dropped; see this round's submission file for the explicit
follow-up list. No production caller exists yet (`proving_symbol` blank,
same FTF-R005 UNWIRED precedent), so there is no fault->RED table for a
*feature* here, only for this client module's own wire contract:

R006P1-C  client.py wire behaviour: the real route paths and header name,
          strict JSON, error-class mapping (404/409/5xx/other),
          identifier-shaped input validation before any request is sent,
          no redirects, bounded pagination, and that the caller's own API
          key is never echoed back in an error message.

Run directly: python3 tests/tasks/test_ftf_r006_p1_client.py
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.integrations.argilla import client as client_mod  # noqa: E402
from packages.integrations.argilla.client import (  # noqa: E402
    API_KEY_HEADER_NAME,
    MAX_PAGE_ITEMS,
    MAX_RESPONSE_BYTES,
    ArgillaClient,
    ArgillaConflictError,
    ArgillaNotFoundError,
    ArgillaRejectedError,
    ArgillaTransport,
    ArgillaUnavailableError,
    parse_strict_json,
)
from packages.security import outbound_guard as guard_mod  # noqa: E402
from packages.security.outbound_guard import OutboundGuardError, guarded_stream_fetch  # noqa: E402
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def raises(exc: type[BaseException], fn: Any, *args: Any, **kwargs: Any) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception:
        return False
    return False


# -- loopback HTTP stub -----------------------------------------------------


class Stub:
    def __init__(self) -> None:
        self.script: list[tuple[int, bytes, dict[str, str]]] = []
        self.seen: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                stub.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "api_key": self.headers.get(API_KEY_HEADER_NAME),
                        "auth": self.headers.get("Authorization"),
                        "body": self.rfile.read(length) if length else b"",
                    }
                )
                status, body, headers = stub.script.pop(0) if stub.script else (200, b"{}", {})
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = do_PATCH = _serve

            def log_message(self, *args: Any) -> None:
                pass

        class CountingServer(ThreadingHTTPServer):
            def get_request(self) -> Any:
                stub.connections += 1
                return super().get_request()

        self.connections = 0
        self.server = CountingServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self, **kw: Any) -> ArgillaClient:
        policy = ConnectionPolicy(
            name="stub",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({self.port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )
        return ArgillaClient(f"http://127.0.0.1:{self.port}", policy, **kw)

    def reply(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.script.append((status, raw, headers or {}))


WORKSPACE = {"id": "11111111-1111-1111-1111-111111111111", "name": "sovra-abc123"}
USER = {
    "id": "22222222-2222-2222-2222-222222222222",
    "username": "sovra-annotator-1",
    "role": "annotator",
    "api_key": "argilla.secret.key",
}


def test_wire_happy_path() -> None:
    stub = Stub()
    c = stub.client(api_key="provision-key")
    stub.reply(200, {"version": "2.8.0", "search_engine": "opensearch", "memory": {}})
    status = c.get_status()
    check("R006P1-C", "get_status returns the version", status.get("version") == "2.8.0")
    check(
        "R006P1-C",
        "GET /status hits the real v1 path",
        stub.seen[-1]["path"] == "/api/v1/status",
    )
    check(
        "R006P1-C",
        "API key sent on the documented header, not Authorization",
        stub.seen[-1]["api_key"] == "provision-key" and stub.seen[-1]["auth"] is None,
    )

    stub.reply(201, dict(WORKSPACE))
    created = c.create_workspace("sovra-abc123")
    check("R006P1-C", "create_workspace returns id+name", created.id == WORKSPACE["id"])
    check(
        "R006P1-C",
        "POST /workspaces hits the real v1 path",
        stub.seen[-1]["path"] == "/api/v1/workspaces",
    )
    check(
        "R006P1-C",
        "create_workspace body carries only name",
        json.loads(stub.seen[-1]["body"]) == {"name": "sovra-abc123"},
    )

    stub.reply(200, {"items": [WORKSPACE]})
    listed = c.list_workspaces_me()
    check("R006P1-C", "list_workspaces_me parses items", listed == [created])
    check(
        "R006P1-C",
        "GET /me/workspaces hits the real v1 path",
        stub.seen[-1]["path"] == "/api/v1/me/workspaces",
    )

    stub.reply(201, dict(USER))
    user = c.create_user(
        username="sovra-annotator-1", role="annotator", password="correct-horse-battery"
    )
    check("R006P1-C", "create_user returns the plaintext api_key", user.api_key == USER["api_key"])
    body = json.loads(stub.seen[-1]["body"])
    check(
        "R006P1-C",
        "create_user body is exactly the documented fields",
        set(body) == {"first_name", "username", "role", "password"} and body["role"] == "annotator",
    )

    stub.reply(200, dict(USER))
    fetched = c.get_user(USER["id"])
    check("R006P1-C", "get_user round-trips", fetched == user)
    check(
        "R006P1-C",
        "GET /users/{id} hits the real v1 path",
        stub.seen[-1]["path"] == f"/api/v1/users/{USER['id']}",
    )

    stub.reply(201, dict(USER))
    c.add_workspace_user(WORKSPACE["id"], USER["id"])
    check(
        "R006P1-C",
        "add_workspace_user posts {user_id}",
        json.loads(stub.seen[-1]["body"]) == {"user_id": USER["id"]}
        and stub.seen[-1]["path"] == f"/api/v1/workspaces/{WORKSPACE['id']}/users",
    )

    stub.reply(200, {"items": [USER]})
    members = c.list_workspace_users(WORKSPACE["id"])
    check("R006P1-C", "list_workspace_users parses items", members == [user])

    stub.reply(200, dict(USER))
    c.remove_workspace_user(WORKSPACE["id"], USER["id"])
    check(
        "R006P1-C",
        "remove_workspace_user DELETEs the member path",
        stub.seen[-1]["method"] == "DELETE"
        and stub.seen[-1]["path"] == f"/api/v1/workspaces/{WORKSPACE['id']}/users/{USER['id']}",
    )

    stub.reply(200, dict(USER))
    c.delete_user(USER["id"])
    check(
        "R006P1-C",
        "delete_user DELETEs /users/{id}",
        stub.seen[-1]["method"] == "DELETE"
        and stub.seen[-1]["path"] == f"/api/v1/users/{USER['id']}",
    )
    stub.server.shutdown()


def test_ensure_workspace_idempotent_and_race() -> None:
    stub = Stub()
    c = stub.client()
    # already exists -> no create call
    stub.reply(200, {"items": [WORKSPACE]})
    found = c.ensure_workspace("sovra-abc123")
    n = len(stub.seen)
    check(
        "R006P1-C",
        "ensure_workspace finds an existing one without creating",
        found.id == WORKSPACE["id"],
    )
    check("R006P1-C", "no create call when already found", n == 1)

    # not present -> created
    stub.reply(200, {"items": []})
    stub.reply(201, dict(WORKSPACE))
    created = c.ensure_workspace("sovra-abc123")
    check("R006P1-C", "ensure_workspace creates when absent", created.id == WORKSPACE["id"])

    # create races another caller -> conflict -> re-list finds it
    stub.reply(200, {"items": []})
    stub.reply(409, {"detail": "Workspace name `sovra-abc123` is not unique"})
    stub.reply(200, {"items": [WORKSPACE]})
    raced = c.ensure_workspace("sovra-abc123")
    check("R006P1-C", "create-race conflict resolves via re-list", raced.id == WORKSPACE["id"])

    # create races, but the winner's row is nowhere to be found -> rejected, not silently empty
    stub.reply(200, {"items": []})
    stub.reply(409, {"detail": "Workspace name `sovra-abc123` is not unique"})
    stub.reply(200, {"items": []})
    check(
        "R006P1-C",
        "unresolved create-race is a rejection, never a fabricated workspace",
        raises(ArgillaRejectedError, c.ensure_workspace, "sovra-abc123"),
    )

    # exact, case-sensitive name match on the initial list -- a differently-cased
    # existing workspace is not a match, so a new one is created.
    different_case = {"id": "33333333-3333-3333-3333-333333333333", "name": "SOVRA-ABC123"}
    stub.reply(200, {"items": [WORKSPACE]})
    stub.reply(201, dict(different_case))
    distinct = c.ensure_workspace("SOVRA-ABC123")
    check(
        "R006P1-C",
        "ensure_workspace is case-sensitive on the initial list (creates, does not reuse)",
        distinct.id == different_case["id"],
    )

    # exact, case-sensitive name match on the re-list-after-conflict path too.
    stub.reply(200, {"items": []})
    stub.reply(409, {"detail": "Workspace name `SOVRA-ABC123` is not unique"})
    stub.reply(200, {"items": [WORKSPACE]})  # only the lower-case one exists
    check(
        "R006P1-C",
        "ensure_workspace is case-sensitive on the post-conflict re-list too",
        raises(ArgillaRejectedError, c.ensure_workspace, "SOVRA-ABC123"),
    )

    # one-sided proof against a `workspace.name -> workspace.name.strip().lower()`
    # mutant specifically: a *stored* name with whitespace/case that would only
    # collide with the lowercase, unpadded target name after normalization (a
    # same-case/no-whitespace target, as `different_case` above used, can never
    # distinguish which side of the comparison a normalizer was added to).
    # `_require_workspace_name` only ever validates this client's own input, so
    # a server-returned record's own name is free to carry whatever the fake
    # server sends.
    mangled = {"id": "44444444-4444-4444-4444-444444444444", "name": " Foo "}
    fresh = {"id": "55555555-5555-5555-5555-555555555555", "name": "foo"}
    stub.reply(200, {"items": [mangled]})
    stub.reply(201, dict(fresh))
    not_reused = c.ensure_workspace("foo")
    check(
        "R006P1-C",
        "a padded/differently-cased stored name does not match on the initial list",
        not_reused.id == fresh["id"],
    )
    stub.reply(200, {"items": []})
    stub.reply(409, {"detail": "Workspace name `foo` is not unique"})
    stub.reply(200, {"items": [mangled]})
    check(
        "R006P1-C",
        "a padded/differently-cased stored name does not match on the post-conflict re-list",
        raises(ArgillaRejectedError, c.ensure_workspace, "foo"),
    )
    stub.server.shutdown()


def test_error_mapping() -> None:
    stub = Stub()
    c = stub.client()
    stub.reply(500, b"")
    check("R006P1-C", "5xx is unavailable", raises(ArgillaUnavailableError, c.get_user, USER["id"]))
    stub.reply(503, b"")
    check("R006P1-C", "503 is unavailable", raises(ArgillaUnavailableError, c.get_user, USER["id"]))
    stub.reply(404, {"detail": "User with id `x` not found"})
    check("R006P1-C", "404 is NotFound", raises(ArgillaNotFoundError, c.get_user, USER["id"]))
    stub.reply(409, {"detail": "User username `x` is not unique"})
    check(
        "R006P1-C",
        "409 is Conflict",
        raises(
            ArgillaConflictError, c.create_user, username="x", role="annotator", password="aaaaaaaa"
        ),
    )
    # FastAPI's own validation-error shape (a list of {loc,msg,type}), not the
    # plain-string future-error shape -- both must be handled, neither trusted
    # as more than a classification.
    stub.reply(
        422, {"detail": [{"loc": ["body", "role"], "msg": "not a valid role", "type": "enum"}]}
    )
    check(
        "R006P1-C",
        "422 with FastAPI list-shaped detail is rejected, not crashed on",
        raises(
            ArgillaRejectedError, c.create_user, username="x", role="annotator", password="aaaaaaaa"
        ),
    )
    stub.reply(401, {"detail": "Could not validate credentials"})
    check(
        "R006P1-C",
        "401 is rejected, not unavailable",
        raises(ArgillaRejectedError, c.get_user, USER["id"]),
    )
    stub.reply(400, b"not json")
    check(
        "R006P1-C",
        "non-JSON error body is still a clean rejection",
        raises(ArgillaRejectedError, c.get_user, USER["id"]),
    )
    # get_status has no downstream field validation of its own, so this is the
    # one call that would silently "succeed" with the error body as data if the
    # generic non-404/409/5xx rejection branch were ever skipped.
    stub.reply(422, {"detail": "schema validation failed"})
    check(
        "R006P1-C",
        "a 422 on get_status is rejected, never returned as a parsed success value",
        raises(ArgillaRejectedError, c.get_status),
    )
    stub.server.shutdown()


def test_malformed_response_bodies() -> None:
    stub = Stub()
    c = stub.client()
    stub.reply(200, b'{"id": "x", "id": "y"}')
    check(
        "R006P1-C", "duplicate key rejected", raises(ArgillaRejectedError, c.get_user, USER["id"])
    )
    stub.reply(200, b'{"id": NaN}')
    check("R006P1-C", "NaN rejected", raises(ArgillaRejectedError, c.get_user, USER["id"]))
    stub.reply(200, b"[]")
    check(
        "R006P1-C",
        "non-object top level rejected",
        raises(ArgillaRejectedError, c.get_user, USER["id"]),
    )
    stub.reply(200, {"id": USER["id"], "username": "x"})  # missing role/api_key
    check(
        "R006P1-C",
        "missing required field rejected",
        raises(ArgillaRejectedError, c.get_user, USER["id"]),
    )
    stub.reply(200, {"items": "not-a-list"})
    check("R006P1-C", "non-list items rejected", raises(ArgillaRejectedError, c.list_workspaces_me))
    stub.reply(200, {"nothing": 1})
    check(
        "R006P1-C", "missing items key rejected", raises(ArgillaRejectedError, c.list_workspaces_me)
    )
    stub.reply(200, {"items": "not-a-list"})
    check(
        "R006P1-C",
        "non-list items rejected for a user list too",
        raises(ArgillaRejectedError, c.list_workspace_users, WORKSPACE["id"]),
    )
    # workspace payloads have their own required-field/type checks, exercised
    # through create_workspace (list_workspaces_me's own malformed-items path
    # above never reaches _workspace_of with a bad single record).
    stub.reply(201, {"id": WORKSPACE["id"]})  # missing name
    check(
        "R006P1-C",
        "a workspace missing its name is rejected",
        raises(ArgillaRejectedError, c.create_workspace, "sovra-abc123"),
    )
    stub.reply(201, {"id": 123, "name": "sovra-abc123"})  # non-string id
    check(
        "R006P1-C",
        "a workspace with a non-string id is rejected",
        raises(ArgillaRejectedError, c.create_workspace, "sovra-abc123"),
    )
    stub.reply(201, {"id": WORKSPACE["id"], "name": ""})  # empty name
    check(
        "R006P1-C",
        "a workspace with an empty name is rejected",
        raises(ArgillaRejectedError, c.create_workspace, "sovra-abc123"),
    )
    stub.server.shutdown()


def test_no_redirects_and_outage() -> None:
    stub = Stub()
    c = stub.client()
    n = len(stub.seen)
    stub.reply(302, b"", {"Location": f"http://127.0.0.1:{stub.port}/elsewhere"})
    check(
        "R006P1-C",
        "redirect not followed (one request only, ends in a rejection)",
        raises(ArgillaRejectedError, c.get_user, USER["id"]) and len(stub.seen) == n + 1,
    )
    dead = Stub()
    dead_client = dead.client()
    dead.server.shutdown()
    dead.server.server_close()
    check(
        "R006P1-C",
        "refused connection is unavailable, never a fabricated success",
        raises(ArgillaUnavailableError, dead_client.get_user, USER["id"]),
    )
    denied = ArgillaClient(
        f"http://127.0.0.1:{stub.port}",
        ConnectionPolicy(
            name="deny",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({stub.port + 1}),
        ),
    )
    check(
        "R006P1-C",
        "policy-denied destination is a rejection, not an outage",
        raises(ArgillaRejectedError, denied.get_user, USER["id"]),
    )
    stub.server.shutdown()


def test_input_validation_before_any_request() -> None:
    stub = Stub()
    c = stub.client()
    n = len(stub.seen)
    for bad_role in ("owner ", "ADMIN", "root", "", 5, None):
        check(
            "R006P1-C",
            f"bad role {bad_role!r} refused before any request",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username="ok",
                role=bad_role,
                password="aaaaaaaa",
            ),
        )
    for bad_user in ("", "has space", "a" * 65, "üñîçødé", None):
        check(
            "R006P1-C",
            f"bad username {bad_user!r} refused before any request",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username=bad_user,
                role="annotator",
                password="aaaaaaaa",
            ),
        )
    for bad_pw in ("short", "x" * 101, 12345678):
        check(
            "R006P1-C",
            f"bad password {bad_pw!r} refused before any request",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username="ok",
                role="annotator",
                password=bad_pw,
            ),
        )
    for bad_id in ("not-a-uuid", "", "11111111-1111-1111-1111-11111111111", None):
        check(
            "R006P1-C",
            f"bad uuid {bad_id!r} refused before any request",
            raises(ArgillaRejectedError, c.get_user, bad_id),
        )
    for bad_ws in ("", "has space", "a" * 65, None):
        check(
            "R006P1-C",
            f"bad workspace name {bad_ws!r} refused before any request",
            raises(ArgillaRejectedError, c.create_workspace, bad_ws),
        )
        check(
            "R006P1-C",
            f"bad workspace name {bad_ws!r} refused by ensure_workspace before any request",
            raises(ArgillaRejectedError, c.ensure_workspace, bad_ws),
        )
    bad_id = "not-a-uuid"
    good_id = WORKSPACE["id"]
    for label, fn, args in (
        ("list_workspace_users", c.list_workspace_users, (bad_id,)),
        ("add_workspace_user workspace_id", c.add_workspace_user, (bad_id, good_id)),
        ("add_workspace_user user_id", c.add_workspace_user, (good_id, bad_id)),
        ("remove_workspace_user workspace_id", c.remove_workspace_user, (bad_id, good_id)),
        ("remove_workspace_user user_id", c.remove_workspace_user, (good_id, bad_id)),
        ("delete_user", c.delete_user, (bad_id,)),
    ):
        check(
            "R006P1-C",
            f"{label} refuses a non-uuid id before any request",
            raises(ArgillaRejectedError, fn, *args),
        )
    check("R006P1-C", "no request was ever sent for any invalid input above", len(stub.seen) == n)
    stub.server.shutdown()


def test_api_key_never_echoed_and_control_chars_refused() -> None:
    stub = Stub()
    c = stub.client(api_key="super-secret-key")
    n = len(stub.seen)
    stub.reply(403, {"detail": "forbidden, key super-secret-key is not authorized"})
    try:
        c.get_user(USER["id"])
        check("R006P1-C", "403 should have raised", False)
    except ArgillaRejectedError as exc:
        text = str(exc)
        check(
            "R006P1-C",
            "the api key is redacted out of the raised error",
            "super-secret-key" not in text,
        )
        # pinned so a `_scrub` mutant that swallows the message (e.g. returning
        # None unconditionally) cannot hide behind the key-absence check alone.
        check(
            "R006P1-C",
            "the raised error still carries the server's own (redacted) detail text",
            "forbidden" in text and "not authorized" in text,
        )

    bad = stub.client(api_key="tok\r\nX-Evil: 1")
    check(
        "R006P1-C",
        "a control character in the api key is refused before any header is built",
        raises(ArgillaRejectedError, bad.get_user, USER["id"]) and len(stub.seen) == n + 1,
    )
    # The explicit check is distinguishable from the generic ValueError
    # fallback: its own static message, and no TCP connection at all (the
    # fallback only fires after http.client has already connected).
    conns = stub.connections
    try:
        bad.get_user(USER["id"])
    except ArgillaRejectedError as exc:
        check(
            "R006P1-C",
            "the control-character refusal carries its own static, key-free message",
            str(exc) == "API key contains control characters",
        )
    check(
        "R006P1-C",
        "a control-character api key opens no connection to the server",
        stub.connections == conns,
    )
    stub.server.shutdown()


def test_base_url_and_timeout_validation() -> None:
    pol = ConnectionPolicy(name="p")
    for bad in ("https://h/path", "https://h?q=1", "https://u@h", "https://h#f", "https://h/ "):
        check("R006P1-C", f"base_url {bad!r} refused", raises(ValueError, ArgillaClient, bad, pol))
    for bad_t in (0, -1, True, "5"):
        check(
            "R006P1-C",
            f"timeout {bad_t!r} refused",
            raises(ValueError, ArgillaClient, "https://h", pol, timeout=bad_t),
        )


def test_password_length_boundary() -> None:
    """8..100 chars inclusive -- the upper bound needs a *passing* case at
    exactly 100, not only a failing one at 101 (a `<=` -> `<` mutant on the
    upper bound only shows up as a false rejection of the valid boundary)."""
    stub = Stub()
    c = stub.client()
    stub.reply(201, dict(USER))
    check(
        "R006P1-C",
        "a password of exactly 100 characters is accepted",
        c.create_user(username="ok", role="annotator", password="x" * 100).id == USER["id"],
    )
    stub.reply(201, dict(USER))
    check(
        "R006P1-C",
        "a password of exactly 8 characters is accepted",
        c.create_user(username="ok", role="annotator", password="x" * 8).id == USER["id"],
    )
    stub.server.shutdown()


def test_page_item_bound() -> None:
    """MAX_PAGE_ITEMS is a `>` bound: exactly at the limit passes, one over
    is rejected -- both directions, for both list helpers. Minimal per-item
    payloads so MAX_PAGE_ITEMS+1 of them still stays well under
    MAX_RESPONSE_BYTES -- only the item-count check should ever fire here."""
    mini_ws = {"id": WORKSPACE["id"], "name": "n"}
    mini_user = {"id": USER["id"], "username": "n", "role": "owner", "api_key": "k"}
    check(
        "R006P1-C",
        "the minimal fixtures stay well under the response-size bound at MAX_PAGE_ITEMS+1",
        len(json.dumps({"items": [mini_user] * (MAX_PAGE_ITEMS + 1)}).encode())
        < MAX_RESPONSE_BYTES,
    )
    at_limit = [dict(mini_ws) for _ in range(MAX_PAGE_ITEMS)]
    over_limit = [dict(mini_ws) for _ in range(MAX_PAGE_ITEMS + 1)]
    stub = Stub()
    c = stub.client()
    stub.reply(200, {"items": at_limit})
    check(
        "R006P1-C",
        "exactly MAX_PAGE_ITEMS workspaces is accepted",
        len(c.list_workspaces_me()) == MAX_PAGE_ITEMS,
    )
    stub.reply(200, {"items": over_limit})
    check(
        "R006P1-C",
        "one over MAX_PAGE_ITEMS workspaces is rejected",
        raises(ArgillaRejectedError, c.list_workspaces_me),
    )
    at_limit_users = [dict(mini_user) for _ in range(MAX_PAGE_ITEMS)]
    over_limit_users = [dict(mini_user) for _ in range(MAX_PAGE_ITEMS + 1)]
    stub.reply(200, {"items": at_limit_users})
    check(
        "R006P1-C",
        "exactly MAX_PAGE_ITEMS workspace users is accepted",
        len(c.list_workspace_users(WORKSPACE["id"])) == MAX_PAGE_ITEMS,
    )
    stub.reply(200, {"items": over_limit_users})
    check(
        "R006P1-C",
        "one over MAX_PAGE_ITEMS workspace users is rejected",
        raises(ArgillaRejectedError, c.list_workspace_users, WORKSPACE["id"]),
    )
    stub.server.shutdown()


def test_strict_json_helper_directly() -> None:
    check(
        "R006P1-C", "strict JSON parses a plain object", parse_strict_json(b'{"a": 1}') == {"a": 1}
    )
    check(
        "R006P1-C",
        "oversized body rejected",
        raises(ArgillaRejectedError, parse_strict_json, b"x" * (2 * 1024 * 1024)),
    )
    check(
        "R006P1-C",
        "non-utf8 body rejected",
        raises(ArgillaRejectedError, parse_strict_json, b"\xff\xfe"),
    )
    # exact size boundary, both sides -- a valid object padded to exactly
    # MAX_RESPONSE_BYTES must parse; one byte over must raise. Both bodies are
    # otherwise well-formed JSON, so only the size check itself can be why one
    # succeeds and the other fails.
    pad_key_at = MAX_RESPONSE_BYTES - len(b'{"a": ""}')
    at_bound = b'{"a": "' + b"x" * pad_key_at + b'"}'
    over_bound = b'{"a": "' + b"x" * (pad_key_at + 1) + b'"}'
    check(
        "R006P1-C", "body exactly at MAX_RESPONSE_BYTES parses", len(at_bound) == MAX_RESPONSE_BYTES
    )
    check(
        "R006P1-C",
        "a well-formed body exactly at the size bound is accepted",
        parse_strict_json(at_bound) == {"a": "x" * pad_key_at},
    )
    check(
        "R006P1-C",
        "a well-formed body one byte over the size bound is rejected, not parsed as success",
        raises(ArgillaRejectedError, parse_strict_json, over_bound),
    )
    # duplicate-key detection, direct and not masked by a downstream
    # required-field check: a payload that would otherwise parse cleanly.
    check(
        "R006P1-C",
        "a literal duplicate key is rejected",
        raises(ArgillaRejectedError, parse_strict_json, b'{"a": 1, "a": 2}'),
    )
    check(
        "R006P1-C",
        "two keys that differ only by case/whitespace are NOT a duplicate",
        parse_strict_json(b'{"a": 1, "A": 2, " a": 3}') == {"a": 1, "A": 2, " a": 3},
    )
    # non-finite constants, direct: a value that would otherwise be accepted.
    check(
        "R006P1-C",
        "a bare NaN value is rejected, not silently parsed as None/other",
        raises(ArgillaRejectedError, parse_strict_json, b'{"a": 1, "b": NaN}'),
    )
    check(
        "R006P1-C",
        "a bare Infinity value is rejected",
        raises(ArgillaRejectedError, parse_strict_json, b'{"a": Infinity}'),
    )


def test_error_detail_text_directly() -> None:
    f = client_mod._error_detail_text
    check("R006P1-C", "plain string detail is returned verbatim", f({"detail": "boom"}) == "boom")
    check(
        "R006P1-C",
        "FastAPI validation-list detail is joined from each item's msg",
        f({"detail": [{"msg": "a"}, {"msg": "b"}]}) == "a; b",
    )
    check(
        "R006P1-C",
        "a list item with a non-string msg is excluded, not stringified",
        f({"detail": [{"msg": 123}]}) == "",
    )
    check(
        "R006P1-C",
        "a list item missing msg entirely is excluded",
        f({"detail": [{"loc": ["body"], "type": "x"}]}) == "",
    )
    check("R006P1-C", "a missing detail key gives an empty string", f({}) == "")
    check(
        "R006P1-C",
        "a detail of the wrong type (neither str nor list) gives an empty string",
        f({"detail": 5}) == "",
    )


# -- round 2 (Control-plane return 2026-10-16): raw-socket stub ---------------


class RawServer:
    """A loopback TCP server that speaks bytes, not HTTP, so a test can send
    what a hostile or broken server would (bad status line, short body,
    endless body, byte-at-a-time drip)."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.sent = 0
        self.connections = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5)
            buf = b""
            while b"\r\n\r\n" not in buf:
                data = conn.recv(65536)
                if not data:
                    return
                buf += data
            self.handler(self, conn)
        except OSError:
            pass
        finally:
            conn.close()

    def client(self, **kw: Any) -> ArgillaClient:
        policy = ConnectionPolicy(
            name="raw",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({self.port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )
        return ArgillaClient(f"http://127.0.0.1:{self.port}", policy, **kw)

    def close(self) -> None:
        self.sock.close()


def _no_key(exc_text: str, key: str) -> bool:
    return key not in exc_text


def _message_of(fn: Any, *args: Any, **kwargs: Any) -> tuple[type | None, str]:
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        return type(exc), str(exc) + repr(exc.__cause__) + repr(exc.__context__)
    return None, ""


def test_r2_create_user_annotator_only() -> None:
    stub = Stub()
    c = stub.client()
    for role in ("admin", "owner"):
        n = len(stub.seen)
        check(
            "R006P1a-1",
            f"create_user(role={role!r}) is refused before any request is sent",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username="sovra-a",
                role=role,
                password="correct-horse-battery",
            )
            and len(stub.seen) == n,
        )
    for bad in ("admin", "owner", "Annotator", "ANNOTATOR", " annotator", "annotator "):
        stub.reply(201, {**USER, "role": bad})
        check(
            "R006P1a-1",
            f"a create response whose role is {bad!r} is rejected, not returned",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username=USER["username"],
                role="annotator",
                password="correct-horse-battery",
            ),
        )
    stub.server.shutdown()


def test_r2_http_client_exceptions_classified() -> None:
    key = "argilla.very.secret.key"

    def bad_status(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(f"NOT-HTTP {key}\r\n\r\n".encode())

    def short_body(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n{key}".encode())

    def valid_prefix(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5000\r\n\r\n" + json.dumps(USER).encode())

    def long_line(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nX-Pad: " + b"a" * 70_000 + b"\r\n\r\n")

    def many_headers(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\n" + b"X-H: 1\r\n" * 150 + b"\r\n")

    def informational(code: int) -> Any:
        def handler(_s: RawServer, conn: socket.socket) -> None:
            body = json.dumps(USER).encode()
            conn.sendall(
                f"HTTP/1.1 {code} Info\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
            )

        return handler

    cases: list[tuple[str, Any]] = [
        ("garbage status line (BadStatusLine)", bad_status),
        ("truncated body (IncompleteRead.partial carries the key)", short_body),
        ("valid-looking JSON cut short of its Content-Length", valid_prefix),
        ("over-long header line (LineTooLong)", long_line),
        ("more than 100 headers", many_headers),
        ("HTTP 101", informational(101)),
        ("HTTP 199", informational(199)),
    ]
    for label, handler in cases:
        server = RawServer(handler)
        c = server.client(api_key=key, timeout=2)
        kind, text = _message_of(c.get_user, USER["id"])
        check(
            "R006P1a-2",
            f"{label} is ArgillaUnavailableError, not a raw http.client exception",
            kind is ArgillaUnavailableError,
        )
        check("R006P1a-2", f"{label}: the message never carries the key", _no_key(text, key))
        server.close()


def test_r2_bounded_body_and_total_deadline() -> None:
    def endless(srv: RawServer, conn: socket.socket) -> None:
        total = 96 * 1024 * 1024
        conn.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {total}\r\n\r\n".encode())
        block = b"x" * 65536
        while srv.sent < total:
            conn.sendall(block)
            srv.sent += len(block)

    server = RawServer(endless)
    c = server.client(timeout=5)
    started = time.monotonic()
    kind, _ = _message_of(c.get_status)
    check("R006P1a-3", "a 96 MiB body is rejected", kind is ArgillaRejectedError)
    time.sleep(0.3)
    check(
        "R006P1a-3",
        "the client stops reading at the bound (the server could push only a few MiB)",
        server.sent < MAX_RESPONSE_BYTES + 16 * 1024 * 1024 and time.monotonic() - started < 4,
    )
    server.close()

    def drip(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
        for _ in range(1000):
            conn.sendall(b" ")
            time.sleep(0.1)

    server = RawServer(drip)
    c = server.client(timeout=0.5)
    started = time.monotonic()
    kind, _ = _message_of(c.get_status)
    elapsed = time.monotonic() - started
    check("R006P1a-3", "a byte-at-a-time server is Unavailable", kind is ArgillaUnavailableError)
    check(
        "R006P1a-3",
        f"the total per-call deadline holds (took {elapsed:.2f}s at timeout=0.5s)",
        elapsed < 0.9,
    )
    server.close()

    policy = ConnectionPolicy(name="p")
    for bad in (float("nan"), float("inf"), float("-inf")):
        check(
            "R006P1a-3",
            f"timeout {bad} refused by the client and the transport",
            raises(ValueError, ArgillaClient, "https://h", policy, timeout=bad)
            and raises(ValueError, ArgillaTransport, "https://h", policy, timeout=bad),
        )


def test_r2_strict_response_schema() -> None:
    stub = Stub()
    c = stub.client()
    no_key = {k: v for k, v in USER.items() if k != "api_key"}
    for label, payload in (
        ("missing api_key", no_key),
        ("empty api_key", {**USER, "api_key": ""}),
        ("non-UUID id", {**USER, "id": "not-a-uuid"}),
        ("unknown role", {**USER, "role": "root"}),
        ("empty username", {**USER, "username": ""}),
    ):
        stub.reply(200, payload)
        check(
            "R006P1a-4",
            f"get_user: {label} is rejected, not fabricated",
            raises(ArgillaRejectedError, c.get_user, USER["id"]),
        )
    stub.reply(200, {"items": [USER, "junk"]})
    check(
        "R006P1a-4",
        "a user list with a non-object item is rejected whole",
        raises(ArgillaRejectedError, c.list_workspace_users, WORKSPACE["id"]),
    )
    stub.reply(200, {"items": [USER, no_key]})
    check(
        "R006P1a-4",
        "a user list with one api_key-less item is rejected whole",
        raises(ArgillaRejectedError, c.list_workspace_users, WORKSPACE["id"]),
    )
    stub.reply(200, {"items": [WORKSPACE, 7]})
    check(
        "R006P1a-4",
        "a workspace list with a non-object item is rejected whole",
        raises(ArgillaRejectedError, c.list_workspaces_me),
    )
    stub.reply(200, {"items": [{"id": "x", "name": "n"}]})
    check(
        "R006P1a-4",
        "a workspace with a non-UUID id is rejected",
        raises(ArgillaRejectedError, c.list_workspaces_me),
    )
    stub.reply(200, b'{"version": 1e999}')
    check(
        "R006P1a-4",
        "1e999 (parses as inf) is rejected through the live call",
        raises(ArgillaRejectedError, c.get_status),
    )
    check(
        "R006P1a-4",
        "1e999 and -1e999 rejected by the strict parser directly",
        raises(ArgillaRejectedError, parse_strict_json, b'{"a": 1e999}')
        and raises(ArgillaRejectedError, parse_strict_json, b'{"a": -1e999}'),
    )
    check(
        "R006P1a-4",
        "a finite float still parses",
        parse_strict_json(b'{"a": 1.5}') == {"a": 1.5},
    )
    stub.server.shutdown()


def test_r2_status_classes_and_redirects() -> None:
    stub = Stub()
    c = stub.client(api_key="provision-key")
    for code in (408, 425, 429):
        stub.reply(code, {"detail": "slow down"})
        check(
            "R006P1a-5",
            f"HTTP {code} is transient (Unavailable), not a permanent rejection",
            raises(ArgillaUnavailableError, c.get_user, USER["id"]),
        )
    for code in (400, 401, 403, 422):
        stub.reply(code, {"detail": "no"})
        kind, _ = _message_of(c.get_user, USER["id"])
        check("R006P1a-5", f"HTTP {code} stays a plain rejection", kind is ArgillaRejectedError)
    for code in (301, 302, 303, 307, 308):
        stub.reply(code, dict(USER), {"Location": "/api/v1/users/other"})
        n = len(stub.seen)
        check(
            "R006P1a-5",
            f"FI18: HTTP {code} carrying a valid user body is rejected, never parsed as success",
            raises(ArgillaRejectedError, c.get_user, USER["id"]) and len(stub.seen) == n + 1,
        )
    stub.server.shutdown()

    target = Stub()
    origin = Stub()
    policy = ConnectionPolicy(
        name="follow",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({origin.port, target.port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        allow_redirects=True,
    )
    following = ArgillaClient(f"http://127.0.0.1:{origin.port}", policy, api_key="leaky-key")
    origin.reply(307, b"", {"Location": f"http://127.0.0.1:{target.port}/api/v1/status"})
    target.reply(200, {"version": "x"})
    kind, _ = _message_of(following.get_status)
    check(
        "R006P1a-5",
        "even with allow_redirects=True the key is never forwarded to another host",
        kind is ArgillaRejectedError and not target.seen and target.connections == 0,
    )
    origin.server.shutdown()
    target.server.shutdown()


def test_r2_policy_denied_sends_nothing_and_scrubs() -> None:
    stub = Stub()
    denied = ArgillaClient(
        f"http://127.0.0.1:{stub.port}",
        ConnectionPolicy(
            name="deny",
            allowed_schemes=frozenset({"http"}),
            allowed_ports=frozenset({stub.port + 1}),
        ),
        api_key="secret-key",
    )
    check(
        "R006P1a-5",
        "FI8b: a policy-denied call is rejected and no request or connection reached the server",
        raises(ArgillaRejectedError, denied.get_user, USER["id"])
        and not stub.seen
        and stub.connections == 0,
    )
    stub.server.shutdown()

    key = "argilla.echoed.key"
    original = client_mod.guarded_stream_fetch
    try:
        for exc, expected in (
            (OutboundGuardError(f"connection failed for {key}"), ArgillaUnavailableError),
            (OSError(f"socket error {key}"), ArgillaUnavailableError),
            (OutboundPolicyError(f"denied {key}"), ArgillaRejectedError),
        ):

            def boom(*_a: Any, _exc: Exception = exc, **_k: Any) -> Any:
                raise _exc

            client_mod.guarded_stream_fetch = boom
            c = ArgillaClient("https://argilla.internal", ConnectionPolicy(name="p"), api_key=key)
            kind, text = _message_of(c.get_status)
            check(
                "R006P1a-5",
                f"FI11: {type(exc).__name__} text is scrubbed of the key",
                kind is expected and _no_key(text, key) and "[redacted]" in text,
            )
    finally:
        client_mod.guarded_stream_fetch = original


def test_r2_repr_and_shared_transport() -> None:
    stub = Stub()
    c = stub.client(api_key="provision-key")
    stub.reply(200, {"items": [USER]})
    members = c.list_workspace_users(WORKSPACE["id"])
    check(
        "R006P1a-6",
        "repr() of a user and of a list_workspace_users result never shows the api_key",
        USER["api_key"] not in repr(members) and USER["api_key"] not in repr(members[0]),
    )
    stub.reply(200, {"version": "2.8.0"})
    check(
        "R006P1a-6",
        "the guarded transport is reusable outside the client (P1b/P2)",
        isinstance(c.transport, ArgillaTransport)
        and c.transport.call("GET", "/status") == {"version": "2.8.0"}
        and stub.seen[-1]["api_key"] == "provision-key",
    )
    stub.server.shutdown()


def test_r2_mutation_kills() -> None:
    stub = Stub()
    c = stub.client()
    for raw in (b"[]", b'"s"', b"1", b"null", b"true"):
        check(
            "R006P1a-4",
            f"parse_strict_json refuses the non-object top level {raw!r} directly",
            raises(ArgillaRejectedError, parse_strict_json, raw),
        )
    for role in ("Annotator", "ANNOTATOR", " annotator", "annotator "):
        n = len(stub.seen)
        check(
            "R006P1a-1",
            f"create_user role {role!r} is refused before any request",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username="sovra-a",
                role=role,
                password="correct-horse-battery",
            )
            and len(stub.seen) == n,
        )
    check(
        "R006P1a-1",
        "a non-string role is refused as a rejection, not a crash",
        raises(
            ArgillaRejectedError,
            c.create_user,
            username="sovra-a",
            role=5,
            password="correct-horse-battery",
        ),
    )
    for items in ({}, "", None, 5, {"a": 1}):
        stub.reply(200, {"items": items})
        check(
            "R006P1a-4",
            f"a user list whose items is {items!r} is rejected",
            raises(ArgillaRejectedError, c.list_workspace_users, WORKSPACE["id"]),
        )
        stub.reply(200, {"items": items})
        check(
            "R006P1a-4",
            f"a workspace list whose items is {items!r} is rejected",
            raises(ArgillaRejectedError, c.list_workspaces_me),
        )
    # exactly MAX_RESPONSE_BYTES is accepted; one byte more is refused
    prefix = b'{"pad": "'
    suffix = b'"}'
    exact = prefix + b"x" * (MAX_RESPONSE_BYTES - len(prefix) - len(suffix)) + suffix
    check(
        "R006P1a-3", "the fixture is exactly MAX_RESPONSE_BYTES", len(exact) == MAX_RESPONSE_BYTES
    )
    stub.reply(200, exact)
    check(
        "R006P1a-3",
        "a body of exactly MAX_RESPONSE_BYTES is accepted",
        len(c.get_status()["pad"]) > 0,
    )
    stub.reply(200, exact[:-2] + b"x" + suffix)
    check(
        "R006P1a-3",
        "a body one byte over MAX_RESPONSE_BYTES is rejected",
        raises(ArgillaRejectedError, c.get_status),
    )
    # a zero-padded Content-Length still equals the body: not a truncation
    padded = RawServer(
        lambda _s, conn: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 02\r\n\r\n{}")
    )
    check(
        "R006P1a-3",
        "Content-Length: 02 on a 2-byte body is a normal success",
        padded.client().get_status() == {},
    )
    padded.close()
    junk = RawServer(
        lambda _s, conn: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0x2\r\n\r\n{}")
    )
    kind, _ = _message_of(junk.client().get_status)
    check(
        "R006P1a-3", "a non-numeric Content-Length is Unavailable", kind is ArgillaUnavailableError
    )
    junk.close()
    stub.server.shutdown()


def test_r2_deadline_between_chunks_with_fake_clock() -> None:
    """The deadline check itself (not the socket timeout): a clock that has
    already passed the deadline must give the deadline error, and exactly
    at the deadline counts as expired."""
    import types

    server = RawServer(
        lambda _s, conn: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    )
    real = client_mod.time
    try:
        for offset, label in ((100.0, "past the deadline"), (2.0, "exactly at the deadline")):
            ticks = iter([1000.0] + [1000.0 + offset] * 50)
            client_mod.time = types.SimpleNamespace(monotonic=lambda it=ticks: next(it))  # type: ignore[assignment]
            kind, text = _message_of(server.client(timeout=2).get_status)
            check(
                "R006P1a-3",
                f"a clock {label} gives the call-deadline error",
                kind is ArgillaUnavailableError and "call deadline" in text,
            )
    finally:
        client_mod.time = real
    server.close()


# -- round 3 (Control-plane return 2026-10-20) ---------------------------------


def _timed_call(fn: Any, *args: Any, limit: float) -> tuple[bool, float, type | None, str]:
    """Run `fn` in a daemon thread so a call that never returns fails the
    test instead of hanging it: (finished, elapsed, exc type, exc text)."""
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            fn(*args)
        except Exception as exc:
            out["kind"] = type(exc)
            out["text"] = str(exc) + repr(exc.__cause__) + repr(exc.__context__)

    started = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(limit)
    return (not worker.is_alive(), time.monotonic() - started, out.get("kind"), out.get("text", ""))


def test_r3_header_phase_deadline() -> None:
    def status_drip(_s: RawServer, conn: socket.socket) -> None:
        for byte in b"HTTP/1.1 200 OK" * 40:
            conn.sendall(bytes([byte]))
            time.sleep(0.1)

    def header_drip(_s: RawServer, conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        for _ in range(2000):
            conn.sendall(b"X")
            time.sleep(0.1)

    for label, handler in (("H1 status-line", status_drip), ("H2 header", header_drip)):
        server = RawServer(handler)
        c = server.client(timeout=0.5)
        finished, elapsed, kind, _ = _timed_call(c.get_status, limit=8)
        check("R006P1a-7", f"{label} drip: the call returns at all", finished)
        check(
            "R006P1a-7",
            f"{label} drip is Unavailable within ~2x the timeout (took {elapsed:.2f}s)",
            finished and kind is ArgillaUnavailableError and elapsed < 1.1,
        )
        server.close()


def test_r3_guard_deadline_directly() -> None:
    def status_drip(_s: RawServer, conn: socket.socket) -> None:
        for byte in b"HTTP/1.1 200 OK" * 40:
            conn.sendall(bytes([byte]))
            time.sleep(0.1)

    server = RawServer(status_drip)
    policy = ConnectionPolicy(
        name="raw",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({server.port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    url = f"http://127.0.0.1:{server.port}/x"

    def fetch(**kw: Any) -> Any:
        return guarded_stream_fetch(url, policy, method="GET", timeout=10.0, **kw)

    finished, elapsed, kind, text = _timed_call(lambda: fetch(deadline=0.4), limit=8)
    check(
        "R006P1a-7",
        f"guarded_stream_fetch(deadline=0.4) raises OutboundGuardError (took {elapsed:.2f}s)",
        finished and kind is OutboundGuardError and elapsed < 0.9 and "deadline" in text,
    )
    for bad in (0, -1.0, float("nan"), float("inf"), True, "1"):
        check(
            "R006P1a-7",
            f"deadline={bad!r} is refused before any connection",
            raises(OutboundGuardError, fetch, deadline=bad),
        )
    for bad in (0, -1.0, float("nan"), float("inf"), True, "1"):
        _, text = _message_of(fetch, deadline=bad)
        check(
            "R006P1a-7",
            f"deadline={bad!r} is refused as an invalid value, not as an expiry",
            "positive finite" in text,
        )
    check("R006P1a-7", "no refused deadline opened a connection", server.connections <= 1)
    server.close()

    # the watchdog firing in the window after getresponse() but before the
    # caller stops it must not hand back a half-killed stream, and the
    # connection must be built with a timeout no longer than the deadline
    quick = RawServer(
        lambda _s, conn: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    )
    qpolicy = ConnectionPolicy(
        name="raw",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({quick.port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    qurl = f"http://127.0.0.1:{quick.port}/x"
    real_prepare = guard_mod._prepare_pinned_connection
    real_timer = guard_mod.threading.Timer
    captured: dict[str, Any] = {}

    class FakeTimer:
        daemon = False

        def __init__(self, _interval: float, fn: Any) -> None:
            captured["fire"] = fn

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    def prepare(*a: Any, **k: Any) -> Any:
        conn, path, host = real_prepare(*a, **k)
        captured["conn"] = conn
        real_get = conn.getresponse

        def late_fire() -> Any:
            resp = real_get()
            captured["fire"]()  # the deadline expires right after the headers arrive
            return resp

        conn.getresponse = late_fire  # type: ignore[method-assign]
        return conn, path, host

    guard_mod._prepare_pinned_connection = prepare
    guard_mod.threading.Timer = FakeTimer  # type: ignore[misc,assignment]
    try:
        kind, text = _message_of(
            guarded_stream_fetch, qurl, qpolicy, method="GET", timeout=10.0, deadline=0.5
        )
    finally:
        guard_mod._prepare_pinned_connection = real_prepare
        guard_mod.threading.Timer = real_timer  # type: ignore[misc]
    check(
        "R006P1a-7",
        "a deadline that expires right after the headers arrive raises, not a dead stream",
        kind is OutboundGuardError and "deadline" in text,
    )
    check(
        "R006P1a-7",
        "the connection timeout is capped at the deadline (10s timeout, 0.5s deadline)",
        captured["conn"].timeout <= 0.5,
    )
    quick.close()

    # default (None) is unchanged: a prompt response still works, no thread left behind
    quick = RawServer(
        lambda _s, conn: conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    )
    quick_policy = ConnectionPolicy(
        name="raw",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({quick.port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    quick_url = f"http://127.0.0.1:{quick.port}/x"
    before = threading.active_count()
    kws: list[dict[str, Any]] = [{}, {"deadline": 5.0}]
    for kw in kws:
        with guarded_stream_fetch(quick_url, quick_policy, method="GET", **kw) as resp:
            check(
                "R006P1a-7",
                f"deadline={kw.get('deadline')} does not disturb a prompt response",
                resp.status == 200 and resp.read_chunk() == b"{}",
            )
    time.sleep(0.2)
    check(
        "R006P1a-7",
        "the deadline watchdog is cancelled once headers are in (no thread left behind)",
        threading.active_count() <= before + 1,
    )
    quick.close()


def test_r3_guard_deadline_mutation_kills() -> None:
    import types

    handler = lambda _s, conn: conn.sendall(  # noqa: E731
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
    )
    quick = RawServer(handler)
    policy = ConnectionPolicy(
        name="raw",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({quick.port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    url = f"http://127.0.0.1:{quick.port}/x"

    # (a) a budget that is already spent (exactly 0 left, on a fake clock)
    # raises the deadline error before any connection or timer exists.
    real_time = guard_mod.time
    real_timer = guard_mod.threading.Timer
    timers: list[Any] = []

    class CountingTimer:
        daemon = False

        def __init__(self, interval: float, fn: Any) -> None:
            timers.append(fn)
            self._real = real_timer(interval, fn)

        def start(self) -> None:
            self._real.start()

        def cancel(self) -> None:
            self._real.cancel()

    ticks = iter([100.0] + [100.5] * 50)
    guard_mod.time = types.SimpleNamespace(monotonic=lambda: next(ticks))  # type: ignore[assignment]
    guard_mod.threading.Timer = CountingTimer  # type: ignore[misc,assignment]
    try:
        kind, text = _message_of(
            guarded_stream_fetch, url, policy, method="GET", timeout=10.0, deadline=0.5
        )
    finally:
        guard_mod.time = real_time
        guard_mod.threading.Timer = real_timer  # type: ignore[misc]
    check(
        "R006P1a-7",
        "a spent budget (exactly 0 remaining) raises the deadline error",
        kind is OutboundGuardError and "exceeded the call deadline" in text,
    )
    check(
        "R006P1a-7",
        "a spent budget starts no watchdog and opens no connection",
        not timers and quick.connections == 0,
    )

    # (b) a watchdog that fires after the call finished must not abort the
    # stream the caller now holds.
    captured: list[Any] = []

    class FakeTimer:
        daemon = False

        def __init__(self, _interval: float, fn: Any) -> None:
            captured.append(fn)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    guard_mod.threading.Timer = FakeTimer  # type: ignore[misc,assignment]
    try:
        stream = guarded_stream_fetch(url, policy, method="GET", timeout=5.0, deadline=5.0)
    finally:
        guard_mod.threading.Timer = real_timer  # type: ignore[misc]
    with stream:
        captured[0]()  # late timer callback, after the headers were accepted
        check(
            "R006P1a-7",
            "a late watchdog callback does not abort a stream already handed back",
            stream.read_chunk() == b"{}"
            and stream.final_url == url
            and stream.status == 200
            and stream._connection.sock is not None
            and stream._connection.sock.fileno() != -1,
        )

    # (c) a plain connection failure with a deadline set is still wrapped.
    hangup = RawServer(lambda _s, conn: None)
    hpolicy = ConnectionPolicy(
        name="raw",
        allowed_schemes=frozenset({"http"}),
        allowed_ports=frozenset({hangup.port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    kind, text = _message_of(
        guarded_stream_fetch,
        f"http://127.0.0.1:{hangup.port}/x",
        hpolicy,
        method="GET",
        timeout=5.0,
        deadline=5.0,
    )
    check(
        "R006P1a-7",
        "a server that hangs up is OutboundGuardError('... failed'), not a raw OSError",
        kind is OutboundGuardError and "failed" in text and "deadline" not in text,
    )
    hangup.close()
    quick.close()

    # (d) the HTTPS connect hook wraps the pinned socket (the late-handshake
    # deadline path is pinned for real in test_r4_*).
    class FakeCtx:
        def wrap_socket(self, raw: Any, server_hostname: str) -> str:
            return f"tls({server_hostname})"

    real_cc = guard_mod.socket.create_connection
    guard_mod.socket.create_connection = lambda *_a, **_k: "RAW"  # type: ignore[assignment]
    try:
        tls = guard_mod._PinnedHTTPSConnection("h.example", "127.0.0.1", 443, 1.0, FakeCtx())  # type: ignore[arg-type]
        tls.connect()
    finally:
        guard_mod.socket.create_connection = real_cc
    check(
        "R006P1a-7",
        "_PinnedHTTPSConnection.connect wraps the pinned socket",
        tls.sock == "tls(h.example)",
    )


def test_r3_final_1xx_is_unavailable() -> None:
    # No Content-Length: http.client treats a 1xx body as empty, so the
    # truncation check cannot catch it and only `_classify` can.
    for code in (102, 103, 199):
        server = RawServer(
            lambda _s, conn, code=code: conn.sendall(f"HTTP/1.1 {code} Info\r\n\r\n".encode())
        )
        kind, text = _message_of(server.client(api_key="k-1xx", timeout=2).get_status)
        check(
            "R006P1a-8",
            f"a final HTTP {code} with no Content-Length is Unavailable, not Rejected",
            kind is ArgillaUnavailableError and "k-1xx" not in text,
        )
        server.close()


def test_r3_key_never_reaches_an_exception_from_response_fields() -> None:
    key = "argilla.provisioning.key"
    stub = Stub()
    c = stub.client(api_key=key)
    variants: list[tuple[str, dict[str, Any]]] = [
        ("role", {**USER, "role": key}),
        ("role+suffix", {**USER, "role": key + "x"}),
        ("role list", {**USER, "role": [key]}),
        ("id", {**USER, "id": key}),
        ("username", {**USER, "username": key, "role": "root"}),
    ]
    for label, payload in variants:
        for call, args in (
            ("get_user", (USER["id"],)),
            ("create_user", ()),
            ("list_workspace_users", (WORKSPACE["id"],)),
        ):
            stub.reply(
                200 if call != "create_user" else 201,
                payload if call != "list_workspace_users" else {"items": [payload]},
            )
            if call == "create_user":
                kind, text = _message_of(
                    c.create_user,
                    username="sovra-a",
                    role="annotator",
                    password="correct-horse-battery",
                )
            else:
                kind, text = _message_of(getattr(c, call), *args)
            check(
                "R006P1a-9",
                f"{call}: the key echoed in {label} never reaches an exception",
                kind is ArgillaRejectedError and _no_key(text, key),
            )
    for field_name in ("id", "name"):
        stub.reply(200, {"items": [{**WORKSPACE, field_name: key}]})
        kind, text = _message_of(c.list_workspaces_me)
        check(
            "R006P1a-9",
            f"list_workspaces_me: the key echoed in {field_name} never reaches an exception",
            _no_key(text, key),
        )
    stub.server.shutdown()


def test_r3_create_response_api_key_shape() -> None:
    stub = Stub()
    c = stub.client()
    for label, bad in (
        ("whitespace", "has space"),
        ("leading space", " key"),
        ("CRLF", "key\r\nX-Evil: 1"),
        ("NUL", "key\x00"),
        ("tab", "k\tey"),
        ("DEL", "k\x7fey"),
        ("NBSP", "k\u00a0ey"),
    ):
        stub.reply(201, {**USER, "api_key": bad})
        check(
            "R006P1a-10",
            f"a create_user response whose api_key has {label} is rejected",
            raises(
                ArgillaRejectedError,
                c.create_user,
                username="sovra-a",
                role="annotator",
                password="correct-horse-battery",
            ),
        )
        stub.reply(200, {**USER, "api_key": bad})
        check(
            "R006P1a-10",
            f"a get_user response whose api_key has {label} is rejected",
            raises(ArgillaRejectedError, c.get_user, USER["id"]),
        )
    stub.reply(201, {**USER, "api_key": "argilla.A-b_c~9=="})
    check(
        "R006P1a-10",
        "an ordinary printable api_key is still accepted",
        c.create_user(
            username="sovra-a", role="annotator", password="correct-horse-battery"
        ).api_key
        == "argilla.A-b_c~9==",
    )
    stub.server.shutdown()


def _echo_probe(key: str) -> tuple[bool, bool]:
    """(username rejection leaks the key, workspace-name rejection leaks it)."""
    stub = Stub()
    c = stub.client(api_key=key)
    leaks_user = leaks_ws = False
    for bad in (f"[{key}]", f"{key} x", f"{key}\n"):
        _, text = _message_of(
            c.create_user, username=bad, role="annotator", password="correct-horse-battery"
        )
        leaks_user = leaks_user or not _no_key(text, key)
    bad_names: list[Any] = [f"[{key}]", {key: 1}, [key]]
    for bad in bad_names:
        for call in (c.ensure_workspace, c.create_workspace):
            _, text = _message_of(call, bad)
            leaks_ws = leaks_ws or not _no_key(text, key)
    stub.server.shutdown()
    return leaks_user, leaks_ws


def test_r4_key_never_echoed_by_username_or_workspace_rejection() -> None:
    key = "argilla.provisioning.key"
    leaks_user, leaks_ws = _echo_probe(key)
    check(
        "R006P1a-10",
        "an invalid username carrying the key never reaches an exception",
        not leaks_user,
    )
    check(
        "R006P1a-10",
        "an invalid workspace name carrying the key never reaches an exception",
        not leaks_ws,
    )

    # G21 / G22: a rejection that echoes the value must turn the probe RED.
    real_user, real_ws = client_mod._require_username, client_mod._require_workspace_name

    def echo_user(value: object) -> str:
        if not isinstance(value, str) or not client_mod._USERNAME.fullmatch(value):
            raise ArgillaRejectedError(f"bad username {value!r}")
        return value

    def echo_ws(value: object) -> str:
        if not isinstance(value, str) or not client_mod._WORKSPACE_NAME.fullmatch(value):
            raise ArgillaRejectedError(f"bad workspace {value!r}")
        return value

    try:
        client_mod._require_username = echo_user
        g21 = _echo_probe(key)
    finally:
        client_mod._require_username = real_user
    try:
        client_mod._require_workspace_name = echo_ws
        g22 = _echo_probe(key)
    finally:
        client_mod._require_workspace_name = real_ws
    check(
        "R006P1a-10", "G21: a username rejection that echoes the value turns the probe RED", g21[0]
    )
    check(
        "R006P1a-10",
        "G22: a workspace-name rejection that echoes the value turns the probe RED",
        g22[1],
    )


def _self_signed_context(tmp: Path) -> Any:
    import ssl
    import subprocess

    cert, keyf = tmp / "c.pem", tmp / "k.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(keyf),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(keyf))
    return ctx


class SlowTlsServer:
    """HTTPS server whose TLS handshake is dripped (each gap shorter than the
    client's per-read timeout) so it completes AFTER the client's total
    deadline, then drips the status line one byte at a time."""

    def __init__(self, ctx: Any, handshake_gap: float, chunks: int = 8) -> None:
        self.ctx, self.gap, self.chunks = ctx, handshake_gap, chunks
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _flush(self, conn: socket.socket, out: Any, drip: bool) -> None:
        data = out.read()
        if not data:
            return
        step = max(1, len(data) // self.chunks) if drip else len(data)
        for i in range(0, len(data), step):
            conn.sendall(data[i : i + step])
            if drip:
                time.sleep(self.gap)

    def _one(self, conn: socket.socket) -> None:
        import ssl

        try:
            conn.settimeout(10)
            inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
            obj = self.ctx.wrap_bio(inc, out, server_side=True)
            while True:
                try:
                    obj.do_handshake()
                    self._flush(conn, out, drip=False)
                    break
                except ssl.SSLWantReadError:
                    self._flush(conn, out, drip=True)
                    data = conn.recv(65536)
                    if not data:
                        return
                    inc.write(data)
            while True:
                try:
                    obj.read(65536)
                    break
                except ssl.SSLWantReadError:
                    data = conn.recv(65536)
                    if not data:
                        return
                    inc.write(data)
            for byte in b"HTTP/1.1 200 OK" * 40:
                obj.write(bytes([byte]))
                self._flush(conn, out, drip=False)
                time.sleep(0.1)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.sock.close()


def test_r4_tls_late_handshake_then_header_drip_is_bounded() -> None:
    import ssl
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        ctx = _self_signed_context(Path(td))
        server = SlowTlsServer(ctx, handshake_gap=0.05)
        policy = ConnectionPolicy(
            name="raw",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({server.port}),
            explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
        )
        client_ctx = ssl.create_default_context()
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE
        real_build = guard_mod._build_connection

        def build(scheme: str, hostname: str, pinned_ip: str, port: int, timeout: float) -> Any:
            return guard_mod._PinnedHTTPSConnection(hostname, pinned_ip, port, timeout, client_ctx)

        url = f"https://127.0.0.1:{server.port}/x"

        def fetch() -> Any:
            return guarded_stream_fetch(url, policy, method="GET", timeout=2.0, deadline=0.5)

        real_cc = guard_mod.socket.create_connection

        def slow_connect(*a: Any, **k: Any) -> Any:
            # A slow TCP connect delays the handshake start by 0.2 s, so the
            # the handshake's own socket timeout (0.5 s)
            # outlives the watchdog (fires at 0.5 s) by 0.2 s: the window in
            # which the handshake completes AFTER the deadline.
            time.sleep(0.2)
            return real_cc(*a, **k)

        guard_mod._build_connection = build
        guard_mod.socket.create_connection = slow_connect
        try:
            finished, elapsed, kind, text = _timed_call(fetch, limit=15)
        finally:
            guard_mod._build_connection = real_build
            guard_mod.socket.create_connection = real_cc
            server.close()
    check(
        "R006P1a-11",
        f"a TLS handshake that completes after the deadline, then a header drip, ends "
        f"OutboundGuardError within ~2x the deadline-plus-handshake (took {elapsed:.2f}s)",
        finished and kind is OutboundGuardError and elapsed < 3.0 and "deadline" in text,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-R006-P1 Argilla client tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-R006-P1 Argilla client tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
