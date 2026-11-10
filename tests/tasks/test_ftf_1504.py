#!/usr/bin/env python3
"""FTF-1504 -- IDOR suite (no-DB gate tests).

Runs in the `gate` job with no database or network egress beyond loopback.
Proves, against the REAL `app.main.create_app()`, that the suite's route
registry covers the live `/api/v1` surface exactly (so a PR that adds a
route without registering it fails `gate`), and proves the comparators able
to fail against a throwaway demo app served over a real socket.

The live two-tenant matrix (Postgres + SeaweedFS) is run by
`tests/security/idor_suite.py` itself; see its docstring.

Run directly: python3 tests/tasks/test_ftf_1504.py
"""

from __future__ import annotations

import contextlib
import dataclasses
import http.client
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "tests" / "security"))
sys.path.insert(0, str(ROOT / "tests" / "tasks"))

import idor_suite as suite  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Response  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- the live app, generated (never hand-listed) -----------------------------


def _live() -> set[tuple[str, str]]:
    import test_ftf_0103a as t0103a

    app = t0103a._build_real_app()
    return suite.live_route_verbs(app, t0103a._flatten_routes)


def test_registry_covers_live_surface() -> set[tuple[str, str]]:
    live = _live()
    print(f"live /api/v1 route-verbs: {len(live)}; registry rows: {len(suite.ROUTE_REGISTRY)}")
    problems = suite.completeness_violations(
        live,
        suite.ROUTE_REGISTRY,
        suite.contract_body_id_slots(),
        suite.route_module_sources(),
    )
    check("FTF-1504-INV", f"registry matches the live app exactly: {problems[:3]}", not problems)
    check("FTF-1504-INV", "the live surface is non-trivially large", len(live) >= 100)
    return live


def test_completeness_gate_is_able_to_fail(live: set[tuple[str, str]]) -> None:
    contract = suite.contract_body_id_slots()
    sources = suite.route_module_sources()
    base = dict(suite.ROUTE_REGISTRY)

    def run(
        registry: dict[tuple[str, str], suite.RouteSpec] | None = None,
        live_set: set[tuple[str, str]] | None = None,
        srcs: dict[str, str] | None = None,
        unc: frozenset[tuple[str, str]] = suite.UNCONTRACTED_ROUTE_VERBS,
    ) -> list[str]:
        return suite.completeness_violations(
            live_set if live_set is not None else live,
            registry if registry is not None else base,
            contract,
            srcs if srcs is not None else sources,
            unc,
        )

    # FAULT: a live route deleted from the registry.
    victim = ("GET", "/api/v1/organizations/{organization_id}/projects/{project_id}/tools")
    trimmed = {k: v for k, v in base.items() if k != victim}
    check(
        "FTF-1504-F-REG-DEL",
        "a live route missing from the registry is reported",
        any("unregistered live route GET" in p for p in run(trimmed)),
    )

    # FAULT: a stale registry entry for a route that no longer exists.
    check(
        "FTF-1504-F-REG-STALE",
        "a registry entry with no live route is reported",
        any("no live route" in p for p in run(live_set=live - {victim})),
    )

    # FAULT: an unregistered contract `_id` body field.
    key = (
        "POST",
        "/api/v1/organizations/{organization_id}/projects/{project_id}/evaluation-suites",
    )
    spec = base[key]
    no_reviewer = dataclasses.replace(
        spec, body_slots=tuple(s for s in spec.body_slots if s.name != "reviewer_id")
    )
    check(
        "FTF-1504-F-CONTRACT-ID",
        "a contract body `_id` that is not registered is reported",
        any("'reviewer_id' is not registered" in p for p in run({**base, key: no_reviewer})),
    )

    # FAULT: an allow-list `_id` name present in a route module but registered nowhere.
    mutated = {
        m: (src + '\n_EXTRA_FIELDS = frozenset({"phantom_resource_id"})\n')
        for m, src in sources.items()
    }
    check(
        "FTF-1504-F-ALLOWLIST-ID",
        "an allow-list `_id` name that is not registered is reported",
        any("'phantom_resource_id'" in p for p in run(srcs=mutated)),
    )
    # ... and read from the payload by a helper call, not only a module-level set.
    called = {
        m: src + '\ndef _x(payload):\n    return _require_uuid(payload, "phantom_call_id")\n'
        for m, src in sources.items()
    }
    check(
        "FTF-1504-F-ALLOWLIST-CALL",
        "a `_id` read via a payload helper call is reported",
        any("'phantom_call_id'" in p for p in run(srcs=called)),
    )
    sub = {
        m: src + '\ndef _y(body):\n    return body["phantom_sub_id"]\n'
        for m, src in sources.items()
    }
    check(
        "FTF-1504-F-ALLOWLIST-SUB",
        "a `_id` read via payload subscript is reported",
        any("'phantom_sub_id'" in p for p in run(srcs=sub)),
    )
    # Negative: response keys and attribute names are not read from a payload.
    benign = {
        m: src + '\ndef _z(x):\n    return {"response_row_id": x.row_id}\n'
        for m, src in sources.items()
    }
    check(
        "FTF-1504-N-ALLOWLIST-BENIGN",
        "response keys are not mistaken for body slots",
        not any("response_row_id" in p for p in run(srcs=benign)),
    )

    # FAULT: a path param without a kind.
    saved = dict(suite.COLLECTION_KINDS)
    try:
        del suite.COLLECTION_KINDS["upload-sessions"]
        try:
            suite.build_registry()
            refused = False
        except suite.IdorSuiteError:
            refused = True
    finally:
        suite.COLLECTION_KINDS.clear()
        suite.COLLECTION_KINDS.update(saved)
    check("FTF-1504-F-NOKIND", "a path param whose collection has no kind is refused", refused)

    # FAULT: registered path slots that disagree with the template.
    wrong = dataclasses.replace(spec, path_slots=spec.path_slots[:-1])
    check(
        "FTF-1504-F-PATHSLOTS",
        "path slots that differ from the template are reported",
        any("path slots" in p for p in run({**base, key: wrong})),
    )

    # FAULT: the uncontracted list drifting from the contracts.
    check(
        "FTF-1504-F-UNCONTRACTED-MISSING",
        "a live route in no contract and not listed is reported",
        any(
            "not listed as uncontracted" in p
            for p in run(
                unc=suite.UNCONTRACTED_ROUTE_VERBS
                - {("POST", "/api/v1/organizations/{organization_id}/component-bindings")}
            )
        ),
    )
    check(
        "FTF-1504-F-UNCONTRACTED-EXTRA",
        "a contracted route listed as uncontracted is reported",
        any(
            "a contract declares it" in p
            for p in run(
                unc=suite.UNCONTRACTED_ROUTE_VERBS
                | {("GET", "/api/v1/organizations/{organization_id}/artifacts")}
            )
        ),
    )
    check(
        "FTF-1504-UNCONTRACTED-COUNT",
        f"25 uncontracted route-verbs are disclosed: {len(suite.UNCONTRACTED_ROUTE_VERBS)}",
        len(suite.UNCONTRACTED_ROUTE_VERBS) == 25,
    )


def test_registry_shape() -> None:
    reg = suite.ROUTE_REGISTRY
    for (method, template), spec in reg.items():
        for slot in spec.path_slots:
            check("FTF-1504-SHAPE", f"{method} {template}: {slot.name} has a kind", bool(slot.kind))
        check(
            "FTF-1504-SHAPE",
            f"{method} {template}: destructive flag",
            spec.destructive
            == (method == "DELETE" or template.rsplit("/", 1)[-1] in suite._DESTRUCTIVE_ACTIONS),
        )
    delete_routes = [k for k in reg if k[0] == "DELETE"]
    check(
        "FTF-1504-SHAPE",
        "every DELETE is destructive",
        all(reg[k].destructive for k in delete_routes),
    )
    check(
        "FTF-1504-SHAPE",
        "no GET is destructive",
        not any(s.destructive for k, s in reg.items() if k[0] == "GET"),
    )
    # The `connection_id` parameter takes its kind from its collection.
    kinds = {
        reg[("GET", "/api/v1/organizations/{organization_id}/connections/litellm/{connection_id}")]
        .path_slots[1]
        .kind,
        reg[
            (
                "GET",
                "/api/v1/organizations/{organization_id}/projects/{project_id}/dataset-sources/{connection_id}",
            )
        ]
        .path_slots[2]
        .kind,
        reg[
            (
                "GET",
                "/api/v1/organizations/{organization_id}/projects/{project_id}/target-connections/{connection_id}",
            )
        ]
        .path_slots[2]
        .kind,
    }
    check(
        "FTF-1504-SHAPE",
        f"connection_id resolves to three distinct kinds: {kinds}",
        len(kinds) == 3,
    )
    # Cursors resolve no row and carry a reason; non-row body slots carry one too.
    for spec in reg.values():
        for slot in spec.query_slots:
            if slot.cursor:
                check(
                    "FTF-1504-SHAPE",
                    "a cursor slot has a reason and resolves no row",
                    bool(slot.reason) and not slot.resolves_row,
                )
        for slot in spec.body_slots:
            if not slot.resolves_row:
                check("FTF-1504-SHAPE", "a non-row body slot has a reason", bool(slot.reason))
    check(
        "FTF-1504-SHAPE",
        "the organization path slot is first on every route",
        all(s.path_slots and s.path_slots[0].kind == "organization" for s in reg.values()),
    )
    check(
        "FTF-1504-SHAPE",
        "nested array body slots are registered",
        any("[]" in slot.name for s in reg.values() for slot in s.body_slots),
    )


# -- comparators, against a demo app served over a real socket ---------------

DEMO_MARKER = "MARKER-ORG-B-SECRET-NAME"


class _DemoState:
    """In-memory stand-ins the comparator observes: a row store, a storage
    listing and an outbound gateway counter."""

    def __init__(self) -> None:
        self.rows: list[str] = ["a1"]
        self.storage: list[str] = []
        self.gateway_calls = 0
        self.audit: list[dict[str, Any]] = []

    def snapshot(self) -> suite.StateSnapshot:
        return suite.StateSnapshot(
            db={"things": json.dumps(self.rows), "audit_events": json.dumps(self.audit)},
            storage=json.dumps(self.storage),
            outbound={"gateway": self.gateway_calls},
            audit_rows=tuple(dict(r) for r in self.audit),
        )


def _make_demo(fault: str | None, state: _DemoState) -> FastAPI:
    app = FastAPI()
    owned = {"a1"}
    foreign = {"b1": DEMO_MARKER}

    def _not_found() -> HTTPException:
        return HTTPException(status_code=404, detail="not_found")

    @app.get("/orgs/a/things/{thing_id}")
    def get_thing(thing_id: str, response: Response) -> dict[str, str]:
        if thing_id in owned:
            return {"id": thing_id, "name": "mine"}
        if thing_id in foreign:
            if fault == "403_for_foreign":
                raise HTTPException(status_code=403, detail="role_not_authorized")
            if fault == "insert_row":
                state.rows.append(f"probe:{thing_id}")
            if fault == "gateway_call":
                state.gateway_calls += 1
            if fault == "storage_write":
                state.storage.append(f"probe/{thing_id}")
            if fault == "different_body":
                raise HTTPException(status_code=404, detail="not_found_here")
            if fault == "extra_header":
                response.headers["x-resource-exists"] = "1"
                raise HTTPException(
                    status_code=404, detail="not_found", headers={"x-resource-exists": "1"}
                )
            if fault == "echo_marker":
                raise HTTPException(status_code=404, detail=f"not_found:{foreign[thing_id]}")
            if fault == "audit_names_foreign":
                state.audit.append(
                    {"id": 1, "created_at": "t", "request_id": "r", "target": thing_id}
                )
        elif fault == "audit_names_foreign":
            state.audit.append(
                {"id": 2, "created_at": "t2", "request_id": "r2", "target": "missing"}
            )
        if fault == "vacuous" and thing_id in owned:
            raise _not_found()
        raise _not_found()

    if fault == "vacuous":
        # A route that 404s everything: the `own` positive control must catch it.
        @app.get("/orgs/vac/things/{thing_id}")
        def vac(thing_id: str) -> None:
            raise _not_found()

    return app


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve(app: FastAPI) -> Iterator[int]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _observe(port: int, path: str) -> suite.Observed:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return suite.Observed(resp.status, tuple(resp.getheaders()), body)
    finally:
        conn.close()


def _run_demo(fault: str | None) -> list[suite.CellVerdict]:
    state = _DemoState()
    with _serve(_make_demo(fault, state)) as port:
        ids = {"foreign_org": "b1", "missing": "zzz", "own": "a1"}

        def send(cls: str) -> suite.Observed:
            return _observe(port, f"/orgs/a/things/{ids[cls]}")

        verdicts = [
            suite.evaluate_cell(
                method="GET",
                template="/orgs/a/things/{thing_id}",
                slot="thing_id",
                id_class="foreign_org",
                role="admin",
                send=send,
                snapshot=state.snapshot,
                markers=[DEMO_MARKER],
                forbidden=["b1"],
            ),
            suite.evaluate_positive_control(
                method="GET",
                template="/orgs/a/things/{thing_id}",
                slot="thing_id",
                role="admin",
                send=send,
            ),
        ]
    return verdicts


def test_comparators() -> None:
    clean = _run_demo(None)
    check(
        "FTF-1504-CLEAN",
        f"a correct route passes every comparator: {[v.problems for v in clean]}",
        all(v.passed for v in clean),
    )
    # NEGATIVE: the real default code with the attack input present, bad outcome asserted absent.
    own_status = clean[1].status
    check("FTF-1504-CLEAN", "the positive control actually resolves the object", own_status == 200)

    faults = {
        "403_for_foreign": "status 403",
        "insert_row": "side effects differ",
        "gateway_call": "side effects differ",
        "storage_write": "side effects differ",
        "different_body": "body bytes differ",
        "extra_header": "headers differ",
        "echo_marker": "marker",
        "audit_names_foreign": "",
    }
    for fault, needle in faults.items():
        verdicts = _run_demo(fault)
        cell = verdicts[0]
        check(
            f"FTF-1504-F-{fault.upper()}",
            f"fault {fault!r} turns the cell RED ({cell.problems})",
            not cell.passed and any(needle in p for p in cell.problems),
        )
    # The vacuous positive control: a route that 404s the owned object too.
    state = _DemoState()
    with _serve(_make_demo("vacuous", state)) as port:

        def send_vac(cls: str) -> suite.Observed:
            ids = {"missing": "zzz", "own": "a1"}
            return _observe(port, f"/orgs/vac/things/{ids[cls]}")

        vac = suite.evaluate_positive_control(
            method="GET",
            template="/orgs/vac/things/{thing_id}",
            slot="thing_id",
            role="admin",
            send=send_vac,
        )
    check(
        "FTF-1504-F-VACUOUS",
        "a positive control that 404s is flagged as vacuous",
        not vac.passed and "vacuous" in vac.problems[0],
    )


def test_comparator_units() -> None:
    a = suite.Observed(
        404, (("content-type", "application/json"), ("date", "x")), b'{"detail":"not_found"}'
    )
    b = suite.Observed(
        404, (("content-type", "application/json"), ("date", "y")), b'{"detail":"not_found"}'
    )
    check(
        "FTF-1504-CMP",
        "the date header alone may differ",
        not suite.indistinguishability_problems(a, b, []),
    )
    c = suite.Observed(
        404, (("content-type", "application/json"), ("Date", "y"), ("X-Extra", "1")), a.body
    )
    check(
        "FTF-1504-CMP",
        "any other header difference is reported",
        bool(suite.indistinguishability_problems(c, b, [])),
    )
    d = suite.Observed(404, b.headers, b'{"detail":"not_found"} ')
    check(
        "FTF-1504-CMP",
        "a one-byte body difference is reported",
        bool(suite.indistinguishability_problems(d, b, [])),
    )
    e = suite.Observed(404, (("x-leak", "has-" + DEMO_MARKER),) + b.headers, b.body)
    check(
        "FTF-1504-CMP",
        "a marker in a header is reported",
        any("marker" in p for p in suite.indistinguishability_problems(e, e, [DEMO_MARKER])),
    )
    # Side-effect delta: an audit written for both classes is allowed only when equal after masking.
    s0 = suite.StateSnapshot({"t": "1"}, "s", {"g": 0}, ())
    s1 = suite.StateSnapshot(
        {"t": "1"}, "s", {"g": 0}, ({"id": 9, "created_at": "q", "request_id": "r", "target": "x"},)
    )
    s2 = suite.StateSnapshot(
        {"t": "1"}, "s", {"g": 0}, ({"id": 3, "created_at": "z", "request_id": "w", "target": "x"},)
    )
    check(
        "FTF-1504-CMP",
        "equal masked denial audits compare equal",
        not suite.side_effect_problems(
            suite.state_delta(s0, s1), suite.state_delta(s0, s2), ["b1"]
        ),
    )
    s3 = suite.StateSnapshot(
        {"t": "1"},
        "s",
        {"g": 0},
        ({"id": 3, "created_at": "z", "request_id": "w", "target": "b1"},),
    )
    check(
        "FTF-1504-CMP",
        "an audit that names the foreign object is reported",
        bool(
            suite.side_effect_problems(suite.state_delta(s0, s3), suite.state_delta(s0, s3), ["b1"])
        ),
    )
    check(
        "FTF-1504-CMP",
        "an unchanged state produces an empty delta",
        suite.state_delta(s0, s0) == {},
    )
    # The enumeration guard: a hand-trimmed digest list that drops audit_events is refused.
    refused = False
    try:
        suite.assert_digest_covers(["projects", "datasets"], ["audit_events"])
    except suite.IdorSuiteError:
        refused = True
    check("FTF-1504-F-DIGEST-TRIM", "a digest that omits audit_events is refused", refused)
    suite.assert_digest_covers(["projects", "audit_events"], ["audit_events"])


def main() -> None:
    live = test_registry_covers_live_surface()
    test_completeness_gate_is_able_to_fail(live)
    test_registry_shape()
    test_comparator_units()
    test_comparators()
    if FAILURES:
        print("\n".join(FAILURES))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
