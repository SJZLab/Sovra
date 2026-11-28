#!/usr/bin/env python3
"""FTF-0706 -- Base artifact loading (no-network task tests).

Builds a small real base artifact in the test -- a real `tokenizers` WordLevel tokenizer
(`tokenizer.json`), a real `config.json` / `tokenizer_config.json` and a real safetensors file
(header + float32 buffer) -- so every digest, the strict parsers and the tokenizer load are real.
The object store here is an in-memory versioned bucket answering the exact signed GET-object and
ListObjectsV2 URLs the worker builds, passed as `fetch` (the `guarded_fetch` signature); the same
loader runs against the live SeaweedFS container in
tests/integration/test_ftf_0706_base_artifact_loading_s3.py.

Acceptance: "Load exact approved base/tokenizer revision inside safe worker and verify all digests."
Negative: "Unknown remote code and inconsistent/missing artifacts rejected before sensitive data
mount." Every negative case asserts the typed code and state, that `mount_datasets` was never called
and that the job directory is gone.

Run directly: uv run python3 tests/tasks/test_ftf_0706.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import struct
import sys
import tempfile
import uuid
import xml.sax.saxutils as saxutils
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.outbound_guard import GuardedResponse, OutboundGuardError  # noqa: E402
from tokenizers import Tokenizer, models, pre_tokenizers  # noqa: E402
from workers.training import base_artifact_loading as bal  # noqa: E402
from workers.training import training_config_schema as tcs  # noqa: E402
from workers.training.training_job_preparation import prepare_training_job  # noqa: E402

from app.artifacts.artifact_schema import TrainableArtifact  # noqa: E402
from app.artifacts.private_s3_artifact_resolver import PrivateS3ConnectionConfig  # noqa: E402
from app.services.storage_semantics_client import S3Credentials, S3Endpoint  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0706"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


# -- a small real base artifact ---------------------------------------------------------------

VOCAB = {"[UNK]": 0, "<s>": 1, "</s>": 2, "hello": 3, "world": 4}


def tokenizer_json(vocab: dict[str, int] | None = None) -> bytes:
    tok = Tokenizer(models.WordLevel(vocab=dict(vocab or VOCAB), unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    return tok.to_str().encode("utf-8")


def config_json(**over: Any) -> bytes:
    doc: dict[str, Any] = {
        "model_type": "llama",
        "architectures": ["LlamaForCausalLM"],
        "hidden_size": 4,
        "num_hidden_layers": 1,
        "vocab_size": 8,
        "torch_dtype": "float32",
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


def tokenizer_config_json(**over: Any) -> bytes:
    doc: dict[str, Any] = {
        "bos_token": "<s>",
        "eos_token": "</s>",
        "unk_token": "[UNK]",
        "chat_template": "{% for m in messages %}{{ m['content'] }}{% endfor %}",
    }
    doc.update(over)
    return json.dumps(doc).encode("utf-8")


def safetensors_bytes(tensor: str = "model.embed_tokens.weight", rows: int = 8) -> bytes:
    data = struct.pack(f"<{rows * 4}f", *[i / 10 for i in range(rows * 4)])
    header = {tensor: {"dtype": "F32", "shape": [rows, 4], "data_offsets": [0, len(data)]}}
    raw = json.dumps(header).encode("utf-8")
    raw += b" " * (-len(raw) % 8)
    return struct.pack("<Q", len(raw)) + raw + data


def base_files() -> dict[str, bytes]:
    return {
        "config.json": config_json(),
        "tokenizer.json": tokenizer_json(),
        "tokenizer_config.json": tokenizer_config_json(),
        "model.safetensors": safetensors_bytes(),
    }


# -- in-memory versioned bucket answering the worker's signed URLs -----------------------------

BUCKET = "sovra-models"
TENANT = "org-7"
PREFIX = f"{TENANT}/models/tiny-llama/"
S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


@dataclass
class Store:
    objects: dict[str, list[tuple[str, bytes]]] = field(default_factory=dict)
    page_size: int = 2
    requests: list[str] = field(default_factory=list)
    fail: Callable[[str], GuardedResponse | None] | None = None

    def put(self, key: str, body: bytes) -> str:
        version = f"v{len(self.objects.get(key, [])) + 1}-{uuid.uuid4().hex[:8]}"
        self.objects.setdefault(key, []).append((version, body))
        return version

    def fetch(self, url: str, policy: Any, **kwargs: Any) -> GuardedResponse:
        self.requests.append(url)
        if "authorization" not in {k.lower() for k in kwargs.get("headers", {})}:
            return GuardedResponse(status=403, headers={}, body=b"", final_url=url)
        if self.fail is not None:
            injected = self.fail(url)
            if injected is not None:
                return injected
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        bucket, _, key = parts.path.lstrip("/").partition("/")
        if bucket != BUCKET:
            return GuardedResponse(status=404, headers={}, body=b"", final_url=url)
        if not key and query.get("list-type") == "2":
            return self._list(url, query)
        for version, body in self.objects.get(key, []):
            if version == query.get("versionId"):
                return GuardedResponse(
                    status=200, headers={"ETag": '"x"'}, body=body, final_url=url
                )
        return GuardedResponse(status=404, headers={}, body=b"", final_url=url)

    def _list(self, url: str, query: dict[str, str]) -> GuardedResponse:
        keys = sorted(k for k in self.objects if k.startswith(query.get("prefix", "")))
        start = int(query.get("continuation-token", "0"))
        page = keys[start : start + self.page_size]
        more = start + self.page_size < len(keys)
        body = f'<?xml version="1.0"?><ListBucketResult xmlns="{S3_NS}">'
        body += "".join(f"<Contents><Key>{saxutils.escape(k)}</Key></Contents>" for k in page)
        body += f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>"
        if more:
            body += f"<NextContinuationToken>{start + self.page_size}</NextContinuationToken>"
        body += "</ListBucketResult>"
        return GuardedResponse(status=200, headers={}, body=body.encode(), final_url=url)


CONNECTION = PrivateS3ConnectionConfig(
    endpoint=S3Endpoint(scheme="http", host="seaweedfs.internal", port=8333),
    credentials=S3Credentials(access_key="worker", secret_key="worker-secret"),
    expected_bucket=BUCKET,
    tenant_key_prefix=TENANT,
)


def manifest_for(store: Store, files: dict[str, bytes], **entry_over: Any) -> bytes:
    entries = []
    for name, body in sorted(files.items()):
        entry: dict[str, Any] = {
            "path": name,
            "version_id": store.put(PREFIX + name, body),
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        entry.update(entry_over.get(name, {}))
        entries.append(entry)
    return json.dumps({"schema": bal.MANIFEST_SCHEMA, "files": entries}).encode("utf-8")


@dataclass
class Case:
    store: Store
    artifact: TrainableArtifact
    config: tcs.ResolvedTrainingConfig


def make_artifact(manifest_version: str, digest_hex: str | None, **over: Any) -> TrainableArtifact:
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "id": uuid.UUID("a1111111-1111-4111-8111-111111111111"),
        "organization_id": uuid.uuid4(),
        "display_name": "tiny-llama",
        "source_type": "private_s3",
        "source_uri": f"s3://{BUCKET}/{PREFIX}{bal.MANIFEST_NAME}",
        "model_family": "llama",
        "base_model_revision": manifest_version,
        "weights_digest": None if digest_hex is None else f"sha256:{digest_hex}",
        "license_id": "apache-2.0",
        "license_accepted_by": "user-1",
        "license_accepted_at": now,
        "trust_remote_code_allowed": False,
        "required_python_packages_json": [],
        "minimum_gpu_profile_json": {},
        "status": "ready",
    }
    fields.update(over)
    return TrainableArtifact(**fields)


def make_config(artifact_id: str, digest_hex: str, revision: str) -> tcs.ResolvedTrainingConfig:
    man = "b2222222-2222-4222-8222-222222222222"
    body: dict[str, Any] = {
        "schema_version": "training_config.v1",
        "mode": "advanced",
        "auto_rule_version": None,
        "method": "lora",
        "base": {
            "trainable_artifact_id": artifact_id,
            "model_catalog_entry_id": None,
            "catalog_version": None,
        },
        "tokenizer": {"source": "base_artifact", "revision": revision},
        "template": {"source": "base_artifact", "loss_policy": "assistant_only", "packing": False},
        "data": {
            "train": {"split_manifest_id": man, "role": "train"},
            "validation": {"split_manifest_id": man, "role": "validation"},
            "frozen_manifest_sha256": "a" * 64,
        },
        "precision": {"compute_dtype": "bf16", "quantization": "none"},
        "hyperparameters": {
            "learning_rate": "0.0002",
            "epochs": 3,
            "warmup_ratio": "0.03",
            "scheduler": "cosine",
            "optimizer": "adamw_torch",
            "weight_decay": "0.01",
            "max_grad_norm": "1",
            "max_seq_length": 2048,
            "micro_batch_size": 2,
            "gradient_accumulation_steps": 8,
            "gradient_checkpointing": True,
            "checkpoint_interval_steps": 100,
            "max_checkpoints": 3,
        },
        "adapter": {
            "lora_rank": 16,
            "lora_alpha": 32,
            "lora_dropout": "0.05",
            "target_modules": ["q_proj", "v_proj"],
        },
        "budget": {"max_cost": "10.5", "currency": "USD", "max_wall_clock_seconds": 3600},
    }
    facts = tcs.TrainingConfigFacts(
        artifact=tcs.ArtifactFacts(
            artifact_id=artifact_id,
            status="ready",
            capability="trainable_open_weight",
            weights_digest=digest_hex,
            base_revision=revision,
            license_accepted=True,
        ),
        split=tcs.SplitFacts(
            manifest_id=man,
            manifest_version=2,
            status="locked",
            frozen_sha256="a" * 64,
            hash_verified=True,
            quality_report_current=True,
            grants_current=True,
            train_count=100,
            validation_count=10,
            train_role_sha256="b" * 64,
            validation_role_sha256="c" * 64,
        ),
        security_profile="standard",
        catalog=None,
        budget=tcs.BudgetFacts(
            policy_id="44444444-4444-4444-8444-444444444444",
            row_version=3,
            currency="USD",
            scopes=("training",),
            total=Decimal("100"),
            reserved=Decimal("0"),
        ),
    )
    return tcs.resolve_training_config(tcs.parse_training_config_request(body), facts)


def build_case(
    files: dict[str, bytes] | None = None,
    *,
    extra_objects: dict[str, bytes] | None = None,
    entry_over: dict[str, dict[str, Any]] | None = None,
    artifact_over: dict[str, Any] | None = None,
    manifest_bytes: bytes | None = None,
) -> Case:
    store = Store()
    manifest = manifest_for(store, base_files() if files is None else files, **(entry_over or {}))
    if manifest_bytes is not None:
        manifest = manifest_bytes
    version = store.put(PREFIX + bal.MANIFEST_NAME, manifest)
    for key, body in (extra_objects or {}).items():
        store.put(key, body)
    digest = hashlib.sha256(manifest).hexdigest()
    artifact = make_artifact(version, digest, **(artifact_over or {}))
    config = make_config(str(artifact.id), digest, version)
    return Case(store=store, artifact=artifact, config=config)


@dataclass
class Outcome:
    prepared: Any = None
    error: bal.BaseArtifactLoadError | None = None
    mount_calls: list[dict[str, Any]] = field(default_factory=list)
    job_dir_exists_after: bool = False


def run(case: Case, *, root: Path | None = None, config: Any = None) -> Outcome:
    outcome = Outcome()
    workspace = root or Path(tempfile.mkdtemp(prefix="ftf0706-"))
    cfg = case.config if config is None else config

    def mount_datasets(data_dir: Path) -> str:
        base = data_dir.parent / "base"
        outcome.mount_calls.append(
            {
                "base_tokenizer_present": (base / "tokenizer.json").is_file(),
                "base_weights_present": (base / "model.safetensors").is_file(),
            }
        )
        data_dir.mkdir(mode=0o700)
        return "mounted"

    try:
        outcome.prepared = prepare_training_job(
            cfg,
            case.artifact,
            connection=CONNECTION,
            workspace_root=workspace,
            mount_datasets=mount_datasets,
            fetch=case.store.fetch,
        )
    except bal.BaseArtifactLoadError as exc:
        outcome.error = exc
    outcome.job_dir_exists_after = (workspace / cfg.config_sha256).exists()
    return outcome


def expect_refused(label: str, outcome: Outcome, code: str, state: str = "failed") -> None:
    err = outcome.error
    check(f"{label}: refused with {code}", err is not None and err.code == code)
    check(f"{label}: state {state}", err is not None and err.state == state)
    check(
        f"{label}: code is a declared error code", err is not None and err.code in bal.ERROR_CODES
    )
    check(f"{label}: datasets never mounted", outcome.mount_calls == [])
    check(f"{label}: job directory removed", outcome.job_dir_exists_after is False)
    if err is not None:
        text = str(err)
        check(
            f"{label}: error text carries no bucket, prefix or path",
            BUCKET not in text and TENANT not in text and ".json" not in text,
        )
        check(f"{label}: error has no exception chain", err.__cause__ is None)


# -- suites ------------------------------------------------------------------------------------


def acceptance_suite() -> None:
    case = build_case()
    out = run(case)
    check("acceptance: loads without error", out.error is None and out.prepared is not None)
    if out.prepared is None:
        return
    loaded = out.prepared.base
    check(
        "acceptance: exact approved revision and digest",
        loaded.base_revision == case.artifact.base_model_revision
        and loaded.weights_digest == case.artifact.weights_digest,
    )
    check(
        "acceptance: tokenizer really loaded from the verified tokenizer.json",
        loaded.tokenizer.encode("hello world").ids == [3, 4],
    )
    check(
        "acceptance: every written file equals the approved digest",
        all(
            hashlib.sha256((loaded.directory / f.path).read_bytes()).hexdigest() == f.sha256
            for f in loaded.files
        )
        and sorted(p.name for p in loaded.directory.iterdir()) == sorted(base_files()),
    )
    check(
        "acceptance: base directory is private (0700)",
        (loaded.directory.stat().st_mode & 0o777) == 0o700,
    )
    check(
        "acceptance: safetensors header parsed from the verified bytes",
        [name for name, _ in loaded.weights] == ["model.safetensors"]
        and loaded.weights[0][1].tensors[0][1].shape == (8, 4),
    )
    check(
        "acceptance: model config and tokenizer config parsed",
        loaded.model_config.vocab_size == 8
        and dict(loaded.tokenizer_config.special_tokens)["eos_token"] == "</s>",
    )
    check(
        "order: datasets mounted exactly once, after the verified base files exist",
        out.mount_calls == [{"base_tokenizer_present": True, "base_weights_present": True}]
        and out.prepared.datasets == "mounted",
    )
    manifest_requests = [u for u in case.store.requests if bal.MANIFEST_NAME in u]
    check(
        "acceptance: the manifest was fetched at its exact approved versionId",
        manifest_requests != []
        and all(f"versionId={case.artifact.base_model_revision}" in u for u in manifest_requests),
    )
    # The pinned version is what loads: a newer current version of a file changes nothing.
    newer = build_case()
    newer.store.put(PREFIX + "tokenizer.json", tokenizer_json({"[UNK]": 0, "x": 1}))
    out2 = run(newer)
    check(
        "acceptance: a newer unapproved version of a file is not loaded",
        out2.error is None and out2.prepared.base.tokenizer.encode("hello").ids == [3],
    )
    # Pagination of the listing is followed (page size 2, five objects).
    check(
        "listing: continuation pages were requested",
        any("continuation-token" in u for u in case.store.requests),
    )


def record_suite() -> None:
    expect_refused(
        "record not ready",
        run(build_case(artifact_over={"status": "registered"})),
        "artifact_not_approved",
    )
    expect_refused(
        "license not accepted",
        run(build_case(artifact_over={"license_accepted_at": None, "license_accepted_by": None})),
        "license_not_accepted",
    )
    expect_refused(
        "record digest unknown",
        run(build_case(artifact_over={"weights_digest": None})),
        "digest_unknown",
    )
    expect_refused(
        "remote code allowed on the record",
        run(build_case(artifact_over={"trust_remote_code_allowed": True})),
        "remote_code_not_allowed",
    )
    expect_refused(
        "unsupported source type",
        run(
            build_case(
                artifact_over={"source_type": "local_filesystem", "source_uri": "models/tiny"}
            )
        ),
        "unsupported_source_type",
    )
    expect_refused(
        "bucket outside the connection",
        run(
            build_case(
                artifact_over={"source_uri": f"s3://other-bucket/{PREFIX}{bal.MANIFEST_NAME}"}
            )
        ),
        "artifact_location_denied",
    )
    expect_refused(
        "other tenant prefix",
        run(
            build_case(
                artifact_over={"source_uri": f"s3://{BUCKET}/org-8/models/x/{bal.MANIFEST_NAME}"}
            )
        ),
        "artifact_location_denied",
    )
    expect_refused(
        "source_uri is not a manifest",
        run(build_case(artifact_over={"source_uri": f"s3://{BUCKET}/{PREFIX}model.safetensors"})),
        "artifact_location_denied",
    )

    case = build_case()
    other_digest = make_config(str(case.artifact.id), "f" * 64, case.artifact.base_model_revision)
    expect_refused("job digest differs", run(case, config=other_digest), "digest_mismatch")
    other_rev = make_config(
        str(case.artifact.id), case.artifact.weights_digest[7:], "another-revision"
    )
    expect_refused("job revision differs", run(case, config=other_rev), "revision_not_approved")
    other_art = make_config(
        "c3333333-3333-4333-8333-333333333333",
        case.artifact.weights_digest[7:],
        case.artifact.base_model_revision,
    )
    expect_refused("job names another artifact", run(case, config=other_art), "artifact_mismatch")
    tampered = tcs.ResolvedTrainingConfig(doc=case.config.doc, config_sha256="0" * 64)
    expect_refused("config hash tampered", run(case, config=tampered), "config_integrity_failed")
    edited_doc = copy.deepcopy(tcs._thaw(case.config.doc))
    edited_doc["server"]["weights_digest"] = "e" * 64
    edited = tcs.ResolvedTrainingConfig(
        doc=tcs._freeze(edited_doc), config_sha256=case.config.config_sha256
    )
    expect_refused("config doc edited", run(case, config=edited), "config_integrity_failed")

    # One field edited and the hash recomputed: each job <-> record comparison stands on its own.
    for label, path, value, code in (
        (
            "doc tokenizer revision only",
            ("tokenizer", "revision"),
            "rev-x",
            "revision_not_approved",
        ),
        (
            "doc server base revision only",
            ("server", "base_revision"),
            "rev-x",
            "revision_not_approved",
        ),
        ("doc server digest only", ("server", "weights_digest"), "e" * 64, "digest_mismatch"),
        (
            "doc artifact id only",
            ("base", "trainable_artifact_id"),
            "c3333333-3333-4333-8333-333333333333",
            "artifact_mismatch",
        ),
    ):
        doc = copy.deepcopy(tcs._thaw(case.config.doc))
        doc[path[0]][path[1]] = value
        frozen = tcs._freeze(doc)
        rehashed = tcs.ResolvedTrainingConfig(
            doc=frozen, config_sha256=tcs.compute_config_sha256(frozen)
        )
        expect_refused(label, run(case, config=rehashed), code)

    # FTF-0306 re-verification: the record's digest no longer matches the pinned manifest.
    drift = build_case()
    drift.artifact.weights_digest = "sha256:" + "d" * 64
    drift_cfg = make_config(str(drift.artifact.id), "d" * 64, drift.artifact.base_model_revision)
    expect_refused(
        "manifest differs from record digest", run(drift, config=drift_cfg), "digest_mismatch"
    )

    expect_refused("manifest version absent", _missing_manifest_version(), "artifact_file_missing")


def _missing_manifest_version() -> Outcome:
    case = build_case()
    case.artifact.base_model_revision = "v9-missing"
    cfg = make_config(str(case.artifact.id), case.artifact.weights_digest[7:], "v9-missing")
    return run(case, config=cfg)


def remote_code_suite() -> None:
    files = base_files()
    files["config.json"] = config_json(auto_map={"AutoModel": "modeling_x.XModel"})
    expect_refused("config.json auto_map", run(build_case(files)), "remote_code_not_allowed")
    files = base_files()
    files["config.json"] = config_json(trust_remote_code=True)
    expect_refused(
        "config.json trust_remote_code", run(build_case(files)), "remote_code_not_allowed"
    )
    files = base_files()
    files["tokenizer_config.json"] = tokenizer_config_json(
        auto_map={"AutoTokenizer": ["tok.T", None]}
    )
    expect_refused("tokenizer_config auto_map", run(build_case(files)), "remote_code_not_allowed")
    files = base_files()
    files["modeling_x.py"] = b"import os\nos.system('id')\n"
    expect_refused("custom code file", run(build_case(files)), "artifact_file_unexpected")
    files = base_files()
    files["pytorch_model.bin"] = b"\x80\x04pickle"
    expect_refused("pickle weights", run(build_case(files)), "unsafe_weight_format")
    files = base_files()
    del files["model.safetensors"]
    files["model.pt"] = b"\x80\x04pickle"
    expect_refused("pickle-only weights", run(build_case(files)), "unsafe_weight_format")


def consistency_suite() -> None:
    files = base_files()
    del files["tokenizer.json"]
    files["tokenizer.model"] = b"sentencepiece"
    expect_refused("tokenizer.json absent", run(build_case(files)), "artifact_file_missing")
    files = base_files()
    del files["config.json"]
    expect_refused("config.json absent", run(build_case(files)), "artifact_file_missing")
    files = base_files()
    del files["model.safetensors"]
    expect_refused("no weights", run(build_case(files)), "artifact_file_missing")

    expect_refused(
        "extra object under the artifact prefix",
        run(build_case(extra_objects={PREFIX + "notes.txt": b"x"})),
        "artifact_file_unexpected",
    )
    expect_refused(
        "manifest pins a version that does not exist",
        run(build_case(entry_over={"tokenizer.json": {"version_id": "v9-gone"}})),
        "artifact_file_missing",
    )
    body = base_files()["config.json"]
    altered = body.replace(b'"llama"', b'"LLAMA"')
    expect_refused(
        "file bytes differ from the manifest digest (same size)",
        run(
            build_case(entry_over={"config.json": {"sha256": hashlib.sha256(altered).hexdigest()}})
        ),
        "artifact_file_digest_mismatch",
    )
    expect_refused(
        "file size differs from the manifest",
        run(build_case(entry_over={"model.safetensors": {"size_bytes": 7}})),
        "artifact_file_size_mismatch",
    )

    files = base_files()
    files["tokenizer.json"] = tokenizer_json({**VOCAB, **{f"t{i}": 5 + i for i in range(10)}})
    expect_refused(
        "tokenizer larger than model vocab", run(build_case(files)), "tokenizer_model_inconsistent"
    )
    files = base_files()
    files["tokenizer_config.json"] = tokenizer_config_json(pad_token="<pad>")
    expect_refused(
        "special token missing from vocab", run(build_case(files)), "tokenizer_model_inconsistent"
    )
    files = base_files()
    files["tokenizer.json"] = b'{"not": "a tokenizer"}'
    expect_refused("unloadable tokenizer", run(build_case(files)), "tokenizer_invalid")

    files = base_files()
    files["model.safetensors"] = safetensors_bytes()[:-4]
    expect_refused("truncated safetensors", run(build_case(files)), "weights_header_invalid")
    files = base_files()
    files["config.json"] = config_json(vocab_size=0)
    expect_refused("invalid config.json", run(build_case(files)), "config_invalid")
    files = base_files()
    files["config.json"] = config_json(quantization_config={"load_in_4bit": True})
    expect_refused("unverified quantization", run(build_case(files)), "quantization_unverified")

    shards = {
        "config.json": config_json(),
        "tokenizer.json": tokenizer_json(),
        "tokenizer_config.json": tokenizer_config_json(),
        "model-00001-of-00002.safetensors": safetensors_bytes("a.weight"),
        "model-00002-of-00002.safetensors": safetensors_bytes("b.weight"),
    }
    good_index = {
        "weight_map": {
            "a.weight": "model-00001-of-00002.safetensors",
            "b.weight": "model-00002-of-00002.safetensors",
        }
    }
    files = dict(shards)
    files["model.safetensors.index.json"] = json.dumps(good_index).encode()
    out = run(build_case(files))
    check("sharded artifact with a consistent index loads", out.error is None)
    bad_index = {"weight_map": {"a.weight": "model-00002-of-00002.safetensors"}}
    files = dict(shards)
    files["model.safetensors.index.json"] = json.dumps(bad_index).encode()
    expect_refused(
        "index disagrees with shards", run(build_case(files)), "weights_index_inconsistent"
    )

    for label, manifest in (
        ("manifest not JSON", b"not json"),
        ("manifest duplicate keys", b'{"schema": "x", "schema": "y", "files": []}'),
        ("manifest wrong schema", json.dumps({"schema": "v0", "files": []}).encode()),
        (
            "manifest empty file list",
            json.dumps({"schema": bal.MANIFEST_SCHEMA, "files": []}).encode(),
        ),
        (
            "manifest bad entry",
            json.dumps(
                {"schema": bal.MANIFEST_SCHEMA, "files": [{"path": "config.json"}]}
            ).encode(),
        ),
    ):
        expect_refused(label, run(build_case(manifest_bytes=manifest)), "manifest_invalid")


def fail_closed_suite() -> None:
    def unreachable(url: str) -> GuardedResponse | None:
        raise OutboundGuardError("connection refused")

    case = build_case()
    case.store.fail = unreachable
    expect_refused("object store unreachable", run(case), "object_store_unavailable", "blocked")

    case = build_case()
    case.store.fail = lambda url: GuardedResponse(status=503, headers={}, body=b"", final_url=url)
    expect_refused("object store 503", run(case), "object_store_unavailable", "blocked")

    case = build_case()
    case.store.fail = lambda url: (
        GuardedResponse(status=500, headers={}, body=b"", final_url=url)
        if "list-type" in url
        else None
    )
    expect_refused("listing 500", run(case), "object_store_unavailable", "blocked")

    case = build_case()
    case.store.fail = lambda url: (
        GuardedResponse(status=200, headers={}, body=b"<oops", final_url=url)
        if "list-type" in url
        else None
    )
    expect_refused("listing unreadable", run(case), "object_store_unavailable", "blocked")

    case = build_case()
    case.store.fail = lambda url: (
        GuardedResponse(status=403, headers={}, body=b"", final_url=url)
        if "model.safetensors" in url
        else None
    )
    expect_refused("file fetch refused", run(case), "object_store_unavailable", "blocked")


def workspace_suite() -> None:
    case = build_case()
    root = Path(tempfile.mkdtemp(prefix="ftf0706-"))
    (root / case.config.config_sha256).mkdir()
    out = run(case, root=root)
    check(
        "workspace: an existing job directory is refused, not merged",
        out.error is not None
        and out.error.code == "workspace_unavailable"
        and out.mount_calls == [],
    )

    case = build_case()
    root = Path(tempfile.mkdtemp(prefix="ftf0706-"))

    def failing_mount(data_dir: Path) -> None:
        raise RuntimeError("mount failed")

    raised = False
    try:
        prepare_training_job(
            case.config,
            case.artifact,
            connection=CONNECTION,
            workspace_root=root,
            mount_datasets=failing_mount,
            fetch=case.store.fetch,
        )
    except RuntimeError:
        raised = True
    check(
        "workspace: a mount failure propagates and removes the job directory",
        raised and not (root / case.config.config_sha256).exists(),
    )


def main() -> None:
    logging.disable(logging.CRITICAL)
    acceptance_suite()
    record_suite()
    remote_code_suite()
    consistency_suite()
    fail_closed_suite()
    workspace_suite()
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"{len(FAILURES)} FTF-0706 check(s) failed")
        sys.exit(1)
    print("FTF-0706 base artifact loading: all checks passed")


if __name__ == "__main__":
    main()
