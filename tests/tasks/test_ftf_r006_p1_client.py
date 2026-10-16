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
import sys
import threading
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
    ArgillaUnavailableError,
    parse_strict_json,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

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

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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
    # M2/M1 at this exact check are empirically equivalent to the generic
    # `except ValueError` fallback a few lines down in `_call` (http.client's
    # own `putheader` raises before any bytes reach the stub either way, and
    # the fallback's own message is static too) -- confirmed by running the
    # mutation gate, not assumed; disclosed in the submission for a
    # Control-plane mutation-allowlist.yaml ruling rather than contorted into
    # an artificial distinction here.
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
    mini_ws = {"id": "0", "name": "n"}
    mini_user = {"id": "0", "username": "n", "role": "a", "api_key": "k"}
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
