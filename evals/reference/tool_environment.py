"""Tool environment (FTF-1702, Section 54-55/66/68-69/77/81).

FTF-1701 (CERTIFIED) built the reference agent *specification*: five real
tool schemas plus `ReferenceBusinessServer`, a genuine deterministic
business-state implementation, and `dispatch_tool_call`, the tool-call
boundary that enforces both tools' own frozen input/output schemas. That
task's own module docstring named this task, FTF-1702, and thirteen others
as *its* own dependents, not the other way around, and disclosed explicitly
that building the full reference *product* (a real connected repository,
>=1,000 training traces, a sealed final suite, real training/evaluation
runs -- Section 54's own end-to-end release qualification scenario) is that
later chain's job, not FTF-1701's.

This task's own acceptance clause is narrower still, and precedes the rest
of that chain: "Implement real disposable seeded backend for search/get/
create-note/request-approval/execute-approved-action." Section 66.3's own
canonical runtime adapter contract names the operation this task is really
about: `reset` -- "destroy/reseed disposable namespace between independent
cases." A qualification campaign runs many independent cases against the
same five tools; without a real, disposable, reseedable environment, one
case's mutations (a note it created, an approval it executed) would leak
into the next case's own accounting -- silently corrupting Section 69's own
"each scheduled unique case has exactly one accepted terminal result"
requirement. This module makes that real: `ToolEnvironment` owns exactly
one `ReferenceBusinessServer` at a time; `reset()` discards it outright and
constructs a fresh one from the same deterministic seed spec, so a
qualification case can never observe another case's own state, proven
directly by a dedicated test that mutates state, resets, and confirms the
mutation is gone rather than merely asserting a doc comment.

This task's own negative_acceptance ("Canned success tools and unguarded
financial writes cannot satisfy target-product test environment") is
enforced by construction, not asserted: `ToolEnvironment.dispatch` never
short-circuits to a canned result -- it is a thin, real caller of FTF-1701's
own `dispatch_tool_call`, which itself runs the real `ReferenceBusinessServer`
handler (genuine tenant/subject-scoped authorization, real idempotency, a
real approval-workflow state machine) and validates both the call's
arguments and the handler's result against the tool's own frozen schema.
This module adds exactly one new guard of its own: `dispatch` also refuses
a `tenant_id`/`subject_id` pair this environment's own seed spec never
declared, so a qualification harness cannot silently address a subject that
was never actually seeded into the disposable namespace it believes it is
testing against.

`database_changes` is honored ("No independent schema change. Consume
frozen contract"); this module touches no database, network, or GPU
resource, so this task's own `resource_gate` clause is moot, matching the
FTF-1701 precedent exactly (`resource_gate` names a real external/GPU
prerequisite only where the acceptance clause itself requires one --
"implement a real disposable seeded backend" does not name a container,
HTTP endpoint, or GPU of its own; the canonical adapter contract each of
Section 66.3's *other* six operations would require -- `describe`,
`preflight`, `invoke`, `cancel`, `collect_outcome`, `attest` -- belongs to
whichever later task actually owns wiring this environment behind a real
runtime adapter, not this one). `shared_lock_required` is empty and
`contract_owner` remains FTF-1701's own contracts:17 lock -- this module
adds no new frozen shape and extends no existing one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_API_ROOT = _ROOT / "apps" / "api"
for _path in (str(_ROOT), str(_API_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from evals.reference.reference_agent_specification import (  # noqa: E402
    ReferenceBusinessServer,
    dispatch_tool_call,
)

__all__ = [
    "DEFAULT_SEED",
    "ToolEnvironment",
    "ToolEnvironmentError",
    "ToolEnvironmentSeed",
    "build_tool_environment",
]


class ToolEnvironmentError(ValueError):
    """A dispatch was addressed to a tenant/subject this environment's own
    seed spec never declared -- refused before FTF-1701's own dispatcher is
    ever reached, so an un-seeded identity can never observe or mutate real
    business state."""


@dataclass(frozen=True)
class ToolEnvironmentSeed:
    """A deterministic fixture: exactly which tenants, subjects and records
    exist the instant a fresh `ReferenceBusinessServer` is built, before any
    tool call runs. `records` is `(tenant_id, owner_subject_id, title)`
    triples -- the same shape `ReferenceBusinessServer.seed_record` already
    accepts, never a second, competing record representation."""

    tenants: tuple[str, ...]
    subjects_by_tenant: dict[str, tuple[str, ...]]
    records: tuple[tuple[str, str, str], ...]


DEFAULT_SEED = ToolEnvironmentSeed(
    tenants=("tenant-a", "tenant-b"),
    subjects_by_tenant={
        "tenant-a": ("subject-a1", "subject-a2"),
        "tenant-b": ("subject-b1",),
    },
    records=(
        ("tenant-a", "subject-a1", "Quarterly report"),
        ("tenant-a", "subject-a2", "Vendor onboarding"),
        ("tenant-b", "subject-b1", "Support escalation"),
    ),
)


class ToolEnvironment:
    """A real, disposable, seeded instance of the reference tool backend.
    Owns exactly one `ReferenceBusinessServer`; `reset()` discards it and
    builds a fresh one from the same seed spec, so state from one
    qualification case can never leak into the next."""

    def __init__(self, *, seed: ToolEnvironmentSeed = DEFAULT_SEED) -> None:
        self._seed_spec = seed
        self._reset_count = 0
        self._server: ReferenceBusinessServer
        self._record_ids: dict[tuple[str, str, str], str]
        self._reseed()

    def _reseed(self) -> None:
        server = ReferenceBusinessServer()
        record_ids: dict[tuple[str, str, str], str] = {}
        for tenant_id, owner_subject_id, title in self._seed_spec.records:
            record = server.seed_record(
                tenant_id=tenant_id, owner_subject_id=owner_subject_id, title=title
            )
            record_ids[(tenant_id, owner_subject_id, title)] = record.record_id
        self._server = server
        self._record_ids = record_ids

    @property
    def server(self) -> ReferenceBusinessServer:
        """The current, live `ReferenceBusinessServer` instance. Holding a
        reference to this object across a `reset()` call is a caller error:
        `reset()` builds an entirely new server rather than clearing this
        one in place, precisely so a stale reference cannot observe
        post-reset mutations, or vice versa."""
        return self._server

    @property
    def reset_count(self) -> int:
        return self._reset_count

    @property
    def seed(self) -> ToolEnvironmentSeed:
        return self._seed_spec

    def record_id_for(self, *, tenant_id: str, owner_subject_id: str, title: str) -> str:
        """Resolve a seeded record's real ID by the fixture triple that
        created it -- callers never hard-code a UUID, since `reset()`
        deliberately assigns a fresh one on every reseed."""
        key = (tenant_id, owner_subject_id, title)
        try:
            return self._record_ids[key]
        except KeyError as exc:
            raise ToolEnvironmentError(f"no seeded record for {key!r}") from exc

    def dispatch(
        self,
        tool_name: str,
        *,
        tenant_id: str,
        subject_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """The one call surface a qualification case uses. Refuses a
        tenant/subject this environment's own seed never declared before
        ever reaching FTF-1701's real dispatcher, its real authorization
        checks or its real idempotency cache -- never a canned success, and
        never a silent no-op for an unrecognized identity."""
        if tenant_id not in self._seed_spec.tenants:
            raise ToolEnvironmentError(f"tenant not seeded in this environment: {tenant_id!r}")
        if subject_id not in self._seed_spec.subjects_by_tenant.get(tenant_id, ()):
            raise ToolEnvironmentError(
                f"subject not seeded in this environment for tenant {tenant_id!r}: {subject_id!r}"
            )
        return dispatch_tool_call(
            self._server,
            tool_name,
            tenant_id=tenant_id,
            subject_id=subject_id,
            arguments=arguments,
        )

    def reset(self) -> None:
        """Section 66.3's own named operation: "destroy/reseed disposable
        namespace between independent cases." Discards the current server
        outright (never mutated in place) and rebuilds a fresh one from the
        same seed spec -- every record gets a newly generated ID, so a
        caller that cached a prior ID cannot accidentally address
        post-reset state either."""
        self._reseed()
        self._reset_count += 1


def build_tool_environment(*, seed: ToolEnvironmentSeed = DEFAULT_SEED) -> ToolEnvironment:
    return ToolEnvironment(seed=seed)
