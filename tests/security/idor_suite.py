#!/usr/bin/env python3
"""FTF-1504 -- IDOR suite (Sections 45, 47, 52, 65.1-65.2, 77, 81).

Acceptance: "Guess foreign IDs across every nested API and object
download/export endpoint". Negative acceptance: "404 masking alone
insufficient if side effect/data still occurs; check actual state/access".

This module is built in layers, each independently testable:

1. **Inventory and registry (this layer, no database).** The live
   `/api/v1` surface is *generated* from the real `app.main.create_app()`
   (flattened with FTF-0103A's `_flatten_routes`), never hand-listed. Every
   route-verb must be registered in `ROUTE_REGISTRY` with every id slot it
   exposes: path parameters (each with a resource kind), id-bearing query
   parameters, and request-body properties ending `_id`/`_ids`. The
   completeness gate (`completeness_violations`) is what makes a new route
   without a registry entry fail `gate`: the registry can never silently
   fall behind the live app.
2. **Indistinguishability and side-effect comparators (no database).**
   Pure functions that decide whether a foreign-id response is
   byte-identical to the `missing` response and whether a request left any
   observable side effect (DB digest, storage digest, outbound counters).
3. **Live matrix (Postgres + SeaweedFS).** Runs the generated
   route x slot x id-class x role matrix against the real app over a real
   socket; see `run_live_matrix` and `main`.

`database_changes`: none. No production code, migration, contract,
runtime dependency, secret or keyed service is added by this task.
"""

from __future__ import annotations

import ast
import dataclasses
import glob
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "security"))

import yaml  # noqa: E402

__all__ = [
    "BODY_SLOT_KINDS",
    "COLLECTION_KINDS",
    "ID_CLASSES",
    "IdorSuiteError",
    "ROUTE_REGISTRY",
    "RouteSpec",
    "SlotSpec",
    "allow_list_id_names",
    "build_registry",
    "completeness_violations",
    "contract_body_id_slots",
    "live_route_verbs",
    "registry_body_leaf_names",
    "template_path_params",
]


class IdorSuiteError(RuntimeError):
    """The suite refuses to produce evidence -- never converts a blocked
    precondition into a silent pass (Law 17)."""


# -- Id classes (the matrix columns) ----------------------------------------

#: Every id class the matrix generates for a slot. `own` is the positive
#: control: it must differ from `missing`, otherwise the slot is vacuous.
ID_CLASSES: tuple[str, ...] = (
    "foreign_org",
    "foreign_project",
    "foreign_parent",
    "hidden_purpose",
    "foreign_name",
    "missing",
    "own",
)

# -- Resource kinds ---------------------------------------------------------

#: The path segment that *precedes* a `{param}` decides the resource kind of
#: that param. `connection_id` is deliberately not mapped by name: the same
#: parameter name means three different resources (LiteLLM connection,
#: dataset source connection, target-application connection) depending on
#: its collection, so the kind comes from the collection segment.
COLLECTION_KINDS: dict[str, str] = {
    "organizations": "organization",
    "artifacts": "trainable_artifact",
    "component-bindings": "component_binding",
    "litellm": "litellm_connection",
    "cost-profiles": "cost_profile",
    "projects": "project",
    "dataset-sources": "dataset_source_connection",
    "datasets": "dataset",
    "issues": "dataset_quality_issue",
    "evaluation-suites": "evaluation_suite",
    "cases": "evaluation_case",
    "manifests": "agent_manifest",
    "evidence": "manifest_evidence",
    "model-imports": "model_import_job",
    "model-versions": "model_version",
    "target-connections": "target_connection",
    "snapshots": "target_source_snapshot",
    "candidates": "discovered_agent_candidate",
    "tools": "tool_definition",
    "upload-sessions": "upload_session",
    "models": "model_name",
}

#: Request-body id properties, by leaf name. `resolves_row` is False for a
#: value that is not a reference to a stored row (a licence identifier is
#: free text); such a slot is registered, with its reason, but the matrix
#: does not generate foreign classes for it.
BODY_SLOT_KINDS: dict[str, tuple[str, bool, str]] = {
    "license_id": ("license_identifier", False, "licence identifier text, not a stored row id"),
    "trainable_artifact_id": ("trainable_artifact", True, ""),
    "workspace_id": ("workspace", True, ""),
    "supersedes_snapshot_id": ("target_source_snapshot", True, ""),
    "target_source_snapshot_id": ("target_source_snapshot", True, ""),
    "dataset_source_connection_id": ("dataset_source_connection", True, ""),
    "quality_report_id": ("dataset_quality_report", True, ""),
    "issue_id": ("dataset_quality_issue", True, ""),
    "successor_dataset_id": ("dataset", True, ""),
    "agent_manifest_id": ("agent_manifest", True, ""),
    "reviewer_id": ("member_identity", True, ""),
    "parent_model_version_id": ("model_version", True, ""),
    "target_application_connection_id": ("target_connection", True, ""),
}


@dataclasses.dataclass(frozen=True)
class SlotSpec:
    """One id-bearing input of a route."""

    where: str  # "path" | "query" | "body"
    name: str  # path param / query key / dotted body property
    kind: str
    resolves_row: bool = True
    cursor: bool = False
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class RouteSpec:
    """Everything the matrix needs to know about one route-verb."""

    method: str
    template: str
    path_slots: tuple[SlotSpec, ...]
    query_slots: tuple[SlotSpec, ...]
    body_slots: tuple[SlotSpec, ...]
    seed: tuple[str, ...]
    destructive: bool

    @property
    def all_slots(self) -> tuple[SlotSpec, ...]:
        return self.path_slots + self.query_slots + self.body_slots


_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")


def template_path_params(template: str) -> list[tuple[str, str]]:
    """[(param name, preceding path segment)] in order of appearance."""
    segments = [s for s in template.split("/") if s]
    out: list[tuple[str, str]] = []
    for index, segment in enumerate(segments):
        match = _PARAM_RE.fullmatch(segment)
        if match is not None:
            out.append((match.group(1), segments[index - 1] if index else ""))
    return out


def _normalise_template(path: str) -> str:
    return re.sub(r"\{([^}:]+):[^}]*\}", r"{\1}", path)


# -- The registry -----------------------------------------------------------

_CURSOR_REASON = "pagination cursor: compared by value, resolves no row"

#: Verbs whose last path segment is an action that changes state; they run
#: the foreign classes and `missing` first, then the control on a
#: disposable seed (the matrix runner reads `RouteSpec.destructive`).
_DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "accept-license",
        "revoke-license",
        "mark-ready",
        "lock",
        "select",
        "finalize",
        "reserve",
        "release",
        "authorize",
        "resolve-issue",
        "submit-for-review",
        "validate",
        "verify",
    }
)

_P = "/api/v1/organizations/{organization_id}"
_PP = _P + "/projects/{project_id}"
_LC = _P + "/connections/litellm/{connection_id}"
_MODEL = _LC + "/models/{model_name}"
_TC = _PP + "/target-connections/{connection_id}"
_SNAP = _TC + "/snapshots/{snapshot_id}"
_SUITE = _PP + "/evaluation-suites/{suite_id}"
_MANIFEST = _PP + "/manifests/{manifest_id}"
_DATASET = _PP + "/datasets/{dataset_id}"
_UPLOAD = _PP + "/upload-sessions/{upload_session_id}"

#: (method, template, query id slots, cursor query slots, body id slots).
#: Body slots are dotted property paths; nested array items use `[]`.
#: The 25 routes absent from the frozen OpenAPI contracts (Decision 10) are
#: declared by hand here and cross-checked against each route module's
#: body handling by `allow_list_id_names`; they are listed in
#: `UNCONTRACTED_ROUTE_VERBS` as a disclosed contract gap.
_ROWS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("GET", _P + "/artifacts", (), (), ()),
    ("POST", _P + "/artifacts", (), (), ("license_id",)),
    ("DELETE", _P + "/artifacts/{artifact_id}", (), (), ()),
    ("GET", _P + "/artifacts/{artifact_id}", (), (), ()),
    ("PATCH", _P + "/artifacts/{artifact_id}", (), (), ("license_id",)),
    ("POST", _P + "/artifacts/{artifact_id}/accept-license", (), (), ()),
    ("POST", _P + "/artifacts/{artifact_id}/mark-ready", (), (), ()),
    ("POST", _P + "/artifacts/{artifact_id}/revoke-license", (), (), ()),
    ("GET", _P + "/component-bindings", (), (), ()),
    ("POST", _P + "/component-bindings", (), (), ()),
    ("GET", _P + "/component-bindings/{binding_id}", (), (), ()),
    ("PATCH", _P + "/component-bindings/{binding_id}", (), (), ()),
    ("POST", _P + "/component-bindings/{binding_id}/validate", (), (), ()),
    ("GET", _P + "/component-status", (), (), ()),
    ("GET", _P + "/connections/litellm", (), (), ()),
    ("POST", _P + "/connections/litellm", (), (), ()),
    ("DELETE", _LC, (), (), ()),
    ("GET", _LC, (), (), ()),
    ("PATCH", _LC, (), (), ()),
    ("GET", _LC + "/catalog", ("project_id",), (), ()),
    ("GET", _LC + "/models", (), (), ()),
    ("POST", _LC + "/models/{model_id}/probe", ("project_id",), (), ()),
    ("GET", _MODEL + "/capabilities", ("project_id",), (), ()),
    ("GET", _MODEL + "/training-artifact", (), (), ()),
    ("POST", _MODEL + "/training-artifact", (), (), ("trainable_artifact_id",)),
    ("GET", _MODEL + "/training-artifact/changes", (), (), ()),
    ("GET", _LC + "/registration-capability", (), (), ()),
    ("POST", _LC + "/validate", (), (), ()),
    ("GET", _P + "/cost-profiles", (), (), ()),
    ("POST", _P + "/cost-profiles", (), (), ()),
    ("GET", _P + "/cost-profiles/{cost_profile_id}", (), (), ()),
    ("POST", _P + "/cost-profiles/{cost_profile_id}/verify", (), (), ()),
    ("GET", _P + "/projects", (), (), ()),
    ("POST", _P + "/projects", (), (), ("workspace_id",)),
    ("DELETE", _PP, (), (), ()),
    ("GET", _PP, (), (), ()),
    ("PATCH", _PP, (), (), ()),
    ("GET", _PP + "/budget-policy", (), (), ()),
    ("PATCH", _PP + "/budget-policy", (), (), ()),
    ("POST", _PP + "/budget-policy", (), (), ()),
    ("GET", _PP + "/budget-policy/history", (), (), ()),
    ("POST", _PP + "/budget-policy/release", (), (), ()),
    ("POST", _PP + "/budget-policy/reserve", (), (), ()),
    ("GET", _PP + "/costs/cost-per-success/search", (), (), ()),
    ("GET", _PP + "/dataset-sources", (), (), ()),
    ("POST", _PP + "/dataset-sources", (), (), ()),
    ("DELETE", _PP + "/dataset-sources/{connection_id}", (), (), ()),
    ("GET", _PP + "/dataset-sources/{connection_id}", (), (), ()),
    ("PATCH", _PP + "/dataset-sources/{connection_id}", (), (), ()),
    ("POST", _PP + "/dataset-sources/{connection_id}/authorize", (), (), ()),
    ("GET", _PP + "/datasets", (), ("after",), ()),
    ("GET", _DATASET, (), (), ()),
    ("GET", _DATASET + "/issue-resolutions", (), ("after",), ()),
    ("GET", _DATASET + "/issues", (), ("after",), ()),
    ("GET", _DATASET + "/issues/{issue_id}", (), (), ()),
    ("GET", _DATASET + "/quality-report", (), (), ()),
    (
        "POST",
        _DATASET + "/resolve-issue",
        (),
        (),
        ("quality_report_id", "resolutions[].issue_id", "resolutions[].successor_dataset_id"),
    ),
    ("GET", _PP + "/evaluation-suites", (), (), ()),
    ("POST", _PP + "/evaluation-suites", (), (), ("agent_manifest_id", "reviewer_id")),
    ("GET", _SUITE, (), (), ()),
    ("PATCH", _SUITE, (), (), ("reviewer_id",)),
    ("GET", _SUITE + "/cases", (), ("after",), ()),
    ("POST", _SUITE + "/cases", (), (), ()),
    ("GET", _SUITE + "/cases/{case_id}", (), (), ()),
    ("PATCH", _SUITE + "/cases/{case_id}", (), (), ()),
    ("POST", _SUITE + "/lock", (), (), ()),
    ("GET", _PP + "/manifests", (), (), ()),
    ("POST", _PP + "/manifests", (), (), ("target_source_snapshot_id",)),
    ("GET", _MANIFEST, (), (), ()),
    ("PATCH", _MANIFEST, (), (), ("target_source_snapshot_id",)),
    ("GET", _MANIFEST + "/evidence", (), (), ()),
    ("POST", _MANIFEST + "/evidence", (), (), ()),
    ("GET", _MANIFEST + "/evidence/{evidence_id}", (), (), ()),
    ("POST", _MANIFEST + "/lock", (), (), ()),
    ("POST", _MANIFEST + "/submit-for-review", (), (), ()),
    ("GET", _PP + "/model-imports", (), (), ()),
    (
        "POST",
        _PP + "/model-imports",
        (),
        (),
        ("parent_model_version_id", "trainable_artifact_id"),
    ),
    ("GET", _PP + "/model-imports/{model_import_job_id}", (), (), ()),
    ("GET", _PP + "/model-versions", (), (), ()),
    ("POST", _PP + "/model-versions", (), (), ("trainable_artifact_id",)),
    ("GET", _PP + "/model-versions/{model_version_id}", (), (), ()),
    ("GET", _PP + "/requirements", (), (), ()),
    ("GET", _PP + "/target-connections", (), (), ()),
    ("POST", _PP + "/target-connections", (), (), ()),
    (
        "POST",
        _PP + "/target-connections/github-branches",
        (),
        (),
        ("target_application_connection_id",),
    ),
    ("POST", _PP + "/target-connections/github-repositories", (), (), ()),
    (
        "POST",
        _PP + "/target-connections/github-resolve-ref",
        (),
        (),
        ("target_application_connection_id",),
    ),
    ("DELETE", _TC, (), (), ()),
    ("GET", _TC, (), (), ()),
    ("PATCH", _TC, (), (), ()),
    ("POST", _TC + "/github-source-snapshot", (), (), ("supersedes_snapshot_id",)),
    ("GET", _TC + "/snapshots", (), (), ()),
    ("POST", _TC + "/snapshots", (), (), ("supersedes_snapshot_id",)),
    ("GET", _SNAP, (), (), ()),
    ("GET", _SNAP + "/candidates", (), (), ()),
    ("POST", _SNAP + "/candidates", (), (), ()),
    ("GET", _SNAP + "/candidates/{candidate_id}", (), (), ()),
    ("POST", _SNAP + "/candidates/{candidate_id}/select", (), (), ()),
    ("GET", _PP + "/tools", (), (), ()),
    ("POST", _PP + "/tools", (), (), ()),
    ("GET", _PP + "/tools/{tool_definition_id}", (), (), ()),
    ("PATCH", _PP + "/tools/{tool_definition_id}", (), (), ()),
    ("POST", _PP + "/ui-state", (), (), ()),
    ("POST", _PP + "/upload-sessions", (), (), ("dataset_source_connection_id",)),
    ("GET", _UPLOAD, (), (), ()),
    ("POST", _UPLOAD + "/finalize", (), (), ()),
    ("POST", _UPLOAD + "/part-urls", (), (), ()),
    ("GET", _UPLOAD + "/parts", (), (), ()),
)


def _kind_for_param(param: str, collection: str, template: str) -> str:
    kind = COLLECTION_KINDS.get(collection)
    if kind is None:
        raise IdorSuiteError(
            f"path parameter {{{param}}} of {template} follows the collection "
            f"{collection!r}, which has no resource kind in COLLECTION_KINDS"
        )
    return kind


def _body_slot(dotted: str, template: str) -> SlotSpec:
    leaf = dotted.rsplit(".", 1)[-1].rsplit("]", 1)[-1].lstrip(".")
    spec = BODY_SLOT_KINDS.get(leaf)
    if spec is None:
        raise IdorSuiteError(f"body slot {dotted!r} of {template} has no entry in BODY_SLOT_KINDS")
    kind, resolves_row, reason = spec
    return SlotSpec("body", dotted, kind, resolves_row, False, reason)


def build_registry(
    rows: Iterable[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = _ROWS,
) -> dict[tuple[str, str], RouteSpec]:
    """Registry keyed by (METHOD, normalised path template). Every path
    parameter is given a resource kind from its collection segment and the
    result is checked against the template, so a path parameter without a
    kind is refused here rather than silently skipped."""
    registry: dict[tuple[str, str], RouteSpec] = {}
    for method, template, query_ids, cursors, body_ids in rows:
        key = (method, template)
        if key in registry:
            raise IdorSuiteError(f"duplicate registry row {key}")
        path_slots = tuple(
            SlotSpec("path", param, _kind_for_param(param, collection, template))
            for param, collection in template_path_params(template)
        )
        query_slots = tuple(
            SlotSpec("query", name, COLLECTION_KINDS["projects"] if name == "project_id" else name)
            for name in query_ids
        ) + tuple(
            SlotSpec("query", name, "cursor", False, True, _CURSOR_REASON) for name in cursors
        )
        body_slots = tuple(_body_slot(name, template) for name in body_ids)
        seed: list[str] = []
        for slot in path_slots + query_slots + body_slots:
            if slot.resolves_row and slot.kind not in seed:
                seed.append(slot.kind)
        last = template.rsplit("/", 1)[-1]
        registry[key] = RouteSpec(
            method=method,
            template=template,
            path_slots=path_slots,
            query_slots=query_slots,
            body_slots=body_slots,
            seed=tuple(seed),
            destructive=method == "DELETE" or last in _DESTRUCTIVE_ACTIONS,
        )
    return registry


ROUTE_REGISTRY: dict[tuple[str, str], RouteSpec] = build_registry()

#: The route-verbs the frozen OpenAPI contracts do not declare (Decision 10).
#: Disclosed as a contract gap; their body slots are hand-declared above and
#: cross-checked by `allow_list_id_names`.
UNCONTRACTED_ROUTE_VERBS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", _P + "/component-bindings"),
        ("GET", _P + "/component-bindings/{binding_id}"),
        ("GET", _P + "/component-status"),
        ("GET", _LC + "/catalog"),
        ("GET", _LC + "/models"),
        ("GET", _PP + "/budget-policy"),
        ("GET", _PP + "/budget-policy/history"),
        ("GET", _PP + "/model-versions"),
        ("GET", _PP + "/model-versions/{model_version_id}"),
        ("PATCH", _P + "/component-bindings/{binding_id}"),
        ("PATCH", _PP + "/budget-policy"),
        ("POST", _P + "/artifacts/{artifact_id}/revoke-license"),
        ("POST", _P + "/component-bindings"),
        ("POST", _P + "/component-bindings/{binding_id}/validate"),
        ("POST", _LC + "/models/{model_id}/probe"),
        ("POST", _LC + "/validate"),
        ("POST", _PP + "/budget-policy"),
        ("POST", _PP + "/budget-policy/release"),
        ("POST", _PP + "/budget-policy/reserve"),
        ("POST", _PP + "/model-versions"),
        ("POST", _PP + "/target-connections/github-branches"),
        ("POST", _PP + "/target-connections/github-repositories"),
        ("POST", _PP + "/target-connections/github-resolve-ref"),
        ("POST", _TC + "/github-source-snapshot"),
        ("POST", _SNAP + "/candidates/{candidate_id}/select"),
    }
)


# -- Live inventory ---------------------------------------------------------


def live_route_verbs(app: Any, flatten_routes: Any) -> set[tuple[str, str]]:
    """Every (METHOD, template) mounted under `/api/v1`, generated from the
    real app. `flatten_routes` is FTF-0103A's `_flatten_routes` (descends
    through lazily-included routers)."""
    verbs: set[tuple[str, str]] = set()
    for path, route in flatten_routes(list(app.routes)):
        if not path.startswith("/api/v1"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            verbs.add((method, _normalise_template(path)))
    return verbs


# -- Contract and allow-list discovery --------------------------------------

_ID_NAME = re.compile(r"_ids?$")
_BODY_NAMES = frozenset({"payload", "body", "data", "raw", "content"})
_OPENAPI_GLOB = str(ROOT / "packages" / "contracts" / "openapi" / "*-interface.yaml")


def _walk_schema(
    node: Any, path: str, out: set[str], doc: dict[str, Any], seen: tuple[str, ...]
) -> None:
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in seen:
            return
        target: Any = doc
        for part in ref.lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        _walk_schema(target, path, out, doc, seen + (ref,))
        return
    for name, sub in (node.get("properties") or {}).items():
        dotted = f"{path}.{name}" if path else name
        if _ID_NAME.search(name):
            out.add(dotted)
        _walk_schema(sub, dotted, out, doc, seen)
    for key in ("items", "additionalProperties"):
        if key in node:
            _walk_schema(node[key], path + "[]", out, doc, seen)
    for key in ("allOf", "oneOf", "anyOf"):
        for sub in node.get(key) or []:
            _walk_schema(sub, path, out, doc, seen)


def contract_body_id_slots(
    openapi_glob: str = _OPENAPI_GLOB,
) -> dict[tuple[str, str], set[str]]:
    """{(METHOD, template): dotted request-body properties ending _id/_ids}
    read from every frozen `*-interface.yaml`. The contract's templates use
    `{model_name}` where a few routes use `{model_id}`; both are normalised
    by the caller through `_normalise_template` only."""
    found: dict[tuple[str, str], set[str]] = {}
    for filename in sorted(glob.glob(openapi_glob)):
        with open(filename, encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
        for template, operations in (doc.get("paths") or {}).items():
            for method, operation in operations.items():
                if method not in ("get", "post", "patch", "put", "delete"):
                    continue
                schema = (
                    ((operation.get("requestBody") or {}).get("content") or {})
                    .get("application/json", {})
                    .get("schema")
                )
                if not schema:
                    continue
                ids: set[str] = set()
                _walk_schema(schema, "", ids, doc, ())
                if ids:
                    found[(method.upper(), template)] = ids
    return found


def _const_ids(node: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and _ID_NAME.search(n.value)
        and re.fullmatch(r"[a-z][a-z_]*", n.value)
    }


def allow_list_id_names(source: str) -> set[str]:
    """`*_id`/`*_ids` names a route module reads from its request body,
    found structurally rather than by a hand-kept list: (a) string constants
    inside a module-level collection assignment (the body allow-list
    frozensets), (b) a string constant passed as a later argument to a call
    whose first argument is the request payload (`_require_uuid(payload,
    "x_id")`), and (c) `payload.get("x_id")` / `payload["x_id"]`. Response
    keys, path parameters and attribute names are not matched because they
    are never read from a payload-named value."""
    tree = ast.parse(source)
    names: set[str] = set()
    for statement in tree.body:
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            value = statement.value
        if value is not None:
            names |= _const_ids(value)
    for walked in ast.walk(tree):
        node: ast.AST = walked
        if isinstance(node, ast.Call) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id in _BODY_NAMES:
                for arg in node.args[1:]:
                    names |= _const_ids(arg)
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id in _BODY_NAMES
            ):
                names |= _const_ids(first)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id in _BODY_NAMES:
                names |= _const_ids(node.slice)
    return names


def route_module_sources(app_root: Path = API_ROOT / "app") -> dict[str, str]:
    """Source text of every route module, keyed by repo-relative path."""
    out: dict[str, str] = {}
    for filename in sorted(app_root.rglob("*routes*.py")):
        out[str(filename.relative_to(ROOT))] = filename.read_text(encoding="utf-8")
    return out


def registry_body_leaf_names(registry: Mapping[tuple[str, str], RouteSpec]) -> set[str]:
    return {
        slot.name.rsplit(".", 1)[-1].rsplit("]", 1)[-1].lstrip(".")
        for spec in registry.values()
        for slot in spec.body_slots
    }


def _normalise_contract_key(method: str, template: str) -> tuple[str, str]:
    return method, _normalise_template(template)


def completeness_violations(
    live: set[tuple[str, str]],
    registry: Mapping[tuple[str, str], RouteSpec],
    contract_ids: Mapping[tuple[str, str], set[str]],
    module_sources: Mapping[str, str],
    uncontracted: frozenset[tuple[str, str]] = UNCONTRACTED_ROUTE_VERBS,
) -> list[str]:
    """Every way the registry can fall behind the live app. Empty means the
    registry covers the live surface exactly:

    * live set == registry keys (a route without an entry, or an entry for a
      route that no longer exists, both fail);
    * every `{param}` has a kind and the registered path slots match the
      template one for one;
    * every request-body `_id` property in the frozen contracts is registered
      on that exact route;
    * every `_id` name a route module reads from its body is registered on
      some route;
    * the uncontracted list is exactly the live routes no contract declares.
    """
    problems: list[str] = []
    for key in sorted(live - set(registry)):
        problems.append(f"unregistered live route {key[0]} {key[1]}")
    for key in sorted(set(registry) - live):
        problems.append(f"registry entry with no live route {key[0]} {key[1]}")
    for key, spec in sorted(registry.items()):
        declared = [slot.name for slot in spec.path_slots]
        actual = [name for name, _ in template_path_params(key[1])]
        if declared != actual:
            problems.append(f"{key[0]} {key[1]}: path slots {declared} != template params {actual}")
        for slot in spec.path_slots:
            if not slot.kind:
                problems.append(f"{key[0]} {key[1]}: path slot {slot.name} has no kind")
    for (method, template), dotted in contract_ids.items():
        key = _normalise_contract_key(method, template)
        found_spec = registry.get(key)
        if found_spec is None:
            # The contract names the route under a different parameter name
            # (`{model_name}` vs `{model_id}`): match on the shape.
            shape = re.sub(r"\{[^}]+\}", "{}", key[1])
            matches = [
                s
                for k, s in registry.items()
                if k[0] == key[0] and re.sub(r"\{[^}]+\}", "{}", k[1]) == shape
            ]
            found_spec = matches[0] if matches else None
        if found_spec is None:
            continue
        registered = {slot.name for slot in found_spec.body_slots}
        for name in sorted(dotted - registered):
            problems.append(f"{key[0]} {key[1]}: contract body id {name!r} is not registered")
    leaf_registered = registry_body_leaf_names(registry)
    for module, source in sorted(module_sources.items()):
        for name in sorted(allow_list_id_names(source) - leaf_registered):
            problems.append(f"{module}: body id name {name!r} is not registered on any route")
    # The uncontracted list must be exactly the live routes no contract has.
    shape_of = lambda k: (k[0], re.sub(r"\{[^}]+\}", "{}", k[1]))  # noqa: E731
    contract_shapes = {shape_of(_normalise_contract_key(*k)) for k in _all_contract_verbs()}
    expected_uncontracted = {k for k in live if shape_of(k) not in contract_shapes}
    shape_unc = {shape_of(k) for k in uncontracted}
    for key in sorted(expected_uncontracted):
        if shape_of(key) not in shape_unc:
            problems.append(
                f"{key[0]} {key[1]}: live but in no contract and not listed as uncontracted"
            )
    for key in sorted(uncontracted):
        if shape_of(key) in contract_shapes:
            problems.append(f"{key[0]} {key[1]}: listed as uncontracted but a contract declares it")
    return problems


def _all_contract_verbs(openapi_glob: str = _OPENAPI_GLOB) -> set[tuple[str, str]]:
    verbs: set[tuple[str, str]] = set()
    for filename in sorted(glob.glob(openapi_glob)):
        with open(filename, encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
        for template, operations in (doc.get("paths") or {}).items():
            for method in operations:
                if method in ("get", "post", "patch", "put", "delete"):
                    verbs.add((method.upper(), template))
    return verbs


# -- Observation and comparators (no database) -------------------------------

#: The only response header allowed to differ between a foreign-id response
#: and the `missing` response of the same route, slot and role.
VOLATILE_HEADERS: frozenset[str] = frozenset({"date"})

#: Audit-row columns masked before two denial audit deltas are compared.
AUDIT_MASKED_COLUMNS: frozenset[str] = frozenset({"id", "created_at", "request_id"})


@dataclasses.dataclass(frozen=True)
class Observed:
    """One raw HTTP response: status, every header, and the exact body bytes.
    Bodies are compared and scanned but never stored in evidence."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def stable_headers(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted((k.lower(), v) for k, v in self.headers if k.lower() not in VOLATILE_HEADERS)
        )


def indistinguishability_problems(
    foreign: Observed, missing: Observed, markers: Iterable[str]
) -> list[str]:
    """Why `foreign` is distinguishable from `missing` ([] when it is not).
    A foreign id must be byte-identical to a never-issued id: same status,
    same raw body, same headers (except `date`), and no marker planted in
    the other tenant's objects anywhere in the response."""
    problems: list[str] = []
    if foreign.status != missing.status:
        problems.append(f"status {foreign.status} != missing {missing.status}")
    if foreign.body != missing.body:
        problems.append("body bytes differ from the missing response")
    if foreign.stable_headers() != missing.stable_headers():
        problems.append("headers differ from the missing response")
    haystack = foreign.body + b"\n" + "\n".join(f"{k}: {v}" for k, v in foreign.headers).encode()
    for marker in markers:
        if marker and marker.encode() in haystack:
            problems.append("a marker planted in a foreign object appears in the response")
            break
    return problems


def positive_control_problems(own: Observed, missing: Observed) -> list[str]:
    """`own` must differ from `missing`; if it does not the slot is vacuous
    (the suite would pass for any implementation, including a route that
    404s everything)."""
    if own.status == missing.status and own.body == missing.body:
        return ["positive control is indistinguishable from missing: the slot is vacuous"]
    return []


@dataclasses.dataclass(frozen=True)
class StateSnapshot:
    """Observable state around one request. `db` is a per-table digest of
    every row of every table in `public`, `storage` a digest of the bucket
    listing plus multipart uploads and parts, `outbound` a call counter per
    local stand-in the seeds point at."""

    db: Mapping[str, str]
    storage: str
    outbound: Mapping[str, int]
    audit_rows: tuple[Mapping[str, Any], ...] = ()


def state_delta(before: StateSnapshot, after: StateSnapshot) -> dict[str, Any]:
    """What changed across one request, with the audit delta masked so two
    denial audits can be compared without their ids and timestamps."""
    delta: dict[str, Any] = {}
    changed = sorted(
        t for t in set(before.db) | set(after.db) if before.db.get(t) != after.db.get(t)
    )
    if changed:
        delta["db_tables_changed"] = changed
    if before.storage != after.storage:
        delta["storage_changed"] = True
    outbound = {
        name: after.outbound.get(name, 0) - before.outbound.get(name, 0)
        for name in sorted(set(before.outbound) | set(after.outbound))
        if after.outbound.get(name, 0) != before.outbound.get(name, 0)
    }
    if outbound:
        delta["outbound_calls"] = outbound
    new_audit = [
        {k: v for k, v in row.items() if k not in AUDIT_MASKED_COLUMNS}
        for row in after.audit_rows[len(before.audit_rows) :]
    ]
    if new_audit:
        delta["audit"] = new_audit
    return delta


def side_effect_problems(
    foreign_delta: Mapping[str, Any], missing_delta: Mapping[str, Any], forbidden: Iterable[str]
) -> list[str]:
    """The negative acceptance: a foreign id must leave exactly the trace a
    missing id leaves (expected: none). A denial audit is allowed only when
    both classes write the same masked audit and it never names the other
    tenant's ids."""
    problems: list[str] = []
    if dict(foreign_delta) != dict(missing_delta):
        problems.append(
            f"side effects differ from the missing response: foreign={dict(foreign_delta)!r} "
            f"missing={dict(missing_delta)!r}"
        )
    rendered = repr(foreign_delta.get("audit", ""))
    for value in forbidden:
        if value and value in rendered:
            problems.append("a denial audit names the other tenant's object")
            break
    return problems


def assert_digest_covers(enumerated_tables: Iterable[str], required: Iterable[str]) -> None:
    """The DB digest must be enumerated from `information_schema`, never a
    hand-kept list. This guards the enumeration itself: if a table the
    suite depends on (`audit_events`) is missing from what was digested, a
    side-effect fault there would stay green, so the run is refused."""
    missing = sorted(set(required) - set(enumerated_tables))
    if missing:
        raise IdorSuiteError(f"the DB digest does not cover required table(s): {missing}")


# -- One matrix cell ---------------------------------------------------------

from collections.abc import Callable  # noqa: E402


@dataclasses.dataclass(frozen=True)
class CellVerdict:
    """Content-free outcome of one (route, slot, class, role) cell."""

    method: str
    template: str
    slot: str
    id_class: str
    role: str
    status: int
    problems: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.problems


def evaluate_cell(
    *,
    method: str,
    template: str,
    slot: str,
    id_class: str,
    role: str,
    send: Callable[[str], Observed],
    snapshot: Callable[[], StateSnapshot],
    markers: Iterable[str],
    forbidden: Iterable[str],
) -> CellVerdict:
    """Run one foreign-class cell against the `missing` baseline for the
    same route, slot and role. `send(class_name)` performs the request;
    `snapshot()` observes DB, storage and outbound state. The foreign class
    is compared with `missing` on the response *and* on every side effect,
    because a 404 that still wrote a row or called a gateway is an IDOR."""
    markers = tuple(markers)
    forbidden = tuple(forbidden)
    before_missing = snapshot()
    missing = send("missing")
    missing_delta = state_delta(before_missing, snapshot())
    before_foreign = snapshot()
    foreign = send(id_class)
    foreign_delta = state_delta(before_foreign, snapshot())
    problems = indistinguishability_problems(foreign, missing, markers)
    problems += side_effect_problems(foreign_delta, missing_delta, forbidden)
    return CellVerdict(method, template, slot, id_class, role, foreign.status, tuple(problems))


def evaluate_positive_control(
    *,
    method: str,
    template: str,
    slot: str,
    role: str,
    send: Callable[[str], Observed],
) -> CellVerdict:
    own = send("own")
    missing = send("missing")
    return CellVerdict(
        method,
        template,
        slot,
        "own",
        role,
        own.status,
        tuple(positive_control_problems(own, missing)),
    )
