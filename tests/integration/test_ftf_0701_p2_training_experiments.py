#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,method-assign,misc,func-returns-value"
"""FTF-0701-P2 real proof: `training_experiments` persistence against a live PostgreSQL 16.

Runs migration 0039 (upgrade, downgrade -1, upgrade), then drives
`app.training.training_experiment_store` as the restricted `sovra_app` role. Fixtures are built
through the certified APIs: the FTF-0519 split-manifest flow (upload, quality report, propose,
approve = lock), FTF-0301/0305 artifact create / accept license / mark ready, FTF-0403
`create_budget_policy`, FTF-0500 dataset-source-connection CRUD for the grant changes. Projects,
source connections and the two catalog rows are seeded by the owner role, as the FTF-0519 proof
does.

Needs SOVRA_DATABASE_URL (the migration role) and SOVRA_APP_DB_PASSWORD. Run as CI does:
`uv run python3 tests/integration/test_ftf_0701_p2_training_experiments.py`.

Code faults are injected on the live module (one function replaced at a time, restored in
`finally`) and database faults on the live table (applied by the owner role, then reverted); each
must turn its scenario RED.
"""

# ruff: noqa: E501
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import logging
import sys
import threading
import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import test_ftf_0516_p1_quality_issues as quality_proof  # noqa: E402
import test_ftf_0519_p1_split_manifests as split_proof  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.artifacts import artifact_crud  # noqa: E402
from app.budget import budget_policy_crud  # noqa: E402
from app.datasets import dataset_source_connection_crud as connection_crud  # noqa: E402
from app.datasets import dataset_split_manifest_store as split_store  # noqa: E402
from app.db.tenant_context import TenantContext, tenant_scoped_session_for  # noqa: E402
from app.training import training_experiment_store as store  # noqa: E402

FAILURES: list[str] = []
CHECKS = [0]
CID = "FTF-0701-P2"
CANARY = "CANARY0701P2"


def check(description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


def _finish() -> None:
    if FAILURES:
        print(f"{CID} training experiment real proof: {len(FAILURES)} of {CHECKS[0]} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"{CID} training experiment real proof: all {CHECKS[0]} checks passed")
    sys.exit(0)


def _p1_valid_body() -> dict[str, Any]:
    """The pure module's own valid request (tests/tasks/test_ftf_0701_p1.py), so this proof and
    the P1 proof agree on what a valid body is."""
    path = ROOT / "tests" / "tasks" / "test_ftf_0701_p1.py"
    spec = importlib.util.spec_from_file_location("ftf_0701_p1_body", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body: dict[str, Any] = module.valid_body()
    return body


BASE_BODY = _p1_valid_body()
WRITER = store.ExperimentActor("user", "user-ml-1", "ml_engineer", "req-0701-p2")
WRITER_2 = store.ExperimentActor("user", "user-ml-2", "ml_engineer", "req-0701-p2-b")
ORG_ADMIN = store.ExperimentActor("user", "user-org-admin", "organization_admin", "req-0701-adm")
ARTIFACT_ACTOR = artifact_crud.ActorInfo("user", "user-org-admin", "req-0701-art")
BUDGET_ACTOR = budget_policy_crud.ActorInfo("user", "user-org-admin", "req-0701-bud")


class Fixture:
    """One project with a locked split manifest, a ready licensed artifact and (optionally) a
    budget policy."""

    def __init__(self, env: Any, label: str, *, org: int = 1) -> None:
        self.env = env
        self.world = env.world(label, org=org)
        self.ctx: TenantContext = self.world.ctx
        self.project_id: uuid.UUID = self.world.project_id
        self.datasets = {
            "train": env.dataset(self.world, "train-a", "training", 200),
            "val": env.dataset(self.world, "val-b", "validation", 40),
            "dev": env.dataset(self.world, "dev-set", "candidate_development", 8),
            "held": env.dataset(self.world, "held-out", "held_out_evaluation", 8),
        }
        proposed, error = env.propose(self.world, "campaign", list(self.datasets.values()))
        if error is not None:
            raise error
        locked, error = env.approve(self.world, proposed)
        if error is not None:
            raise error
        self.manifest_id: uuid.UUID = proposed.id
        row = env.rows(
            "SELECT status, frozen_manifest_sha256 FROM dataset_split_manifests WHERE id=:m",
            m=self.manifest_id,
        )[0]
        assert row[0] == "locked", row
        self.frozen: str = row[1]
        self.artifact_id = self.new_artifact(f"{label}-base")

    def run(self, fn: Callable[[Session], Any]) -> tuple[Any, BaseException | None]:
        result: tuple[Any, BaseException | None] = self.env.run(self.ctx, fn)
        return result

    def new_artifact(self, name: str, *, ready: bool = True) -> uuid.UUID:
        def build(session: Session) -> uuid.UUID:
            artifact = artifact_crud.create_artifact(
                session,
                self.ctx,
                display_name=f"{name}-{uuid.uuid4().hex[:6]}",
                source_type="huggingface",
                source_uri="org/base-model",
                model_family="llama",
                base_model_revision="rev1",
                license_id="apache-2.0",
                actor=ARTIFACT_ACTOR,
            )
            session.flush()
            artifact_crud.update_artifact(
                session,
                self.ctx,
                artifact.id,
                weights_digest="sha256:" + hashlib.sha256(name.encode()).hexdigest(),
                actor=ARTIFACT_ACTOR,
            )
            if ready:
                artifact_crud.accept_license(session, self.ctx, artifact.id, actor=ARTIFACT_ACTOR)
                artifact_crud.mark_ready(session, self.ctx, artifact.id, actor=ARTIFACT_ACTOR)
            artifact_id: uuid.UUID = artifact.id
            return artifact_id

        artifact_id, error = self.run(build)
        if error is not None:
            raise error
        assert isinstance(artifact_id, uuid.UUID)
        return artifact_id

    def budget(
        self, total: str = "1000", currency: str = "USD", scopes: tuple[str, ...] = ("training",)
    ) -> uuid.UUID:
        policy, error = self.run(
            lambda s: budget_policy_crud.create_budget_policy(
                s,
                self.ctx,
                project_id=self.project_id,
                total_amount=Decimal(total),
                currency=currency,
                scopes=list(scopes),
                actor=BUDGET_ACTOR,
            )
        )
        if error is not None:
            raise error
        policy_id: uuid.UUID = policy.id
        return policy_id

    def body(self, **over: Any) -> dict[str, Any]:
        body = copy.deepcopy(BASE_BODY)
        body["base"]["trainable_artifact_id"] = str(self.artifact_id)
        body["tokenizer"]["revision"] = "rev1"
        for slot in ("train", "validation"):
            body["data"][slot]["split_manifest_id"] = str(self.manifest_id)
        body["data"]["frozen_manifest_sha256"] = self.frozen
        body["budget"] = {"max_cost": "10.5", "currency": "USD", "max_wall_clock_seconds": 3600}
        for path, value in over.items():
            node = body
            parts = path.split("__")
            for part in parts[:-1]:
                node = node[part]
            node[parts[-1]] = value
        return body

    def create(
        self,
        name: str,
        body: dict[str, Any],
        key: str,
        *,
        actor: store.ExperimentActor = WRITER,
        ctx: TenantContext | None = None,
        project_id: uuid.UUID | None = None,
    ) -> tuple[Any, BaseException | None]:
        use_ctx = ctx or self.ctx
        result: tuple[Any, BaseException | None] = self.env.run(
            use_ctx,
            lambda s: store.create_training_experiment(
                s,
                use_ctx,
                project_id or self.project_id,
                name=name,
                config_body=body,
                idempotency_key=key,
                actor=actor,
            ),
        )
        return result


def code_of(error: BaseException | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, store.TrainingExperimentError):
        return error.code
    return f"{type(error).__name__}: {error}"


def counts(env: Any) -> tuple[int, int]:
    return (
        int(env.scalar("SELECT count(*) FROM training_experiments") or 0),
        int(
            env.scalar(
                "SELECT count(*) FROM audit_events WHERE action='training_experiment.create'"
            )
            or 0
        ),
    )


def refuses(
    env: Any, label: str, code: str, call: Callable[[], tuple[Any, BaseException | None]]
) -> bool:
    """`call` refuses with exactly `code` and writes no row and no audit event."""
    before = counts(env)
    _, error = call()
    got = code_of(error)
    check(f"{label}: refused with {code} (got {got})", got == code)
    check(f"{label}: nothing written ({before} -> {counts(env)})", counts(env) == before)
    return got == code and counts(env) == before


def raw_insert_error(
    env: Any, fx: Fixture, ctx: TenantContext | None = None, **over: Any
) -> BaseException | None:
    """A raw INSERT as sovra_app (no store): the database's own refusal, or None."""
    doc = {
        "schema_version": "training_config.v1",
        "method": "lora",
        "data": {
            "train": {"split_manifest_id": str(fx.manifest_id), "role": "train"},
            "validation": {"split_manifest_id": str(fx.manifest_id), "role": "validation"},
        },
    }
    params: dict[str, Any] = {
        "org": fx.ctx.organization_id,
        "project": fx.project_id,
        "name": f"raw-{uuid.uuid4().hex[:8]}",
        "config": doc,
        "artifact": fx.artifact_id,
        "manifest": fx.manifest_id,
        "frozen": fx.frozen,
        "policy": env.scalar("SELECT id FROM budget_policies WHERE project_id=:p", p=fx.project_id),
        "key": f"raw-{uuid.uuid4().hex}",
    }
    params.update(over)
    params["config"] = json.dumps(params["config"])
    error: BaseException | None = split_proof.raw_error(
        env,
        ctx or fx.ctx,
        "INSERT INTO training_experiments (organization_id, project_id, name, version, method, "
        "config_schema_version, config_json, config_sha256, trainable_artifact_id, "
        "split_manifest_id, frozen_manifest_sha256, budget_policy_id, max_cost, currency, "
        "security_profile, requested_by, request_id, idempotency_key, request_sha256) VALUES "
        "(:org, :project, :name, 1, 'lora', 'training_config.v1', CAST(:config AS jsonb), "
        f"'{'a' * 64}', :artifact, :manifest, :frozen, :policy, 10, 'USD', 'standard', 'raw-user', "
        f"'raw-req', :key, '{'b' * 64}')",
        **params,
    )
    return error


def refused_by(error: BaseException | None, *needles: str) -> bool:
    """The database refused, and every needle is in its message or is its violated constraint
    (a trigger's RAISE carries its name in the diagnostics, not in the message)."""
    if error is None:
        return False
    diag = getattr(getattr(error, "orig", None), "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    return all(n in str(error) or n == constraint for n in needles)


# ----------------------------------------------------------------------------------------
# sections
# ----------------------------------------------------------------------------------------


def check_catalog(env: Any) -> None:
    def privilege(name: str) -> bool:
        return bool(
            env.scalar(
                "SELECT has_table_privilege('sovra_app', 'training_experiments', :p)", p=name
            )
        )

    check("sovra_app may SELECT and INSERT", privilege("SELECT") and privilege("INSERT"))
    for name in ("UPDATE", "DELETE", "TRUNCATE"):
        check(f"sovra_app may not {name}", not privilege(name))
    check(
        "no column-level UPDATE privilege either",
        int(
            env.scalar(
                "SELECT count(*) FROM information_schema.column_privileges WHERE grantee='sovra_app' "
                "AND table_name='training_experiments' AND privilege_type='UPDATE'"
            )
            or 0
        )
        == 0,
    )
    flags = env.rows(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='training_experiments'"
    )[0]
    check("row-level security is ENABLED and FORCED", flags[0] is True and flags[1] is True)
    policies = env.rows(
        "SELECT policyname, cmd, qual, with_check FROM pg_policies "
        "WHERE tablename='training_experiments'"
    )
    check(
        f"exactly one tenant_isolation policy on organization_id: {policies}",
        len(policies) == 1
        and policies[0][0] == "tenant_isolation"
        and policies[0][1] == "ALL"
        and "organization_id" in policies[0][2]
        and "app.current_organization_id" in policies[0][2]
        and "organization_id" in policies[0][3],
    )


def section_no_budget(env: Any, fx: Fixture) -> None:
    refuses(
        env,
        "a project with no budget policy",
        "budget_policy_missing",
        lambda: fx.create("exp-nobudget", fx.body(), "key-nobudget"),
    )


def section_happy(env: Any, fx: Fixture) -> dict[str, Any]:
    before = counts(env)
    started = time.monotonic()
    created, error = fx.create("exp-a", fx.body(), "key-a-1")
    elapsed = time.monotonic() - started
    check(f"lora create succeeds: {error!r}", error is None and created is not None)
    if error is not None:
        _finish()
    print(f"  measured: create over a 240-row training split took {elapsed:.3f}s")
    exp = created.experiment
    check("created, not replayed", created.replayed is False)
    check("one row and one audit event", counts(env) == (before[0] + 1, before[1] + 1))
    row = env.rows(
        "SELECT version, parent_version, status, method, config_schema_version, config_json, "
        "config_sha256, trainable_artifact_id, model_catalog_entry_id, split_manifest_id, "
        "frozen_manifest_sha256, budget_policy_id, max_cost, currency, security_profile, "
        "requested_by, request_id, idempotency_key, organization_id, project_id, name "
        "FROM training_experiments WHERE id=:i",
        i=exp.id,
    )[0]
    doc = row[5]
    independent = hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    check("the stored config_sha256 equals an independent recompute", row[6] == independent)
    check(
        "version 1, no parent, draft, lora, schema v1",
        row[:5] == (1, None, "draft", "lora", "training_config.v1"),
    )
    policy_id = env.scalar("SELECT id FROM budget_policies WHERE project_id=:p", p=fx.project_id)
    check(
        "the id and budget columns are the resolved document's",
        row[7] == fx.artifact_id
        and row[8] is None
        and row[9] == fx.manifest_id
        and row[10] == fx.frozen
        and row[11] == policy_id
        and row[12] == Decimal("10.5")
        and row[13] == "USD"
        and row[14] == "standard"
        and row[15:18] == ("user-ml-1", "req-0701-p2", "key-a-1")
        and row[18] == fx.ctx.organization_id
        and row[19] == fx.project_id
        and row[20] == "exp-a",
    )
    roles = {
        r[0]: (r[1], r[2])
        for r in env.rows(
            "SELECT role, record_count, role_sha256 FROM dataset_split_manifest_roles "
            "WHERE manifest_id=:m",
            m=fx.manifest_id,
        )
    }
    artifact = env.rows(
        "SELECT weights_digest, base_model_revision FROM trainable_artifacts WHERE id=:a",
        a=fx.artifact_id,
    )[0]
    server = doc["server"]
    check(
        f"the server block carries the database's own facts: {server}",
        server
        == {
            "weights_digest": artifact[0].removeprefix("sha256:"),
            "base_revision": artifact[1],
            "split_manifest_version": 1,
            "frozen_manifest_sha256": fx.frozen,
            "train_role_sha256": roles["train"][1],
            "validation_role_sha256": roles["validation"][1],
            "train_count": roles["train"][0],
            "validation_count": roles["validation"][0],
            "budget_policy_id": str(policy_id),
            "budget_policy_row_version": int(
                env.scalar("SELECT row_version FROM budget_policies WHERE id=:p", p=policy_id)
            ),
            "security_profile": "standard",
        },
    )
    check("the effective seed is stored", doc["hyperparameters"]["seed"] == 3407)
    check(
        "no reservation was made",
        env.scalar("SELECT reserved_amount FROM budget_policies WHERE id=:p", p=policy_id)
        == Decimal("0"),
    )
    audit = env.rows(
        "SELECT actor_id, resource_type, details_json, request_id FROM audit_events "
        "WHERE resource_id=:r AND action='training_experiment.create'",
        r=str(exp.id),
    )
    check(
        f"one content-free audit row: {audit}",
        len(audit) == 1
        and audit[0][0] == "user-ml-1"
        and audit[0][1] == "training_experiment"
        and audit[0][2]
        == {
            "project_id": str(fx.project_id),
            "version": 1,
            "method": "lora",
            "config_sha256": row[6],
        }
        and audit[0][3] == "req-0701-p2",
    )

    # qlora + nf4, by the organization admin.
    q, error = fx.create(
        "exp-q",
        fx.body(method="qlora", precision__quantization="nf4"),
        "key-q-1",
        actor=ORG_ADMIN,
    )
    check(
        f"qlora create succeeds: {error!r}",
        error is None and q.experiment.method == "qlora" and q.experiment.version == 1,
    )

    # Replay: same key, same request -> the stored row, nothing written.
    before = counts(env)
    again, error = fx.create("exp-a", fx.body(), "key-a-1")
    check(
        f"a replay returns the stored row: {error!r}",
        error is None and again.replayed is True and again.experiment.id == exp.id,
    )
    check("a replay writes no row and no audit event", counts(env) == before)
    spelled, error = fx.create(
        "exp-a", fx.body(hyperparameters__learning_rate="0.00020"), "key-a-1"
    )
    check(
        f"an equal decimal spelling is the same request: {error!r}",
        error is None and spelled.replayed is True and counts(env) == before,
    )
    refuses(
        env,
        "the same key with another body",
        "idempotency_key_reused",
        lambda: fx.create("exp-a", fx.body(hyperparameters__epochs=4), "key-a-1"),
    )
    refuses(
        env,
        "the same key with another name",
        "idempotency_key_reused",
        lambda: fx.create("exp-other", fx.body(), "key-a-1"),
    )
    # The key is scoped to (organization, actor): another actor may use the same key.
    other, error = fx.create("exp-a", fx.body(), "key-a-1", actor=WRITER_2)
    check(
        f"another actor with the same key creates version 2: {error!r}",
        error is None
        and other.replayed is False
        and other.experiment.version == 2
        and other.experiment.parent_version == 1,
    )
    v3, error = fx.create("exp-a", fx.body(hyperparameters__epochs=5), "key-a-3")
    check(
        f"a changed config is a new version with its parent: {error!r}",
        error is None and v3.experiment.version == 3 and v3.experiment.parent_version == 2,
    )
    check(
        "version 1 is untouched by later versions",
        env.scalar("SELECT config_sha256 FROM training_experiments WHERE id=:i", i=exp.id)
        == row[6],
    )
    return {"exp": exp, "sha": row[6]}


def section_refusals(env: Any, fx: Fixture) -> None:
    for role in ("viewer", "evaluator", "workspace_admin", "ML_ENGINEER", "ml_engineer ", ""):
        actor = store.ExperimentActor("user", "user-x", role, "req-x")
        refuses(
            env,
            f"role {role!r} may not create",
            "actor_not_permitted",
            lambda a=actor: fx.create(
                "exp-role", fx.body(), f"key-role-{uuid.uuid4().hex}", actor=a
            ),
        )
    refuses(
        env,
        "a project id that does not exist",
        "not_found",
        lambda: fx.create("exp-nf", fx.body(), "key-nf-1", project_id=uuid.uuid4()),
    )
    for label, name in (
        ("empty", ""),
        ("blank", "   "),
        ("leading space", " x"),
        ("trailing space", "x "),
        ("201 chars", "n" * 201),
        ("control char", "a\nb"),
        ("not a string", 7),
    ):
        refuses(
            env,
            f"name {label}",
            "invalid_body",
            lambda n=name: fx.create(n, fx.body(), f"key-name-{uuid.uuid4().hex}"),
        )
    for label, key in (
        ("empty", ""),
        ("129 chars", "k" * 129),
        ("non-ASCII", "ké"),
        ("control", "a\tb"),
        ("not a string", None),
    ):
        refuses(
            env,
            f"idempotency key {label}",
            "invalid_body",
            lambda k=key: fx.create("exp-key", fx.body(), k),
        )
    ok, error = fx.create("n" * 200, fx.body(), "k" * 128)
    check(f"a 200-char name and a 128-char key are accepted: {error!r}", error is None)

    # Negative acceptance, server-side, with an independent count.
    for label, over, code in (
        ("rank 0", {"adapter__lora_rank": 0}, "invalid_lora_rank"),
        ("rank 257", {"adapter__lora_rank": 257}, "invalid_lora_rank"),
        ("learning rate 0", {"hyperparameters__learning_rate": "0"}, "invalid_learning_rate"),
        ("learning rate 1", {"hyperparameters__learning_rate": "1"}, "invalid_learning_rate"),
        (
            "length between buckets",
            {"hyperparameters__max_seq_length": 3000},
            "invalid_max_seq_length",
        ),
        ("dtype int8", {"precision__compute_dtype": "int8"}, "unsupported_dtype"),
        ("dtype BF16", {"precision__compute_dtype": "BF16"}, "unsupported_dtype"),
        ("unknown key", {"trust_remote_code": True}, "invalid_body"),
        ("full_sft", {"method": "full_sft"}, "method_not_enabled"),
        ("qlora without nf4", {"method": "qlora"}, "method_quantization_mismatch"),
    ):
        refuses(
            env,
            f"negative acceptance: {label}",
            code,
            lambda o=over: fx.create("exp-neg", fx.body(**o), f"key-neg-{uuid.uuid4().hex}"),
        )
    for slot in ("train", "validation"):
        for role in ("final_qualification", "development_eval", "heldout_test"):
            refuses(
                env,
                f"held-out role {role} in the {slot} slot",
                "heldout_training_source_denied",
                lambda s=slot, r=role: fx.create(
                    "exp-held", fx.body(**{f"data__{s}__role": r}), f"key-held-{uuid.uuid4().hex}"
                ),
            )
    check(
        "no stored document names a held-out role (independent count)",
        int(
            env.scalar(
                "SELECT count(*) FROM training_experiments WHERE "
                "config_json#>>'{data,train,role}' <> 'train' "
                "OR config_json#>>'{data,validation,role}' <> 'validation'"
            )
            or 0
        )
        == 0,
    )
    refuses(
        env,
        "validation role in the train slot",
        "invalid_source_role",
        lambda: fx.create("exp-role2", fx.body(data__train__role="validation"), "key-swap-1"),
    )

    # Facts.
    refuses(
        env,
        "a stale frozen manifest hash",
        "split_manifest_changed",
        lambda: fx.create("exp-f", fx.body(data__frozen_manifest_sha256="0" * 64), "key-f-1"),
    )
    unknown = str(uuid.uuid4())
    refuses(
        env,
        "a manifest id that does not exist",
        "split_manifest_not_locked",
        lambda: fx.create(
            "exp-f",
            fx.body(
                data__train__split_manifest_id=unknown, data__validation__split_manifest_id=unknown
            ),
            "key-f-2",
        ),
    )
    refuses(
        env,
        "an artifact id that does not exist",
        "artifact_not_trainable",
        lambda: fx.create(
            "exp-f", fx.body(base__trainable_artifact_id=str(uuid.uuid4())), "key-f-3"
        ),
    )
    registered = fx.new_artifact("registered-only", ready=False)
    refuses(
        env,
        "an artifact that was never licensed",
        "license_not_accepted",
        lambda: fx.create("exp-f", fx.body(base__trainable_artifact_id=str(registered)), "key-f-4"),
    )
    refuses(
        env,
        "a tokenizer revision that is not the base revision",
        "tokenizer_revision_mismatch",
        lambda: fx.create("exp-f", fx.body(tokenizer__revision="rev2"), "key-f-5"),
    )
    # Budget boundary: total 1000, reserved 0.
    at, error = fx.create("exp-budget", fx.body(budget__max_cost="1000"), "key-b-at")
    check(f"max_cost equal to the available budget is allowed: {error!r}", error is None)
    refuses(
        env,
        "max_cost above the available budget",
        "budget_limit_exceeds_policy",
        lambda: fx.create("exp-budget", fx.body(budget__max_cost="1000.0001"), "key-b-over"),
    )
    refuses(
        env,
        "a budget currency that is not the policy's",
        "budget_currency_mismatch",
        lambda: fx.create("exp-budget", fx.body(budget__currency="EUR"), "key-b-cur"),
    )


def section_catalog(env: Any, fx: Fixture) -> None:
    connection_id, entry_ok, entry_other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_artifact = fx.new_artifact("catalog-other")
    env.execute(
        "INSERT INTO litellm_connections (id, organization_id, name, base_url, secret_ref) "
        "VALUES (:i, :o, :n, 'http://litellm.internal:4000', 'secret/ref')",
        i=connection_id,
        o=fx.ctx.organization_id,
        n=f"conn-{uuid.uuid4().hex[:8]}",
    )
    for entry, artifact, name in (
        (entry_ok, fx.artifact_id, "base-ok"),
        (entry_other, other_artifact, "base-other"),
    ):
        env.execute(
            "INSERT INTO model_catalog_entries (id, organization_id, litellm_connection_id, "
            "public_model_name, trainable_artifact_id, catalog_version) "
            "VALUES (:i, :o, :c, :n, :a, 3)",
            i=entry,
            o=fx.ctx.organization_id,
            c=connection_id,
            n=name,
            a=artifact,
        )
    created, error = fx.create(
        "exp-catalog",
        fx.body(base__model_catalog_entry_id=str(entry_ok), base__catalog_version=3),
        "key-cat-ok",
    )
    check(
        f"a current catalog mapping to the same artifact is stored: {error!r}",
        error is None and created.experiment.model_catalog_entry_id == entry_ok,
    )
    refuses(
        env,
        "a stale catalog version",
        "catalog_mapping_changed",
        lambda: fx.create(
            "exp-catalog",
            fx.body(base__model_catalog_entry_id=str(entry_ok), base__catalog_version=2),
            "key-cat-stale",
        ),
    )
    refuses(
        env,
        "a catalog entry that does not exist",
        "catalog_mapping_changed",
        lambda: fx.create(
            "exp-catalog",
            fx.body(base__model_catalog_entry_id=str(uuid.uuid4()), base__catalog_version=1),
            "key-cat-missing",
        ),
    )
    refuses(
        env,
        "a catalog entry mapped to another artifact",
        "catalog_artifact_mismatch",
        lambda: fx.create(
            "exp-catalog",
            fx.body(base__model_catalog_entry_id=str(entry_other), base__catalog_version=3),
            "key-cat-other",
        ),
    )


def section_reads(env: Any, fx: Fixture, made: dict[str, Any], foreign: Fixture) -> None:
    exp = made["exp"]
    got, error = fx.run(lambda s: store.get_training_experiment(s, fx.ctx, fx.project_id, exp.id))
    check(
        f"get returns the row: {error!r}",
        error is None and got.id == exp.id and got.config_sha256 == made["sha"],
    )
    messages = set()
    for label, call in (
        (
            "a missing id",
            lambda s: store.get_training_experiment(s, fx.ctx, fx.project_id, uuid.uuid4()),
        ),
        (
            "an id read by another organization",
            lambda s: store.get_training_experiment(s, foreign.ctx, fx.project_id, exp.id),
        ),
        (
            "an id read under another organization's own project",
            lambda s: store.get_training_experiment(s, foreign.ctx, foreign.project_id, exp.id),
        ),
    ):
        ctx = foreign.ctx if "organization" in label else fx.ctx
        _, error = env.run(ctx, call)
        check(f"get {label}: not_found (got {code_of(error)})", code_of(error) == "not_found")
        messages.add(str(error))
    check(f"missing and foreign are the identical refusal: {messages}", messages == {"not_found"})
    # Another project of the same organization never serves this project's experiment.
    sibling = env.world("sibling")
    _, error = fx.run(
        lambda s: store.get_training_experiment(s, fx.ctx, sibling.project_id, exp.id)
    )
    check(
        f"another project of the same organization: not_found (got {code_of(error)})",
        code_of(error) == "not_found",
    )
    listed, error = fx.run(
        lambda s: store.list_training_experiments(s, fx.ctx, sibling.project_id, limit=50)
    )
    check(f"the sibling project lists nothing: {error!r}", error is None and listed == [])

    page, error = fx.run(
        lambda s: store.list_training_experiments(s, fx.ctx, fx.project_id, limit=200)
    )
    expected = env.rows(
        "SELECT name, version FROM training_experiments WHERE project_id=:p "
        'ORDER BY name COLLATE "C", version',
        p=fx.project_id,
    )
    names = [(e.name, e.version) for e in page] if error is None else []
    check(
        f"list returns every experiment of the project: {error!r}",
        error is None and sorted(names) == sorted((r[0], r[1]) for r in expected),
    )
    check(
        "list is ordered by name, then version",
        all((a[0] != b[0]) or (a[1] < b[1]) for a, b in zip(names, names[1:], strict=False))
        and [n for n, _ in names] == [n for n, _ in sorted(names, key=lambda x: names.index(x))],
    )
    first, _ = fx.run(lambda s: store.list_training_experiments(s, fx.ctx, fx.project_id, limit=2))
    rest, _ = fx.run(
        lambda s: store.list_training_experiments(
            s, fx.ctx, fx.project_id, limit=200, after=(first[-1].name, first[-1].version)
        )
    )
    check(
        "paging with `after` continues without a gap or a repeat",
        [(e.name, e.version) for e in first] + [(e.name, e.version) for e in rest] == names,
    )
    for label, kwargs in (
        ("limit 0", {"limit": 0}),
        ("limit 201", {"limit": 201}),
        ("limit True", {"limit": True}),
        ("after a string", {"limit": 5, "after": "exp-a"}),
    ):
        _, error = fx.run(
            lambda s, k=kwargs: store.list_training_experiments(s, fx.ctx, fx.project_id, **k)
        )
        check(f"list {label} is a ValueError: {error!r}", isinstance(error, ValueError))
    _, error = env.run(
        foreign.ctx,
        lambda s: store.list_training_experiments(s, foreign.ctx, fx.project_id, limit=5),
    )
    check(f"list of a foreign project: not_found ({code_of(error)})", code_of(error) == "not_found")

    # A superuser edit of the stored document: the row is never served.
    victim, error = fx.create("exp-tamper", fx.body(), "key-tamper-1")
    assert error is None, error

    def tampered_refused() -> bool:
        _, get_error = fx.run(
            lambda s: store.get_training_experiment(s, fx.ctx, fx.project_id, victim.experiment.id)
        )
        _, list_error = fx.run(
            lambda s: store.list_training_experiments(s, fx.ctx, fx.project_id, limit=200)
        )
        _, replay_error = fx.create("exp-tamper", fx.body(), "key-tamper-1")
        return (
            code_of(get_error) == "config_integrity_failed"
            and code_of(list_error) == "config_integrity_failed"
            and code_of(replay_error) == "config_integrity_failed"
        )

    env.execute(
        "UPDATE training_experiments SET config_json = jsonb_set(config_json, "
        "'{hyperparameters,epochs}', '19') WHERE id=:i",
        i=victim.experiment.id,
    )
    check("an edited document is refused by get, list and replay", tampered_refused())
    with code_fault("_verified", lambda experiment: experiment):
        check("FAULT read integrity check removed -> RED", not tampered_refused())
    env.execute(
        "UPDATE training_experiments SET config_json = jsonb_set(config_json, "
        "'{hyperparameters,epochs}', '3') WHERE id=:i",
        i=victim.experiment.id,
    )
    restored, error = fx.run(
        lambda s: store.get_training_experiment(s, fx.ctx, fx.project_id, victim.experiment.id)
    )
    check(f"the restored document is served again: {error!r}", error is None)


class code_fault:  # noqa: N801 -- used as a context manager keyword
    """Replace one attribute of the live store module; always restored."""

    def __init__(self, name: str, replacement: Any) -> None:
        self.name, self.replacement = name, replacement
        self.saved: Any = None

    def __enter__(self) -> None:
        self.saved = getattr(store, self.name)
        setattr(store, self.name, self.replacement)

    def __exit__(self, *exc: object) -> None:
        setattr(store, self.name, self.saved)


def section_raw(env: Any, fx: Fixture, foreign: Fixture) -> None:
    check(
        f"a well-formed raw INSERT is accepted (control): {raw_insert_error(env, fx)!r}",
        raw_insert_error(env, fx) is None,
    )

    def held_out_doc(role: str) -> dict[str, Any]:
        return {
            "schema_version": "training_config.v1",
            "method": "lora",
            "data": {
                "train": {"split_manifest_id": str(fx.manifest_id), "role": role},
                "validation": {"split_manifest_id": str(fx.manifest_id), "role": "validation"},
            },
        }

    def check_refuses_held_out() -> bool:
        return all(
            refused_by(
                raw_insert_error(env, fx, config=held_out_doc(role)),
                "ck_training_experiments_config_consistency",
            )
            for role in ("final_qualification", "development_eval", "heldout_test", "Train", "")
        )

    check("a raw INSERT naming a held-out role is refused by the CHECK", check_refuses_held_out())
    for label, doc in (
        (
            "a validation slot that trains on final_qualification",
            {
                "schema_version": "training_config.v1",
                "method": "lora",
                "data": {
                    "train": {"split_manifest_id": str(fx.manifest_id), "role": "train"},
                    "validation": {
                        "split_manifest_id": str(fx.manifest_id),
                        "role": "final_qualification",
                    },
                },
            },
        ),
        (
            "a document of another manifest",
            {
                "schema_version": "training_config.v1",
                "method": "lora",
                "data": {
                    "train": {"split_manifest_id": str(uuid.uuid4()), "role": "train"},
                    "validation": {"split_manifest_id": str(fx.manifest_id), "role": "validation"},
                },
            },
        ),
        ("a document with another method", {**held_out_doc("train"), "method": "qlora"}),
        (
            "a document with no data block",
            {"schema_version": "training_config.v1", "method": "lora"},
        ),
        ("a non-object document", ["train"]),
    ):
        error = raw_insert_error(env, fx, config=doc)
        check(
            f"raw INSERT with {label}: {error!r}",
            refused_by(error, "ck_training_experiments_config_consistency"),
        )

    # Trigger: a proposed manifest, a stale frozen hash, a revoked license, a foreign project.
    proposed_fx_world = env.world("proposed")
    datasets = [
        env.dataset(proposed_fx_world, "train-a", "training", 200),
        env.dataset(proposed_fx_world, "val-b", "validation", 40),
        env.dataset(proposed_fx_world, "dev-set", "candidate_development", 8),
        env.dataset(proposed_fx_world, "held-out", "held_out_evaluation", 8),
    ]
    proposed, error = env.propose(proposed_fx_world, "campaign", datasets)
    assert error is None, error

    def trigger_refusals() -> dict[str, bool]:
        return {
            "proposed manifest": refused_by(
                raw_insert_error(
                    env,
                    fx,
                    manifest=proposed.id,
                    config={
                        "schema_version": "training_config.v1",
                        "method": "lora",
                        "data": {
                            "train": {"split_manifest_id": str(proposed.id), "role": "train"},
                            "validation": {
                                "split_manifest_id": str(proposed.id),
                                "role": "validation",
                            },
                        },
                    },
                ),
                "tr_training_experiments_manifest_locked",
            ),
            "stale frozen hash": refused_by(
                raw_insert_error(env, fx, frozen="0" * 64),
                "tr_training_experiments_manifest_frozen",
            ),
            "unlicensed artifact": refused_by(
                raw_insert_error(env, fx, artifact=fx.new_artifact("raw-unlicensed", ready=False)),
                "tr_training_experiments_artifact_ready",
            ),
        }

    outcome = trigger_refusals()
    for label, refused in outcome.items():
        check(f"raw INSERT refused by the trigger: {label}", refused)
    revoked = fx.new_artifact("raw-revoked")
    _, error = fx.run(
        lambda s: artifact_crud.revoke_license(s, fx.ctx, revoked, actor=ARTIFACT_ACTOR)
    )
    assert error is None, error
    check(
        "raw INSERT naming a license-revoked artifact is refused by the trigger",
        refused_by(
            raw_insert_error(env, fx, artifact=revoked), "tr_training_experiments_artifact_ready"
        ),
    )
    sibling = Fixture(env, "raw-sibling")
    sibling.budget()
    check(
        "raw INSERT naming a manifest of another project is refused by the trigger",
        refused_by(
            raw_insert_error(
                env,
                fx,
                manifest=sibling.manifest_id,
                frozen=sibling.frozen,
                config={
                    "schema_version": "training_config.v1",
                    "method": "lora",
                    "data": {
                        "train": {"split_manifest_id": str(sibling.manifest_id), "role": "train"},
                        "validation": {
                            "split_manifest_id": str(sibling.manifest_id),
                            "role": "validation",
                        },
                    },
                },
            ),
            "tr_training_experiments_manifest_locked",
        ),
    )
    check(
        "raw INSERT naming the budget policy of another project is refused by the trigger",
        refused_by(
            raw_insert_error(
                env,
                fx,
                policy=env.scalar(
                    "SELECT id FROM budget_policies WHERE project_id=:p", p=sibling.project_id
                ),
            ),
            "tr_training_experiments_budget_project",
        ),
    )

    error = split_proof.raw_error(
        env,
        fx.ctx,
        "UPDATE training_experiments SET status='draft' WHERE project_id=:p",
        p=fx.project_id,
    )
    check(f"raw UPDATE is refused by grant: {error!r}", refused_by(error, "permission denied"))
    error = split_proof.raw_error(
        env, fx.ctx, "DELETE FROM training_experiments WHERE project_id=:p", p=fx.project_id
    )
    check(f"raw DELETE is refused by grant: {error!r}", refused_by(error, "permission denied"))

    # Cross-tenant.
    def cross_tenant_refused() -> bool:
        return raw_insert_error(env, fx, ctx=foreign.ctx) is not None

    def foreign_sees_nothing() -> bool:
        with tenant_scoped_session_for(env.factory, foreign.ctx) as session:
            seen = session.execute(
                text("SELECT count(*) FROM training_experiments WHERE organization_id=:o"),
                {"o": fx.ctx.organization_id},
            ).scalar()
        return int(seen or 0) == 0

    check("org2 inserting a row carrying org1 ids is refused by RLS", cross_tenant_refused())
    check("org2 sees none of org1's experiments in raw SQL", foreign_sees_nothing())
    own_org_other_ids = raw_insert_error(
        env,
        foreign,
        artifact=fx.artifact_id,
    )
    check(
        f"org2's own row naming org1's artifact is refused: {own_org_other_ids!r}",
        own_org_other_ids is not None,
    )

    # Database faults: each is applied by the owner, must turn its check RED, and is reverted.
    def db_fault(label: str, apply: str, revert: str, still_refused: Callable[[], bool]) -> None:
        env.execute(apply)
        try:
            check(f"FAULT {label} -> RED", not still_refused())
        finally:
            # rows a fault let in are removed first, so the protection can be put back
            env.execute("DELETE FROM training_experiments WHERE requested_by='raw-user'")
            env.execute(revert)
        check(f"after reverting {label} the protection holds again", still_refused())

    def update_refused() -> bool:
        return refused_by(
            split_proof.raw_error(
                env,
                fx.ctx,
                "UPDATE training_experiments SET status='draft' WHERE project_id=:p",
                p=fx.project_id,
            ),
            "permission denied",
        )

    def delete_refused() -> bool:
        return refused_by(
            split_proof.raw_error(
                env,
                fx.ctx,
                "DELETE FROM training_experiments WHERE name=:n",
                n="no-such-experiment",
            ),
            "permission denied",
        )

    db_fault(
        "UPDATE granted",
        'GRANT UPDATE ON training_experiments TO "sovra_app"',
        'REVOKE UPDATE ON training_experiments FROM "sovra_app"',
        update_refused,
    )
    db_fault(
        "DELETE granted",
        'GRANT DELETE ON training_experiments TO "sovra_app"',
        'REVOKE DELETE ON training_experiments FROM "sovra_app"',
        delete_refused,
    )
    db_fault(
        "row-level security disabled",
        "ALTER TABLE training_experiments DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE training_experiments ENABLE ROW LEVEL SECURITY",
        foreign_sees_nothing,
    )
    constraint = env.scalar(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='ck_training_experiments_config_consistency'"
    )
    db_fault(
        "the held-out CHECK dropped",
        "ALTER TABLE training_experiments DROP CONSTRAINT ck_training_experiments_config_consistency",
        "ALTER TABLE training_experiments ADD CONSTRAINT "
        f"ck_training_experiments_config_consistency {constraint}",
        check_refuses_held_out,
    )
    # With the trigger out of the way, the policy's WITH CHECK alone refuses a cross-tenant row.
    env.execute(
        "ALTER TABLE training_experiments DISABLE TRIGGER tr_training_experiments_insert_guard"
    )
    try:
        error = raw_insert_error(env, fx, ctx=foreign.ctx)
        check(
            f"the policy WITH CHECK refuses org2 writing an org1 row: {error!r}",
            refused_by(error, "row-level security"),
        )
    finally:
        env.execute("DELETE FROM training_experiments WHERE requested_by='raw-user'")
        env.execute(
            "ALTER TABLE training_experiments ENABLE TRIGGER tr_training_experiments_insert_guard"
        )
    db_fault(
        "the lock trigger disabled",
        "ALTER TABLE training_experiments DISABLE TRIGGER tr_training_experiments_insert_guard",
        "ALTER TABLE training_experiments ENABLE TRIGGER tr_training_experiments_insert_guard",
        lambda: all(trigger_refusals().values()),
    )
    # raw rows left by the faults are removed by the owner so later counts stay meaningful.
    env.execute("DELETE FROM training_experiments WHERE requested_by='raw-user'")


def section_force_rls(env: Any, fx: Fixture, foreign: Fixture) -> None:
    """FORCE matters for the table owner: without it the owner role reads every tenant's rows."""
    owner_url = env.owner.url

    def owner_scoped_count() -> int:
        engine = create_engine(owner_url)
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL row_security = on"))
                conn.execute(
                    text("SELECT set_config('app.current_organization_id', :o, true)"),
                    {"o": str(foreign.ctx.organization_id)},
                )
                is_super = conn.execute(
                    text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                ).scalar()
                if is_super:
                    return -1
                return int(
                    conn.execute(
                        text("SELECT count(*) FROM training_experiments WHERE organization_id=:o"),
                        {"o": fx.ctx.organization_id},
                    ).scalar()
                    or 0
                )
        finally:
            engine.dispose()

    seen = owner_scoped_count()
    if seen == -1:
        # The migration role bypasses RLS (superuser): FORCE is proven on the catalog flag, and by
        # a non-superuser table owner created for this check.
        env.execute("DROP ROLE IF EXISTS ftf0701_owner_probe")
        env.execute("CREATE ROLE ftf0701_owner_probe NOLOGIN")
        previous = env.scalar(
            "SELECT tableowner FROM pg_tables WHERE tablename='training_experiments'"
        )
        env.execute("ALTER TABLE training_experiments OWNER TO ftf0701_owner_probe")
        try:

            def probe() -> int:
                with env.owner.begin() as conn:
                    conn.execute(text("SET LOCAL ROLE ftf0701_owner_probe"))
                    conn.execute(
                        text("SELECT set_config('app.current_organization_id', :o, true)"),
                        {"o": str(foreign.ctx.organization_id)},
                    )
                    return int(
                        conn.execute(
                            text(
                                "SELECT count(*) FROM training_experiments WHERE organization_id=:o"
                            ),
                            {"o": fx.ctx.organization_id},
                        ).scalar()
                        or 0
                    )

            check("with FORCE, the table owner in org2's scope sees no org1 row", probe() == 0)
            env.execute("ALTER TABLE training_experiments NO FORCE ROW LEVEL SECURITY")
            try:
                check("FAULT FORCE dropped -> RED (the owner reads org1 rows)", probe() > 0)
            finally:
                env.execute("ALTER TABLE training_experiments FORCE ROW LEVEL SECURITY")
            check("after restoring FORCE the owner sees none again", probe() == 0)
        finally:
            env.execute(f'ALTER TABLE training_experiments OWNER TO "{previous}"')
            env.execute("DROP ROLE IF EXISTS ftf0701_owner_probe")
    else:
        check("with FORCE, the table owner in org2's scope sees no org1 row", seen == 0)
        env.execute("ALTER TABLE training_experiments NO FORCE ROW LEVEL SECURITY")
        try:
            check(
                "FAULT FORCE dropped -> RED (the owner reads org1 rows)", owner_scoped_count() > 0
            )
        finally:
            env.execute("ALTER TABLE training_experiments FORCE ROW LEVEL SECURITY")


def section_live_facts(env: Any) -> None:
    """Grant, quality report and license changes after lock, through the certified APIs."""
    fx = Fixture(env, "live")
    fx.budget()
    ok, error = fx.create("exp-live", fx.body(), "key-live-0")
    check(f"control: the live fixture creates: {error!r}", error is None)
    conn_actor = connection_crud.ActorInfo("user", "user-org-admin", "req-0701-conn")
    conn = fx.world.conns["all"]

    def narrowed_refused() -> bool:
        return refuses(
            env,
            "a grant narrowed after lock",
            "dataset_grant_changed",
            lambda: fx.create("exp-live", fx.body(), f"key-live-{uuid.uuid4().hex}"),
        )

    _, error = fx.run(
        lambda s: connection_crud.update_dataset_source_connection(
            s,
            fx.ctx,
            fx.project_id,
            conn,
            allowed_purposes=["validation", "candidate_development", "held_out_evaluation"],
            actor=conn_actor,
        )
    )
    assert error is None, error
    narrowed_refused()

    # FAULT: the live FTF-0519 read replaced by a raw manifest read (no grant re-check).
    def raw_read(
        session: Session, context: TenantContext, *, project_id: Any, manifest_id: Any
    ) -> Any:
        rows = session.execute(
            text(
                "SELECT role FROM dataset_split_assignments WHERE manifest_id=:m "
                "AND role IN ('train','validation')"
            ),
            {"m": manifest_id},
        ).all()

        class _Row:
            def __init__(self, role: str) -> None:
                self.role = role

        manifest = session.execute(
            text("SELECT version, frozen_manifest_sha256 FROM dataset_split_manifests WHERE id=:m"),
            {"m": manifest_id},
        ).one()
        return split_store.TrainingSplitView(
            manifest_id=manifest_id,
            version=manifest[0],
            frozen_manifest_sha256=manifest[1],
            rows=tuple(_Row(r[0]) for r in rows),
        )

    before = counts(env)
    with code_fault("load_training_split", raw_read):
        _, error = fx.create("exp-live", fx.body(), "key-live-fault-raw")
    check(
        "FAULT load_training_split replaced by a raw manifest read -> RED (the narrowed grant "
        f"is not noticed: {code_of(error)})",
        error is None and counts(env)[0] == before[0] + 1,
    )

    _, error = fx.run(
        lambda s: connection_crud.update_dataset_source_connection(
            s,
            fx.ctx,
            fx.project_id,
            conn,
            allowed_purposes=list(split_proof.ALL_PURPOSES),
            actor=conn_actor,
        )
    )
    assert error is None, error
    ok, error = fx.create("exp-live", fx.body(), "key-live-restored")
    check(f"the restored grant creates again: {error!r}", error is None)
    _, error = fx.run(
        lambda s: connection_crud.revoke_dataset_source_connection(
            s, fx.ctx, fx.project_id, conn, actor=conn_actor
        )
    )
    assert error is None, error
    refuses(
        env,
        "a grant revoked after lock",
        "dataset_grant_changed",
        lambda: fx.create("exp-live", fx.body(), "key-live-revoked"),
    )

    # Quality report replaced after lock.
    fq = Fixture(env, "quality")
    fq.budget()
    env.quality(
        fq.world,
        fq.datasets["train"],
        extra=(quality_proof._extra("seeded_note", (1,), severity="warning"),),
    )
    refuses(
        env,
        "a quality report replaced after lock",
        "quality_report_changed",
        lambda: fq.create("exp-q", fq.body(), "key-quality-1"),
    )

    # License revoked through FTF-0305.
    fl = Fixture(env, "license")
    fl.budget()
    ok, error = fl.create("exp-lic", fl.body(), "key-lic-0")
    check(f"control: the license fixture creates: {error!r}", error is None)
    _, error = fl.run(
        lambda s: artifact_crud.revoke_license(s, fl.ctx, fl.artifact_id, actor=ARTIFACT_ACTOR)
    )
    assert error is None, error
    refuses(
        env,
        "a license revoked after the first experiment",
        "license_not_accepted",
        lambda: fl.create("exp-lic", fl.body(), "key-lic-1"),
    )

    # FAULT: the artifact facts no longer read the license / status.
    real_artifact_facts = store._artifact_facts

    def always_trainable(session: Session, context: TenantContext, artifact_id: uuid.UUID) -> Any:
        facts = real_artifact_facts(session, context, artifact_id)
        return type(facts)(
            artifact_id=facts.artifact_id,
            status="ready",
            capability="trainable_open_weight",
            weights_digest=facts.weights_digest,
            base_revision=facts.base_revision,
            license_accepted=True,
        )

    with code_fault("_artifact_facts", always_trainable):
        _, error = fl.create("exp-lic", fl.body(), "key-lic-fault")
    check(
        "FAULT license check skipped -> the database trigger still refuses the row "
        f"({code_of(error)})",
        code_of(error) == "artifact_not_trainable",
    )

    # Budget scope and currency.
    fs = Fixture(env, "scope")
    fs.budget(scopes=("evaluation",))
    refuses(
        env,
        "a budget policy without the training scope",
        "budget_scope_not_authorized",
        lambda: fs.create("exp-s", fs.body(), "key-scope-1"),
    )
    real_budget_facts = store._budget_facts

    def generous(session: Session, context: TenantContext, project_id: uuid.UUID) -> Any:
        facts = real_budget_facts(session, context, project_id)
        assert facts is not None
        return type(facts)(
            policy_id=facts.policy_id,
            row_version=facts.row_version,
            currency=facts.currency,
            scopes=("training",),
            total=facts.total,
            reserved=facts.reserved,
        )

    before = counts(env)
    with code_fault("_budget_facts", generous):
        _, error = fs.create("exp-s", fs.body(), "key-scope-fault")
    check(
        f"FAULT budget check skipped -> RED (a row is written: {code_of(error)})",
        error is None and counts(env)[0] == before[0] + 1,
    )


def section_concurrency(env: Any, fx: Fixture) -> None:
    def two_threads(
        name_a: str, key_a: str, name_b: str, key_b: str, body_b: dict[str, Any] | None = None
    ) -> list[tuple[Any, BaseException | None]]:
        results: list[tuple[Any, BaseException | None]] = [(None, None), (None, None)]
        first_inserted = threading.Event()

        def first() -> None:
            try:
                with tenant_scoped_session_for(env.factory, fx.ctx) as session:
                    created = store.create_training_experiment(
                        session,
                        fx.ctx,
                        fx.project_id,
                        name=name_a,
                        config_body=fx.body(),
                        idempotency_key=key_a,
                        actor=WRITER,
                    )
                    session.flush()
                    first_inserted.set()
                    time.sleep(1.0)  # hold the transaction open while the second request runs
                results[0] = (created, None)
            except BaseException as exc:  # noqa: BLE001
                first_inserted.set()
                results[0] = (None, exc)

        def second() -> None:
            first_inserted.wait(timeout=30)
            results[1] = env.run(
                fx.ctx,
                lambda s: store.create_training_experiment(
                    s,
                    fx.ctx,
                    fx.project_id,
                    name=name_b,
                    config_body=body_b or fx.body(),
                    idempotency_key=key_b,
                    actor=WRITER,
                ),
            )

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results

    def same_name_gives_two_versions(name: str) -> bool:
        results = two_threads(name, f"k1-{name}", name, f"k2-{name}")
        versions = sorted(r[0].experiment.version for r in results if r[1] is None and r[0])
        return versions == [1, 2]

    check(
        "two concurrent creates of the same name are versions 1 and 2",
        same_name_gives_two_versions("exp-race"),
    )

    def unlocked(session: Session, context: TenantContext, project_id: uuid.UUID) -> Any:
        row = session.execute(
            text("SELECT id, security_profile FROM projects WHERE id=:p"), {"p": project_id}
        ).first()
        assert row is not None
        return store._ProjectRow(id=row[0], security_profile=row[1])

    with code_fault("_locked_project", unlocked):
        check(
            "FAULT version assignment without the row lock -> RED",
            not same_name_gives_two_versions("exp-race-fault"),
        )

    before = counts(env)
    results = two_threads("exp-key-race", "key-race-same", "exp-key-race", "key-race-same")
    ids = {r[0].experiment.id for r in results if r[1] is None and r[0]}
    check(
        f"two concurrent creates with the same key are exactly one row: {results}",
        len(ids) == 1
        and counts(env) == (before[0] + 1, before[1] + 1)
        and sorted(r[0].replayed for r in results if r[0]) == [False, True],
    )

    # The same key racing with another body, with the project lock removed: the unique index is
    # the backstop and the loser is `idempotency_key_reused`, never a second row.
    before = counts(env)
    with code_fault("_locked_project", unlocked):
        results = two_threads(
            "exp-key-race-2",
            "key-race-other",
            "exp-key-race-2b",
            "key-race-other",
            fx.body(hyperparameters__epochs=7),
        )
    codes = sorted(str(code_of(r[1])) for r in results)
    check(
        f"the idempotency index is the backstop for a racing key: {codes}",
        codes == ["None", "idempotency_key_reused"] and counts(env)[0] == before[0] + 1,
    )


def section_idempotency_faults(env: Any, fx: Fixture) -> None:
    real_find = store._find_by_idempotency_key
    first, error = fx.create("exp-idem", fx.body(), "key-idem-1")
    assert error is None, error

    def other_actor_gets_own_row(key: str) -> bool:
        created, create_error = fx.create("exp-idem", fx.body(), key, actor=WRITER_2)
        return create_error is None and created.replayed is False

    def widened(session: Session, context: TenantContext, actor_id: str, key: str) -> Any:
        row = session.execute(
            text("SELECT requested_by FROM training_experiments WHERE idempotency_key=:k LIMIT 1"),
            {"k": key},
        ).first()
        return real_find(session, context, row[0] if row else actor_id, key)

    with code_fault("_find_by_idempotency_key", widened):
        check(
            "FAULT idempotency scope widened to the organization -> RED",
            not other_actor_gets_own_row("key-idem-1"),
        )
    check(
        "another actor's request with the same key is its own row",
        other_actor_gets_own_row("key-idem-1"),
    )

    def reuse_refused() -> bool:
        _, reuse_error = fx.create("exp-idem", fx.body(hyperparameters__epochs=9), "key-idem-1")
        return code_of(reuse_error) == "idempotency_key_reused"

    def narrowed(session: Session, context: TenantContext, actor_id: str, key: str) -> Any:
        return None  # (actor, key, body): another body never finds the stored row

    before = counts(env)
    with code_fault("_find_by_idempotency_key", narrowed):
        _, narrowed_error = fx.create("exp-idem", fx.body(hyperparameters__epochs=9), "key-idem-1")
    check(
        "FAULT idempotency narrowed to (actor, key, body) -> RED (no clean reuse refusal: "
        f"{type(narrowed_error).__name__}), and the unique index still lets no second row in",
        code_of(narrowed_error) != "idempotency_key_reused"
        and narrowed_error is not None
        and counts(env) == before,
    )
    check("the same key with another body is idempotency_key_reused", reuse_refused())


def section_transaction(env: Any, fx: Fixture) -> None:
    """The row and its audit event commit or roll back together."""

    def rolled_back_leaves_nothing() -> bool:
        before = counts(env)
        try:
            with tenant_scoped_session_for(env.factory, fx.ctx) as session:
                store.create_training_experiment(
                    session,
                    fx.ctx,
                    fx.project_id,
                    name="exp-rollback",
                    config_body=fx.body(),
                    idempotency_key=f"key-rb-{uuid.uuid4().hex}",
                    actor=WRITER,
                )
                raise RuntimeError("abort the unit of work")
        except RuntimeError:
            pass
        return counts(env) == before

    check("a rolled-back create leaves no row and no audit event", rolled_back_leaves_nothing())
    real_audit = store.write_audit_event

    def detached_audit(session: Session, **kwargs: Any) -> Any:
        with tenant_scoped_session_for(env.factory, fx.ctx) as other:
            return real_audit(other, **kwargs)

    with code_fault("write_audit_event", detached_audit):
        check(
            "FAULT audit written outside the transaction -> RED",
            not rolled_back_leaves_nothing(),
        )


def section_leak(env: Any, fx: Fixture) -> None:
    """META-LEAK: a canary in user-controlled values never reaches an error, the audit trail or
    the logs."""
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Capture(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        chains: list[str] = []
        for label, over in (
            ("target module", {"adapter__target_modules": [f"{CANARY} proj", "v_proj"]}),
            ("revision", {"tokenizer__revision": f"{CANARY}-rev"}),
            ("max_cost", {"budget__max_cost": f"{CANARY}"}),
            ("role", {"data__train__role": f"{CANARY}"}),
            ("unknown key", {CANARY: 1}),
        ):
            _, error = fx.create(f"exp-{CANARY}", fx.body(**over), f"key-leak-{uuid.uuid4().hex}")
            cursor: BaseException | None = error
            while cursor is not None:
                chains.append(f"{cursor!s} {cursor!r} {getattr(cursor, 'args', ())!r}")
                cursor = cursor.__cause__ or cursor.__context__
            check(f"leak probe {label} was refused", error is not None)
        created, error = fx.create(
            f"exp-{CANARY}",
            fx.body(adapter__target_modules=[f"{CANARY}_proj", "v_proj"]),
            "key-leak-ok",
        )
        check(f"a canary-named experiment with a canary module creates: {error!r}", error is None)
    finally:
        root.removeHandler(handler)
    check("no canary in any exception chain", not any(CANARY in c for c in chains))
    audit_text = env.scalar(
        "SELECT coalesce(string_agg(a::text, '|'), '') FROM audit_events a "
        "WHERE action='training_experiment.create'"
    )
    check("no canary in any audit row", CANARY not in str(audit_text))
    check("no canary in captured logs", not any(CANARY in r for r in records))


def main() -> None:
    migration_url = split_proof._required_env("SOVRA_DATABASE_URL")
    app_password = split_proof._required_env("SOVRA_APP_DB_PASSWORD")
    for args, label in (
        (["upgrade", "head"], "alembic upgrade head"),
        (["downgrade", "-1"], "alembic downgrade -1"),
        (["upgrade", "head"], "alembic upgrade head after downgrade -1"),
    ):
        migrated = split_proof._alembic(args, migration_url)
        check(
            f"{label} runs cleanly (exit {migrated.returncode})\n{migrated.stdout}\n{migrated.stderr}",
            migrated.returncode == 0,
        )
        if migrated.returncode != 0:
            _finish()
    heads = split_proof._alembic(["heads"], migration_url)
    check(
        f"exactly one Alembic head, 0039: {heads.stdout!r}",
        heads.stdout.count("(head)") == 1 and "0039_training_experiments" in heads.stdout,
    )

    env = split_proof.Env()
    env.owner = create_engine(migration_url)
    app_engine = create_engine(split_proof._app_role_url(migration_url, app_password))
    check("the app engine is the restricted sovra_app role", app_engine.url.username == "sovra_app")
    env.factory = sessionmaker(bind=app_engine, expire_on_commit=False)
    check_catalog(env)
    split_proof.seed(env)

    fx = Fixture(env, "main")
    foreign = Fixture(env, "foreign", org=2)
    foreign.budget()
    section_no_budget(env, fx)
    fx.budget()
    made = section_happy(env, fx)
    section_refusals(env, fx)
    section_catalog(env, fx)
    section_reads(env, fx, made, foreign)
    section_idempotency_faults(env, fx)
    section_transaction(env, fx)
    section_concurrency(env, fx)
    section_leak(env, fx)
    section_raw(env, fx, foreign)
    section_force_rls(env, fx, foreign)
    section_live_facts(env)
    if split_proof.FAILURES:
        FAILURES.extend(split_proof.FAILURES)
    _finish()


if __name__ == "__main__":
    main()
