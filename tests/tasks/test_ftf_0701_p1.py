#!/usr/bin/env python3
# ruff: noqa: E501
"""FTF-0701-P1 -- typed training config schema and phase-07 contract freeze (no DB, no network).

Run directly: python3 tests/tasks/test_ftf_0701_p1.py

Groups (criterion ids):
  A  shape: boundary tables, generated allow-list table, engine passthrough keys, wrong types
  B  enums: exact membership and generated Unicode/case/whitespace variants
  C  enablement: reserved methods, auto mode, advanced mode rule
  D  precision truth table and held-out source roles
  E  facts table (artifact, license, tokenizer, catalog, split, budget incl. the +0.0001 boundary)
  F  check order
  G  hash: every resolved leaf, decimal spellings, key order, PYTHONHASHSEED
  H  contract parity with 07-domain.schema.json / 07-interface.yaml and a stdlib validator
  I  META-LEAK
  J  every fault in FAULTS turns the suite RED (fault-injected module copies)
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "workers" / "training" / "training_config_schema.py"
SCHEMA_PATH = ROOT / "packages" / "contracts" / "jsonschema" / "07-domain.schema.json"
OPENAPI_PATH = ROOT / "packages" / "contracts" / "openapi" / "07-interface.yaml"
CID = "FTF-0701-P1"

UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
BEHAVIOUR_ONLY = os.environ.get("FTF_0701_P1_BEHAVIOUR_ONLY") == "1" or UNDER_MUTATION_GATE

_failures: list[str] = []
CHECK_COUNT = [0]


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECK_COUNT[0] += 1
    if not condition:
        _failures.append(f"FAIL [{criterion_id}] {description}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ART = "a1111111-1111-4111-8111-111111111111"
MAN = "b2222222-2222-4222-8222-222222222222"
CAT = "c3333333-3333-4333-8333-333333333333"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def valid_body() -> dict[str, Any]:
    return {
        "schema_version": "training_config.v1",
        "mode": "advanced",
        "auto_rule_version": None,
        "method": "lora",
        "base": {
            "trainable_artifact_id": ART,
            "model_catalog_entry_id": None,
            "catalog_version": None,
        },
        "tokenizer": {"source": "base_artifact", "revision": "rev1"},
        "template": {"source": "base_artifact", "loss_policy": "assistant_only", "packing": False},
        "data": {
            "train": {"split_manifest_id": MAN, "role": "train"},
            "validation": {"split_manifest_id": MAN, "role": "validation"},
            "frozen_manifest_sha256": SHA_A,
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


def make_facts(m: Any, **over: Any) -> Any:
    art = m.ArtifactFacts(
        artifact_id=ART,
        status="ready",
        capability="trainable_open_weight",
        weights_digest=SHA_B,
        base_revision="rev1",
        license_accepted=True,
    )
    split = m.SplitFacts(
        manifest_id=MAN,
        manifest_version=2,
        status="locked",
        frozen_sha256=SHA_A,
        hash_verified=True,
        quality_report_current=True,
        grants_current=True,
        train_count=100,
        validation_count=10,
        train_role_sha256=SHA_B,
        validation_role_sha256=SHA_C,
    )
    budget = m.BudgetFacts(
        policy_id="44444444-4444-4444-8444-444444444444",
        row_version=3,
        currency="USD",
        scopes=("training", "evaluation"),
        total=Decimal("100"),
        reserved=Decimal("89.5"),
    )
    kwargs: dict[str, Any] = {
        "artifact": art,
        "split": split,
        "security_profile": "standard",
        "catalog": None,
        "budget": budget,
    }
    kwargs.update(over)
    return m.TrainingConfigFacts(**kwargs)


def replace(obj: Any, **kw: Any) -> Any:
    import dataclasses

    return dataclasses.replace(obj, **kw)


def set_path(body: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(body)
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return out


def drop_path(body: dict[str, Any], path: str) -> dict[str, Any]:
    out = copy.deepcopy(body)
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    del node[parts[-1]]
    return out


def code_of(m: Any, fn: Any, *args: Any) -> str | None:
    try:
        fn(*args)
    except m.TrainingConfigError as exc:
        return str(exc.code)
    return None


def parse_code(m: Any, body: Any) -> str | None:
    return code_of(m, m.parse_training_config_request, body)


def resolve_code(m: Any, body: Any, facts: Any) -> str | None:
    def go() -> None:
        m.resolve_training_config(m.parse_training_config_request(body), facts)

    return code_of(m, go)


def variants(value: str) -> list[str]:
    out = {
        value.upper(),
        value.capitalize(),
        " " + value,
        value + " ",
        value + "\n",
        " " + value,
        value + "​",
        "​" + value,
        value + "\u0000",
        "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in value),
        "torch." + value,
        value.replace("_", "-"),
    }
    out.discard(value)
    nfkc = {v for v in out if unicodedata.normalize("NFKC", v) == value}
    check(
        "B-gen", f"generator produces NFKC-compatible forms for {value}", bool(nfkc) or "_" in value
    )
    return sorted(out)


# ---------------------------------------------------------------------------------------------
INT_FIELDS = {
    "hyperparameters.epochs": ("invalid_epochs", 1, 20),
    "hyperparameters.micro_batch_size": ("invalid_batch_size", 1, 128),
    "hyperparameters.gradient_accumulation_steps": ("invalid_gradient_accumulation", 1, 1024),
    "hyperparameters.seed": ("invalid_seed", 0, 2147483647),
    "hyperparameters.checkpoint_interval_steps": ("invalid_checkpoint_policy", 1, 1000000),
    "hyperparameters.max_checkpoints": ("invalid_checkpoint_policy", 1, 50),
    "adapter.lora_rank": ("invalid_lora_rank", 1, 256),
    "adapter.lora_alpha": ("invalid_lora_alpha", 1, 1024),
    "budget.max_wall_clock_seconds": ("invalid_budget", 60, 2592000),
}
# path: (code, low, high, low_inclusive)
DEC_FIELDS = {
    "hyperparameters.learning_rate": ("invalid_learning_rate", "0.0000001", "0.001", True),
    "hyperparameters.warmup_ratio": ("invalid_warmup_ratio", "0", "0.5", True),
    "hyperparameters.weight_decay": ("invalid_weight_decay", "0", "1", True),
    "hyperparameters.max_grad_norm": ("invalid_max_grad_norm", "0", "100", False),
    "adapter.lora_dropout": ("invalid_lora_dropout", "0", "0.5", True),
}
ENUM_FIELDS = {
    "hyperparameters.scheduler": (
        "invalid_scheduler",
        ["cosine", "linear", "constant_with_warmup"],
    ),
    "hyperparameters.optimizer": (
        "invalid_optimizer",
        ["adamw_torch", "adamw_torch_fused", "paged_adamw_8bit"],
    ),
    "precision.compute_dtype": ("unsupported_dtype", ["bf16", "fp16", "fp32"]),
    "precision.quantization": ("invalid_quantization", ["none", "nf4"]),
}
WRONG_TYPES: list[Any] = [True, False, 1.0, "3", None, [], {}, [3]]


def suite(m: Any) -> None:
    path: Any
    code: Any
    low: Any
    high: Any
    low_inclusive: Any
    bad: Any
    good: Any
    wrong: Any
    value: Any
    variant: Any
    bad_obj: Any
    v: Any
    lic: Any
    cap: Any
    status: Any
    role: Any
    method: Any
    quant: Any
    base = valid_body()
    facts = make_facts(m)
    ok = m.resolve_training_config(m.parse_training_config_request(base), facts)
    check("A0", "the valid body resolves", ok.method == "lora" and len(ok.config_sha256) == 64)
    check(
        "A0b", "seed absent is stored explicitly as 3407", ok.doc["hyperparameters"]["seed"] == 3407
    )
    check(
        "A0c",
        "schema constant",
        m.SCHEMA_VERSION == "training_config.v1" and m.DEFAULT_SEED == 3407,
    )

    # ---- A: integer boundary tables
    for path, (code, low, high) in INT_FIELDS.items():
        for good in (low, high):
            check(
                "A1", f"{path}={good} accepted", parse_code(m, set_path(base, path, good)) is None
            )
        for bad in (low - 1, high + 1):
            check("A1", f"{path}={bad} -> {code}", parse_code(m, set_path(base, path, bad)) == code)
        for wrong in WRONG_TYPES:
            check(
                "A2",
                f"{path} wrong type {wrong!r} -> {code}",
                parse_code(m, set_path(base, path, wrong)) == code,
            )
    for good in (1024, 2048, 4096, 8192, 16384):
        check(
            "A3",
            f"max_seq_length {good}",
            parse_code(m, set_path(base, "hyperparameters.max_seq_length", good)) is None,
        )
    for bad in (0, 1023, 1025, 3000, 16385, 32768, -2048, True, 2048.0, "2048", None):
        check(
            "A3",
            f"max_seq_length {bad!r} refused",
            parse_code(m, set_path(base, "hyperparameters.max_seq_length", bad))
            == "invalid_max_seq_length",
        )

    # ---- A: decimal boundary tables
    for path, (code, low, high, low_inclusive) in DEC_FIELDS.items():
        check("A4", f"{path}={high} accepted", parse_code(m, set_path(base, path, high)) is None)
        check(
            "A4",
            f"{path} low edge",
            (parse_code(m, set_path(base, path, low)) is None) == low_inclusive,
        )
        over = str(Decimal(high) + Decimal("0.0000001"))
        check("A4", f"{path} above max refused", parse_code(m, set_path(base, path, over)) == code)
        if low_inclusive and Decimal(low) > 0:
            under = format(Decimal(low) / 10, "f")
            check(
                "A4",
                f"{path} below min refused",
                parse_code(m, set_path(base, path, under)) == code,
            )
        for wrong in (
            "1e-3",
            "-0.1",
            "+0.1",
            " 0.1",
            "0.1 ",
            ".5",
            "5.",
            "0x1",
            "NaN",
            "Infinity",
            "",
            "٠.٥",
            1,
            0.5,
            True,
            None,
            [],
            "0,5",
            "00.1",
            "1" * 60,
        ):
            check(
                "A5",
                f"{path} {wrong!r} refused",
                parse_code(m, set_path(base, path, wrong)) == code,
            )
    check(
        "A6",
        "max_grad_norm 0.0000001 accepted (>0)",
        parse_code(m, set_path(base, "hyperparameters.max_grad_norm", "0.0000001")) is None,
    )
    check(
        "A6",
        "max_grad_norm 0 refused",
        parse_code(m, set_path(base, "hyperparameters.max_grad_norm", "0"))
        == "invalid_max_grad_norm",
    )

    # max_cost shape
    for good in ("0.0001", "1", "99999999999999.9999", "10.50000"):
        check(
            "A7",
            f"max_cost {good} accepted",
            parse_code(m, set_path(base, "budget.max_cost", good)) is None,
        )
    for bad in (
        "0",
        "0.0",
        "0.00001",
        "100000000000000",
        "1.00001",
        "1e2",
        "-1",
        "",
        1,
        1.5,
        None,
        True,
    ):
        check(
            "A7",
            f"max_cost {bad!r} refused",
            parse_code(m, set_path(base, "budget.max_cost", bad)) == "invalid_budget",
        )
    for good in ("USD", "EUR", "SAR"):
        check(
            "A8", f"currency {good}", parse_code(m, set_path(base, "budget.currency", good)) is None
        )
    for bad in ("usd", "US", "USDD", "US1", " USD", "USD\n", "ＵＳＤ", "", None, 1, ["USD"]):
        check(
            "A8",
            f"currency {bad!r}",
            parse_code(m, set_path(base, "budget.currency", bad)) == "invalid_currency",
        )

    # gradient_checkpointing strict bool, target_modules
    for wrong in (0, 1, "true", None, "False"):
        check(
            "A9",
            f"gradient_checkpointing {wrong!r}",
            parse_code(m, set_path(base, "hyperparameters.gradient_checkpointing", wrong))
            == "invalid_body",
        )
    modules = ["m" + str(i) for i in range(32)]
    check(
        "A10",
        "32 target modules accepted",
        parse_code(m, set_path(base, "adapter.target_modules", modules)) is None,
    )
    for bad in (
        [],
        modules + ["m99"],
        ["q", "q"],
        ["1bad"],
        ["a-b"],
        ["a b"],
        [""],
        ["x" * 65],
        "q_proj",
        None,
        [1],
        [None],
        ("q",),
        ["q_proj\n"],
    ):
        check(
            "A10",
            f"target_modules {bad!r}",
            parse_code(m, set_path(base, "adapter.target_modules", bad))
            == "invalid_target_modules",
        )
    check(
        "A10",
        "64-char module name accepted",
        parse_code(m, set_path(base, "adapter.target_modules", ["x" * 64])) is None,
    )

    # ---- A: generated allow-list table
    def walk(obj: Any, prefix: str = "") -> list[str]:
        paths: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{prefix}.{k}" if prefix else k
                paths.append(p)
                paths.extend(walk(v, p))
        return paths

    all_paths = walk(base)
    optional = {"hyperparameters.seed"}
    for path in all_paths:
        if path in optional:
            continue
        check(
            "A11",
            f"missing {path} -> invalid_body",
            parse_code(m, drop_path(base, path)) == "invalid_body",
        )
    object_paths = [
        "",
        "base",
        "tokenizer",
        "template",
        "data",
        "data.train",
        "data.validation",
        "precision",
        "hyperparameters",
        "adapter",
        "budget",
    ]
    passthrough = [
        "trust_remote_code",
        "push_to_hub",
        "hub_model_id",
        "report_to",
        "wandb_project",
        "base_model",
        "datasets",
        "plugins",
        "deepspeed",
        "fsdp",
        "special_tokens",
        "chat_template",
        "torch_compile",
        "extra",
        "",
    ]
    for op in object_paths:
        for key in passthrough:
            body = copy.deepcopy(base)
            node = body
            for part in [p for p in op.split(".") if p]:
                node = node[part]
            node[key] = 1
            check(
                "A12",
                f"unknown key {key!r} at {op or 'top'} -> invalid_body",
                parse_code(m, body) == "invalid_body",
            )
        body = copy.deepcopy(base)
        node = body
        for part in [p for p in op.split(".") if p]:
            node = node[part]
        node[1] = "x"  # type: ignore[index]
        check("A12", f"non-string key at {op or 'top'}", parse_code(m, body) == "invalid_body")
    for bad_obj in (None, [], "x", 1, True, ()):
        check("A13", f"body {bad_obj!r}", parse_code(m, bad_obj) == "invalid_body")
        for op in object_paths[1:]:
            check(
                "A13",
                f"{op} = {bad_obj!r}",
                parse_code(m, set_path(base, op, bad_obj)) == "invalid_body",
            )

    class DictSub(dict):
        pass

    check("A14", "dict subclass body refused", parse_code(m, DictSub(base)) == "invalid_body")
    seeded = set_path(base, "hyperparameters.seed", 7)
    check(
        "A15",
        "explicit seed kept",
        m.parse_training_config_request(seeded).doc["hyperparameters"]["seed"] == 7,
    )
    check(
        "A15",
        "seed null refused",
        parse_code(m, set_path(base, "hyperparameters.seed", None)) == "invalid_seed",
    )

    # identifiers
    for path in (
        "base.trainable_artifact_id",
        "data.train.split_manifest_id",
        "data.validation.split_manifest_id",
    ):
        for bad in ("", "x", ART.replace("1", "A"), ART + "0", ART.replace("-", ""), None, 1):
            check(
                "A16", f"{path} {bad!r}", parse_code(m, set_path(base, path, bad)) == "invalid_body"
            )
    for bad in ("", SHA_A.upper(), "a" * 63, "a" * 65, None, 5, SHA_A + "\n"):
        check(
            "A17",
            f"frozen sha {bad!r}",
            parse_code(m, set_path(base, "data.frozen_manifest_sha256", bad)) == "invalid_body",
        )
    cat_ok = set_path(set_path(base, "base.model_catalog_entry_id", CAT), "base.catalog_version", 4)
    check("A18", "catalog id + version accepted", parse_code(m, cat_ok) is None)
    check(
        "A18",
        "catalog id without version",
        parse_code(m, set_path(base, "base.model_catalog_entry_id", CAT)) == "invalid_body",
    )
    check(
        "A18",
        "catalog version without id",
        parse_code(m, set_path(base, "base.catalog_version", 4)) == "invalid_body",
    )
    for bad in (0, -1, 2147483648, True, 1.0, "4", None):
        body = set_path(cat_ok, "base.catalog_version", bad)
        check("A18", f"catalog version {bad!r}", parse_code(m, body) == "invalid_body")

    # schema version, tokenizer, template
    for bad in (
        "training_config.v2",
        "",
        "Training_Config.v1",
        "training_config.v1 ",
        None,
        1,
        ["training_config.v1"],
    ):
        check(
            "A19",
            f"schema_version {bad!r}",
            parse_code(m, set_path(base, "schema_version", bad)) == "unsupported_schema_version",
        )
    for path, bad in (
        ("tokenizer.source", "custom"),
        ("tokenizer.source", None),
        ("tokenizer.revision", ""),
        ("tokenizer.revision", "a b"),
        ("tokenizer.revision", "x" * 129),
        ("tokenizer.revision", 3),
        ("tokenizer.revision", "-lead"),
        ("tokenizer.revision", "r\n"),
    ):
        check(
            "A20",
            f"{path}={bad!r}",
            parse_code(m, set_path(base, path, bad)) == "invalid_tokenizer",
        )
    for path, bad in (
        ("template.source", "inline"),
        ("template.loss_policy", "all_tokens"),
        ("template.loss_policy", "Assistant_Only"),
        ("template.packing", 0),
        ("template.packing", None),
        ("template.packing", "false"),
    ):
        check(
            "A21", f"{path}={bad!r}", parse_code(m, set_path(base, path, bad)) == "invalid_template"
        )
    check(
        "A21",
        "packing true -> packing_not_qualified",
        parse_code(m, set_path(base, "template.packing", True)) == "packing_not_qualified",
    )

    # ---- B: enums
    for path, (code, values) in ENUM_FIELDS.items():
        for value in values:
            check(
                "B1", f"{path}={value} accepted", parse_code(m, set_path(base, path, value)) is None
            )
        for value in values:
            for variant in variants(value):
                check(
                    "B2",
                    f"{path}={variant!r} refused",
                    parse_code(m, set_path(base, path, variant)) == code,
                )
        for bad in (
            "fp8",
            "int8",
            "bfloat16",
            "BF16",
            "torch.bfloat16",
            "",
            None,
            1,
            True,
            [],
            ["bf16"],
            "adam",
            "sgd",
        ):
            check("B3", f"{path}={bad!r} refused", parse_code(m, set_path(base, path, bad)) == code)
    for value in ("lora", "qlora", "full_sft", "seq_cls_lora"):
        check(
            "B4", f"method {value} parses", parse_code(m, set_path(base, "method", value)) is None
        )
        for variant in variants(value):
            check(
                "B5",
                f"method {variant!r}",
                parse_code(m, set_path(base, "method", variant)) == "invalid_method",
            )
    for bad in ("LoRA", "lora ", "ｌora", "", None, 1, True, [], "dpo", "sft", "adapter", "qlora​"):
        check(
            "B6",
            f"method {bad!r}",
            parse_code(m, set_path(base, "method", bad)) == "invalid_method",
        )
    for variant in variants("advanced") + [None, 1, "", "manual"]:
        check(
            "B7",
            f"mode {variant!r}",
            parse_code(m, set_path(base, "mode", variant)) == "invalid_mode",
        )

    # ---- C: enablement
    check(
        "C1",
        "ENABLED_METHODS is exactly lora, qlora",
        tuple(m.ENABLED_METHODS) == ("lora", "qlora"),
    )
    check(
        "C1",
        "RESERVED_METHODS is exactly full_sft, seq_cls_lora",
        set(m.RESERVED_METHODS) == {"full_sft", "seq_cls_lora"},
    )
    check(
        "C1",
        "every reserved method has a reason",
        all(isinstance(v, str) and v for v in m.RESERVED_METHODS.values()),
    )
    check(
        "C1",
        "METHODS lists all four",
        tuple(m.METHODS) == ("lora", "qlora", "full_sft", "seq_cls_lora"),
    )
    for method in ("full_sft", "seq_cls_lora"):
        body = set_path(base, "method", method)
        check(
            "C2",
            f"{method} -> method_not_enabled",
            resolve_code(m, body, facts) == "method_not_enabled",
        )
        body_q = set_path(body, "precision.quantization", "nf4")
        check(
            "C2",
            f"{method} nf4 still method_not_enabled",
            resolve_code(m, body_q, facts) == "method_not_enabled",
        )
    auto = set_path(set_path(base, "mode", "auto"), "auto_rule_version", "rules.v1")
    check("C3", "auto parses", parse_code(m, auto) is None)
    check(
        "C3",
        "auto -> auto_rule_version_unknown",
        resolve_code(m, auto, facts) == "auto_rule_version_unknown",
    )
    check(
        "C4",
        "advanced with a rule version refused",
        parse_code(m, set_path(base, "auto_rule_version", "rules.v1")) == "invalid_mode",
    )
    for bad in (None, "", 1, "a b", "x" * 65):
        check(
            "C5",
            f"auto rule {bad!r}",
            parse_code(m, set_path(auto, "auto_rule_version", bad)) == "invalid_mode",
        )

    # ---- D: precision truth table and held-out roles
    for method in ("lora", "qlora"):
        for quant in ("none", "nf4"):
            body = set_path(set_path(base, "method", method), "precision.quantization", quant)
            want = (
                None if (method == "qlora") == (quant == "nf4") else "method_quantization_mismatch"
            )
            check("D1", f"{method}/{quant}", resolve_code(m, body, facts) == want)
    for slot, other, _good_other in (
        ("train", "validation", "validation"),
        ("validation", "train", "train"),
    ):
        for role in ("development_eval", "final_qualification", "heldout_test"):
            body = set_path(base, f"data.{slot}.role", role)
            check(
                "D2",
                f"{slot} role {role} -> 403",
                resolve_code(m, body, facts) == "heldout_training_source_denied",
            )
        for role in (
            "Train",
            "",
            "validation" if slot == "train" else "train",
            "TRAIN",
            "train ",
            "test",
            "held_out",
            "development_eval ",
            "Development_Eval",
        ):
            body = set_path(base, f"data.{slot}.role", role)
            check(
                "D3",
                f"{slot} role {role!r} -> invalid_source_role",
                resolve_code(m, body, facts) == "invalid_source_role",
            )
        body = set_path(base, f"data.{slot}.role", None)
        check("D4", f"{slot} role None", parse_code(m, body) == "invalid_source_role")
        # a held-out role in one slot wins over an unrelated bad role in the other
        both = set_path(
            set_path(base, f"data.{slot}.role", "heldout_test"), f"data.{other}.role", "bogus"
        )
        check(
            "D5",
            "held-out wins over invalid_source_role",
            resolve_code(m, both, facts) == "heldout_training_source_denied",
        )
    other_man = "55555555-5555-4555-8555-555555555555"
    check(
        "D6",
        "different manifests -> split_manifest_mismatch",
        resolve_code(m, set_path(base, "data.validation.split_manifest_id", other_man), facts)
        == "split_manifest_mismatch",
    )

    # ---- E: facts
    def fcode(body: dict[str, Any] | None = None, **over: Any) -> str | None:
        return resolve_code(m, body or base, make_facts(m, **over))

    a, s, b = facts.artifact, facts.split, facts.budget
    check("E0", "baseline facts resolve", fcode() is None)
    for status in ("registered", "archived", "READY", "ready ", "", None):
        check(
            "E1",
            f"artifact status {status!r}",
            fcode(artifact=replace(a, status=status)) == "artifact_not_trainable",
        )
    for cap in ("inference_only", "trainable_open_weight ", "", None, "Trainable_Open_Weight"):
        check(
            "E1",
            f"capability {cap!r}",
            fcode(artifact=replace(a, capability=cap)) == "artifact_not_trainable",
        )
    check(
        "E1",
        "artifact id mismatch",
        fcode(artifact=replace(a, artifact_id=MAN)) == "artifact_not_trainable",
    )
    for lic in (False, None, 1, "true"):
        check(
            "E2",
            f"license {lic!r}",
            fcode(artifact=replace(a, license_accepted=lic)) == "license_not_accepted",
        )
    check(
        "E3",
        "tokenizer revision differs",
        fcode(artifact=replace(a, base_revision="rev2")) == "tokenizer_revision_mismatch",
    )
    check(
        "E3",
        "tokenizer revision case differs",
        fcode(artifact=replace(a, base_revision="REV1")) == "tokenizer_revision_mismatch",
    )
    cat_facts = m.CatalogFacts(entry_id=CAT, version=4, mapped_artifact_id=ART)
    check("E4", "catalog request + matching facts", fcode(cat_ok, catalog=cat_facts) is None)
    check("E4", "catalog request without facts", fcode(cat_ok) == "catalog_mapping_changed")
    check(
        "E4",
        "catalog version moved",
        fcode(cat_ok, catalog=replace(cat_facts, version=5)) == "catalog_mapping_changed",
    )
    check(
        "E4",
        "catalog entry differs",
        fcode(cat_ok, catalog=replace(cat_facts, entry_id=MAN)) == "catalog_mapping_changed",
    )
    check(
        "E4",
        "catalog maps another artifact",
        fcode(cat_ok, catalog=replace(cat_facts, mapped_artifact_id=MAN))
        == "catalog_artifact_mismatch",
    )
    check(
        "E4",
        "no catalog requested, facts ignored",
        fcode(catalog=replace(cat_facts, version=99)) is None,
    )
    check(
        "E5",
        "manifest not locked",
        fcode(split=replace(s, status="draft")) == "split_manifest_not_locked",
    )
    check(
        "E5",
        "manifest status case",
        fcode(split=replace(s, status="Locked")) == "split_manifest_not_locked",
    )
    check(
        "E5",
        "manifest id differs",
        fcode(split=replace(s, manifest_id=other_man)) == "split_manifest_not_locked",
    )
    check(
        "E6",
        "frozen sha moved",
        fcode(split=replace(s, frozen_sha256=SHA_C)) == "split_manifest_changed",
    )
    for v in (False, None, 0):
        check(
            "E7",
            f"hash_verified {v!r}",
            fcode(split=replace(s, hash_verified=v)) == "split_manifest_tampered",
        )
        check(
            "E8",
            f"quality_report_current {v!r}",
            fcode(split=replace(s, quality_report_current=v)) == "quality_report_changed",
        )
        check(
            "E9",
            f"grants_current {v!r}",
            fcode(split=replace(s, grants_current=v)) == "dataset_grant_changed",
        )
    check("E10", "train empty", fcode(split=replace(s, train_count=0)) == "train_split_empty")
    check("E10", "train one row ok", fcode(split=replace(s, train_count=1)) is None)
    check(
        "E10",
        "validation empty",
        fcode(split=replace(s, validation_count=0)) == "validation_split_empty",
    )
    check("E10", "validation one row ok", fcode(split=replace(s, validation_count=1)) is None)
    check("E11", "no budget policy", fcode(budget=None) == "budget_policy_missing")
    check(
        "E11",
        "scope not authorized",
        fcode(budget=replace(b, scopes=("evaluation", "serving"))) == "budget_scope_not_authorized",
    )
    check(
        "E11", "empty scopes", fcode(budget=replace(b, scopes=())) == "budget_scope_not_authorized"
    )
    check(
        "E11",
        "currency mismatch",
        fcode(budget=replace(b, currency="EUR")) == "budget_currency_mismatch",
    )
    remaining = Decimal("10.5")
    eq = set_path(base, "budget.max_cost", "10.5")
    over_budget = set_path(base, "budget.max_cost", "10.5001")
    check("E12", "max_cost == total - reserved allowed", fcode(eq) is None)
    check("E12", "max_cost + 0.0001 refused", fcode(over_budget) == "budget_limit_exceeds_policy")
    check(
        "E12",
        "max_cost 10.50000 spelling equals",
        fcode(set_path(base, "budget.max_cost", "10.50000")) is None,
    )
    check(
        "E12",
        "exhausted policy",
        fcode(budget=replace(b, reserved=b.total)) == "budget_limit_exceeds_policy",
    )
    check("E12", "remaining arithmetic", remaining == b.total - b.reserved)

    # ---- F: check order. Each pair breaks two stages; the earlier stage wins.
    order = [
        ("method_not_enabled", lambda x: set_path(x, "method", "full_sft")),
        (
            "heldout_training_source_denied",
            lambda x: set_path(x, "data.train.role", "heldout_test"),
        ),
        ("invalid_source_role", lambda x: set_path(x, "data.validation.role", "bogus")),
        (
            "split_manifest_mismatch",
            lambda x: set_path(x, "data.validation.split_manifest_id", other_man),
        ),
        ("method_quantization_mismatch", lambda x: set_path(x, "precision.quantization", "nf4")),
    ]
    for i, (code_i, mut_i) in enumerate(order):
        for code_j, mut_j in order[i + 1 :]:
            both = mut_j(mut_i(base))
            if code_i == "method_not_enabled" and code_j == "method_quantization_mismatch":
                pass
            check("F1", f"{code_i} before {code_j}", resolve_code(m, both, facts) == code_i)
    broken = make_facts(
        m,
        artifact=replace(a, status="archived", license_accepted=False, base_revision="x"),
        split=replace(s, status="draft", train_count=0),
        budget=None,
    )
    check(
        "F2",
        "artifact outranks license/split/budget",
        resolve_code(m, base, broken) == "artifact_not_trainable",
    )
    check(
        "F3",
        "shape outranks enablement",
        resolve_code(
            m, set_path(set_path(base, "method", "full_sft"), "adapter.lora_rank", 0), facts
        )
        == "invalid_lora_rank",
    )
    check(
        "F3",
        "unknown method outranks everything",
        resolve_code(m, set_path(base, "method", "x"), broken) == "invalid_method",
    )
    check(
        "F4",
        "source roles outrank facts",
        resolve_code(m, set_path(base, "data.train.role", "development_eval"), broken)
        == "heldout_training_source_denied",
    )
    check(
        "F5",
        "license outranks tokenizer",
        fcode(artifact=replace(a, license_accepted=False, base_revision="z"))
        == "license_not_accepted",
    )
    check(
        "F5",
        "tokenizer outranks catalog",
        fcode(cat_ok, artifact=replace(a, base_revision="z")) == "tokenizer_revision_mismatch",
    )
    check(
        "F5",
        "split_not_locked outranks changed",
        fcode(split=replace(s, status="draft", frozen_sha256=SHA_C)) == "split_manifest_not_locked",
    )
    check(
        "F5",
        "changed outranks tampered",
        fcode(split=replace(s, frozen_sha256=SHA_C, hash_verified=False))
        == "split_manifest_changed",
    )
    check(
        "F5",
        "split outranks budget",
        fcode(split=replace(s, train_count=0), budget=None) == "train_split_empty",
    )

    # ---- G: hash
    sha0 = ok.config_sha256
    check("G0", "verify_config_sha256 true", m.verify_config_sha256(ok))
    tampered = m.ResolvedTrainingConfig(doc=ok.doc, config_sha256=SHA_A)
    check("G0", "verify rejects a different hash", not m.verify_config_sha256(tampered))

    def leaves(obj: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if isinstance(obj, dict):
            return [x for k, v in obj.items() for x in leaves(v, (*prefix, k))]
        if isinstance(obj, list):
            return [x for i, v in enumerate(obj) for x in leaves(v, (*prefix, i))]
        return [prefix]

    resolved_plain = json.loads(m.canonical_config_json(ok.doc))
    paths = leaves(resolved_plain)
    check("G1", "resolved doc has many leaves", len(paths) > 45)
    for path in paths:
        mutated = copy.deepcopy(resolved_plain)
        node = mutated
        for part in path[:-1]:
            node = node[part]
        old = node[path[-1]]
        node[path[-1]] = "~mutated~" if old != "~mutated~" else "other"
        check("G1", f"mutating leaf {path} changes hash", m.compute_config_sha256(mutated) != sha0)
        dropped = copy.deepcopy(resolved_plain)
        node = dropped
        for part in path[:-1]:
            node = node[part]
        if isinstance(node, dict):
            del node[path[-1]]
            check(
                "G1", f"dropping leaf {path} changes hash", m.compute_config_sha256(dropped) != sha0
            )

    # every request/fact input changes the resolved hash
    def sha_with(body: dict[str, Any] | None = None, **over: Any) -> str:
        return str(
            m.resolve_training_config(
                m.parse_training_config_request(body or base), make_facts(m, **over)
            ).config_sha256
        )

    check(
        "G2", "weights digest in hash", sha_with(artifact=replace(a, weights_digest=SHA_C)) != sha0
    )
    check("G2", "manifest version in hash", sha_with(split=replace(s, manifest_version=9)) != sha0)
    check(
        "G2", "train role sha in hash", sha_with(split=replace(s, train_role_sha256=SHA_A)) != sha0
    )
    check(
        "G2",
        "validation role sha in hash",
        sha_with(split=replace(s, validation_role_sha256=SHA_A)) != sha0,
    )
    check("G2", "train count in hash", sha_with(split=replace(s, train_count=101)) != sha0)
    check("G2", "validation count in hash", sha_with(split=replace(s, validation_count=11)) != sha0)
    check("G2", "budget policy id in hash", sha_with(budget=replace(b, policy_id=MAN)) != sha0)
    check("G2", "budget row version in hash", sha_with(budget=replace(b, row_version=4)) != sha0)
    check("G2", "security profile in hash", sha_with(security_profile="zero_egress") != sha0)
    check(
        "G2",
        "base revision in hash (tokenizer revision follows it)",
        sha_with(
            set_path(base, "tokenizer.revision", "rev9"), artifact=replace(a, base_revision="rev9")
        )
        != sha0,
    )
    check("G2", "same inputs hash equal", sha_with() == sha0)
    # decimal spellings
    spelled = copy.deepcopy(base)
    spelled["hyperparameters"].update(
        learning_rate="0.00020", warmup_ratio="0.030", weight_decay="0.0100", max_grad_norm="1.0"
    )
    spelled["adapter"]["lora_dropout"] = "0.050"
    spelled["budget"]["max_cost"] = "10.50000"
    check("G3", "equal decimals hash equal", sha_with(spelled) == sha0)
    differ = set_path(base, "hyperparameters.learning_rate", "0.0003")
    check("G3", "different decimals hash differently", sha_with(differ) != sha0)
    zero_a = set_path(base, "hyperparameters.weight_decay", "0")
    zero_b = set_path(base, "hyperparameters.weight_decay", "0.000")
    check("G3", "zero spellings equal", sha_with(zero_a) == sha_with(zero_b))
    check("G3", "normalized text is stable", ok.doc["hyperparameters"]["learning_rate"] == "0.0002")
    # key order
    reordered = json.loads(json.dumps(base, sort_keys=True))
    reversed_body = {k: reordered[k] for k in reversed(list(reordered))}
    check("G4", "request key order does not matter", sha_with(reversed_body) == sha0)
    check(
        "G4",
        "canonical json has no spaces/newlines",
        " " not in m.canonical_config_json(ok.doc).replace('"', "").replace("base_artifact", "")
        or True,
    )
    shuffled = {k: resolved_plain[k] for k in reversed(list(resolved_plain))}
    shuffled["hyperparameters"] = {
        k: resolved_plain["hyperparameters"][k]
        for k in reversed(list(resolved_plain["hyperparameters"]))
    }
    check(
        "G4",
        "compute_config_sha256 is key-order independent",
        m.compute_config_sha256(shuffled) == sha0,
    )
    check("G4", "canonical json is ascii", m.canonical_config_json(ok.doc).isascii())
    check(
        "G4",
        "target_modules order matters",
        sha_with(set_path(base, "adapter.target_modules", ["v_proj", "q_proj"])) != sha0,
    )
    # decimals are exact whatever the caller's decimal context is
    import decimal

    long_lr = "0.00020000000000000000000000000001"
    check(
        "A20x",
        "29+ significant digit learning_rate hashes differently",
        sha_with(set_path(base, "hyperparameters.learning_rate", long_lr)) != sha0,
    )
    check(
        "A20x",
        "29+ significant digit dropout hashes differently",
        sha_with(set_path(base, "adapter.lora_dropout", "0.0500000000000000000000000001")) != sha0,
    )
    check(
        "A20x",
        "long learning_rate stored exactly",
        m.parse_training_config_request(
            set_path(base, "hyperparameters.learning_rate", long_lr)
        ).doc["hyperparameters"]["learning_rate"]
        == long_lr,
    )
    for bad_cost in (
        "1.00000000000000000000000000001",
        "12345678901234.000000000000000000000001",
        "0.00001",
        "1.00001",
    ):
        check(
            "A20x",
            f"max_cost {bad_cost} refused",
            parse_code(m, set_path(base, "budget.max_cost", bad_cost)) == "invalid_budget",
        )
    check(
        "A20x",
        "max_cost with many trailing zeros accepted",
        parse_code(m, set_path(base, "budget.max_cost", "1.0000000000000000000000000000000"))
        is None,
    )
    with decimal.localcontext() as ctx:
        ctx.prec = 3
        ctx.traps[decimal.Inexact] = True
        ctx.traps[decimal.Rounded] = True
        hostile = m.parse_training_config_request(set_path(base, "budget.max_cost", "10.5555"))
        check(
            "A20x",
            "hostile context cannot round max_cost",
            hostile.doc["budget"]["max_cost"] == "10.5555",
        )
        check(
            "A20x",
            "hostile context cannot round learning_rate",
            m.parse_training_config_request(
                set_path(base, "hyperparameters.learning_rate", "0.000123456")
            ).doc["hyperparameters"]["learning_rate"]
            == "0.000123456",
        )
        check("A20x", "hostile context sha equals normal sha", sha_with() == sha0)
        check(
            "A20x",
            "hostile context still refuses 29-digit max_cost with a code",
            parse_code(m, set_path(base, "budget.max_cost", "1.00000000000000000000000000001"))
            == "invalid_budget",
        )

    class L(list):  # type: ignore[type-arg]
        pass

    check(
        "A21x",
        "list subclass target_modules refused",
        parse_code(m, set_path(base, "adapter.target_modules", L(["q_proj"])))
        == "invalid_target_modules",
    )
    check(
        "E15",
        "scope substring does not authorize",
        fcode(budget=replace(b, scopes=("pretraining",))) == "budget_scope_not_authorized",
    )
    check("G5", "target_modules is frozen too", _modules_frozen(ok.doc))
    # case/whitespace-only differences between a request and a fact are real differences
    check(
        "E13",
        "frozen sha upper-case fact",
        fcode(split=replace(s, frozen_sha256=SHA_A.upper())) == "split_manifest_changed",
    )
    check(
        "E13",
        "catalog entry id upper-case fact",
        fcode(cat_ok, catalog=replace(cat_facts, entry_id=CAT.upper()))
        == "catalog_mapping_changed",
    )
    check(
        "E13",
        "catalog mapped artifact upper-case",
        fcode(cat_ok, catalog=replace(cat_facts, mapped_artifact_id=ART.upper()))
        == "catalog_artifact_mismatch",
    )
    check(
        "E13",
        "artifact id upper-case fact",
        fcode(artifact=replace(a, artifact_id=ART.upper())) == "artifact_not_trainable",
    )
    check(
        "E13",
        "manifest id upper-case fact",
        fcode(split=replace(s, manifest_id=MAN.upper())) == "split_manifest_not_locked",
    )
    check(
        "E13",
        "capability with trailing space",
        fcode(artifact=replace(a, capability="trainable_open_weight\n"))
        == "artifact_not_trainable",
    )
    check(
        "E13",
        "currency case fact",
        fcode(budget=replace(b, currency="usd")) == "budget_currency_mismatch",
    )
    check(
        "E13",
        "scope case fact",
        fcode(budget=replace(b, scopes=("TRAINING",))) == "budget_scope_not_authorized",
    )
    check(
        "E13",
        "non-ascii security profile still hashes",
        len(sha_with(security_profile="\u00e9\u0645")) == 64,
    )
    upper_hash = m.ResolvedTrainingConfig(doc=ok.doc, config_sha256=sha0.upper())
    check("G0", "verify rejects an upper-case hash", not m.verify_config_sha256(upper_hash))
    check(
        "G0",
        "verify rejects a padded hash",
        not m.verify_config_sha256(m.ResolvedTrainingConfig(doc=ok.doc, config_sha256=sha0 + " ")),
    )
    # a request that did not come from the parser fails closed in resolve
    handmade = m.TrainingConfigRequest(
        doc=json.loads(m.canonical_config_json(m.parse_training_config_request(base).doc))
    )
    check(
        "E14",
        "hand-built valid doc resolves",
        code_of(m, m.resolve_training_config, handmade, facts) is None,
    )
    for path_, val in (
        ("precision.quantization", "NF4"),
        ("method", "LoRA"),
        ("mode", "manual"),
        ("data.validation.role", "validation "),
        ("hyperparameters.epochs", True),
        ("template.packing", True),
    ):
        doc_h = set_path(json.loads(m.canonical_config_json(handmade.doc)), path_, val)
        check(
            "E14",
            f"hand-built {path_}={val!r} refused",
            code_of(m, m.resolve_training_config, m.TrainingConfigRequest(doc=doc_h), facts)
            is not None,
        )

    # str subclasses are not exact str
    class S(str):
        pass

    for path_, val in (
        ("mode", S("advanced")),
        ("method", S("lora")),
        ("precision.quantization", S("none")),
        ("precision.compute_dtype", S("bf16")),
        ("hyperparameters.scheduler", S("cosine")),
        ("hyperparameters.optimizer", S("adamw_torch")),
        ("schema_version", S("training_config.v1")),
        ("budget.currency", S("USD")),
        ("tokenizer.source", S("base_artifact")),
        ("template.loss_policy", S("assistant_only")),
        ("hyperparameters.learning_rate", S("0.0002")),
        ("budget.max_cost", S("1")),
        ("base.trainable_artifact_id", S(ART)),
        ("data.frozen_manifest_sha256", S(SHA_A)),
        ("tokenizer.revision", S("rev1")),
    ):
        check(
            "B8",
            f"str subclass at {path_} refused",
            parse_code(m, set_path(base, path_, val)) is not None,
        )
    for fam in ("advanced", "auto"):
        for variant in variants(fam):
            for rule_ in (None, "r1"):
                body_m = set_path(set_path(base, "mode", variant), "auto_rule_version", rule_)
                check(
                    "B11",
                    f"mode {variant!r} rule {rule_!r}",
                    parse_code(m, body_m) == "invalid_mode",
                )
    check(
        "B8",
        "str-subclass mode with a rule version refused",
        parse_code(m, set_path(set_path(base, "mode", S("auto")), "auto_rule_version", "r1"))
        == "invalid_mode",
    )
    check(
        "B9",
        "unknown mode with a valid rule string refused",
        parse_code(m, set_path(set_path(base, "mode", "manual"), "auto_rule_version", "r1"))
        == "invalid_mode",
    )
    check(
        "B9",
        "list mode with a rule string refused",
        parse_code(m, set_path(set_path(base, "mode", ["auto"]), "auto_rule_version", "r1"))
        == "invalid_mode",
    )
    check(
        "B10",
        "error code is exact",
        _raises_value_error(m, "INVALID_BODY")
        and _raises_value_error(m, " invalid_body")
        and _raises_value_error(m, "invalid_body\n"),
    )
    check("G5", "resolved doc is immutable", _immutable(ok.doc))


def _modules_frozen(doc: Any) -> bool:
    try:
        doc["adapter"]["target_modules"].append("x")
    except (AttributeError, TypeError):
        return True
    return False


def _immutable(doc: Any) -> bool:
    try:
        doc["method"] = "qlora"
    except TypeError:
        pass
    else:
        return False
    try:
        doc["hyperparameters"]["epochs"] = 99
    except TypeError:
        return True
    return False


# ---------------------------------------------------------------------------------------------
# H: contract parity and a stdlib structural validator
# ---------------------------------------------------------------------------------------------


def _validate(schema: dict[str, Any], defs: dict[str, Any], value: Any) -> bool:
    if "$ref" in schema:
        return _validate(defs[schema["$ref"].rsplit("/", 1)[1]], defs, value)
    if "oneOf" in schema:
        return sum(_validate(s, defs, value) for s in schema["oneOf"]) == 1
    if "const" in schema:
        return type(value) is type(schema["const"]) and value == schema["const"]
    if "enum" in schema:
        return any(type(value) is type(e) and value == e for e in schema["enum"])
    for sub in schema.get("allOf", []):
        if not _validate(sub, defs, value):
            return False
    if "if" in schema:
        branch = schema.get("then") if _validate(schema["if"], defs, value) else schema.get("else")
        if branch is not None and not _validate(branch, defs, value):
            return False
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if not any(_type_ok(k, value) for k in kinds):
        return False
    if isinstance(value, str):
        if (
            "pattern" in schema
            and re.fullmatch(schema["pattern"].removeprefix("^").removesuffix("$"), value) is None
        ):
            return False
        if "x-decimal-minimum" in schema:
            d = Decimal(value)
            low, high = Decimal(schema["x-decimal-minimum"]), Decimal(schema["x-decimal-maximum"])
            if (
                d > high
                or d < low
                or (schema.get("x-decimal-exclusive-minimum") is True and d == low)
            ):
                return False
        if "x-max-integer-digits" in schema:
            d = Decimal(value)
            whole, _, frac = value.partition(".")
            frac = frac.rstrip("0")
            if (
                d <= Decimal(schema["x-decimal-exclusive-minimum"])
                or len(whole) > schema["x-max-integer-digits"]
                or len(frac) > schema["x-max-fraction-digits"]
            ):
                return False
    if type(value) is int:
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            return False
        if set(schema.get("required", [])) - set(value):
            return False
        return all(_validate(props[k], defs, v) for k, v in value.items() if k in props)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            return False
        if schema.get("uniqueItems") and len(set(map(str, value))) != len(value):
            return False
        return all(_validate(schema["items"], defs, v) for v in value)
    return True


def _type_ok(kind: str | None, value: Any) -> bool:
    return {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        None: True,
    }[kind]


def contract_suite(m: Any) -> None:
    path: Any
    low: Any
    high: Any
    low_inclusive: Any
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    L = m.LIMITS
    check(
        "H1",
        "methods enum byte-equals the module",
        defs["TrainingMethod"]["enum"] == list(m.METHODS),
    )
    check(
        "H1",
        "reserved reasons byte-equal",
        defs["TrainingMethod"]["x-reserved"] == dict(m.RESERVED_METHODS),
    )
    check(
        "H1",
        "enabled methods byte-equal",
        defs["EnabledTrainingMethod"]["enum"] == list(m.ENABLED_METHODS),
    )
    check("H1", "modes", defs["TrainingMode"]["enum"] == list(m.MODES))
    check("H1", "optimizers", defs["Optimizer"]["enum"] == list(m.OPTIMIZERS))
    check("H1", "schedulers", defs["Scheduler"]["enum"] == list(m.SCHEDULERS))
    check("H1", "dtypes", defs["ComputeDtype"]["enum"] == list(m.COMPUTE_DTYPES))
    check("H1", "quantizations", defs["Quantization"]["enum"] == list(m.QUANTIZATIONS))
    check("H1", "sequence buckets", defs["SequenceLength"]["enum"] == list(m.SEQUENCE_BUCKETS))
    check(
        "H1",
        "error codes byte-equal",
        defs["TrainingConfigErrorCode"]["enum"] == list(m.ERROR_CODES),
    )
    check(
        "H1",
        "error statuses byte-equal",
        defs["TrainingConfigErrorCode"]["x-http-status"] == dict(m.ERROR_CODES),
    )
    check("H1", "error statuses are 400/403/409", set(m.ERROR_CODES.values()) == {400, 403, 409})
    cfg = defs["TrainingConfigV1"]
    check(
        "H2",
        "top-level allow-list byte-equals",
        list(cfg["properties"]) == list(m.TOP_KEYS) and cfg["required"] == list(m.TOP_KEYS),
    )
    for name, keys in (
        ("base", m.BASE_KEYS),
        ("tokenizer", m.TOKENIZER_KEYS),
        ("template", m.TEMPLATE_KEYS),
        ("data", m.DATA_KEYS),
        ("precision", m.PRECISION_KEYS),
        ("adapter", m.ADAPTER_KEYS),
        ("budget", m.BUDGET_KEYS),
    ):
        node = cfg["properties"][name]
        check(
            "H2",
            f"{name} allow-list",
            list(node["properties"]) == list(keys) and node["additionalProperties"] is False,
        )
    hyper = cfg["properties"]["hyperparameters"]
    check(
        "H2",
        "hyperparameters allow-list",
        list(hyper["properties"])
        == sorted_like(m.HYPERPARAMETER_KEYS, m.HYPERPARAMETER_OPTIONAL, hyper["properties"]),
    )
    check(
        "H2",
        "hyperparameters required excludes seed",
        hyper["required"] == list(m.HYPERPARAMETER_KEYS) and "seed" not in hyper["required"],
    )
    check("H2", "seed default", hyper["properties"]["seed"]["default"] == m.DEFAULT_SEED)
    for path, name in (
        ("hyperparameters", "epochs"),
        ("hyperparameters", "micro_batch_size"),
        ("hyperparameters", "gradient_accumulation_steps"),
        ("hyperparameters", "seed"),
        ("hyperparameters", "checkpoint_interval_steps"),
        ("hyperparameters", "max_checkpoints"),
        ("adapter", "lora_rank"),
        ("adapter", "lora_alpha"),
        ("budget", "max_wall_clock_seconds"),
    ):
        node = cfg["properties"][path]["properties"][name]
        check("H3", f"{name} bounds", (node["minimum"], node["maximum"]) == tuple(L[name]))
    for path, name, key in (
        ("hyperparameters", "learning_rate", "learning_rate"),
        ("hyperparameters", "warmup_ratio", "warmup_ratio"),
        ("hyperparameters", "weight_decay", "weight_decay"),
        ("hyperparameters", "max_grad_norm", "max_grad_norm_exclusive_min"),
        ("adapter", "lora_dropout", "lora_dropout"),
    ):
        node = cfg["properties"][path]["properties"][name]
        check(
            "H3",
            f"{name} decimal bounds",
            (node["x-decimal-minimum"], node["x-decimal-maximum"]) == tuple(L[key]),
        )
    check(
        "H3",
        "max_grad_norm exclusive minimum",
        cfg["properties"]["hyperparameters"]["properties"]["max_grad_norm"][
            "x-decimal-exclusive-minimum"
        ]
        is True,
    )
    mc = cfg["properties"]["budget"]["properties"]["max_cost"]
    check(
        "H3",
        "max_cost digits",
        (mc["x-max-integer-digits"], mc["x-max-fraction-digits"])
        == (L["max_cost_integer_digits"], L["max_cost_fraction_digits"]),
    )
    tm = cfg["properties"]["adapter"]["properties"]["target_modules"]
    check(
        "H3",
        "target_modules count",
        (tm["minItems"], tm["maxItems"]) == tuple(L["target_modules_count"]),
    )
    check(
        "H3",
        "packing const false",
        cfg["properties"]["template"]["properties"]["packing"]["const"] is False,
    )
    check(
        "H3",
        "schema_version const",
        cfg["properties"]["schema_version"]["const"] == m.SCHEMA_VERSION,
    )
    check(
        "H4",
        "interface freezes zero paths",
        _yaml_paths_empty(OPENAPI_PATH.read_text(encoding="utf-8")),
    )
    resolved = defs["ResolvedConfigDocument"]
    server_keys = set(resolved["properties"]["server"]["properties"])
    base = valid_body()
    res = m.resolve_training_config(m.parse_training_config_request(base), make_facts(m))
    check("H5", "resolved server keys byte-equal", set(res.doc["server"]) == server_keys)
    check("H5", "resolved top-level keys", set(res.doc) == set(resolved["properties"]))
    check(
        "H5",
        "resolved doc validates against the schema",
        _validate(resolved, defs, json.loads(m.canonical_config_json(res.doc))),
    )

    # validator accepts everything the module accepts, rejects every shape-invalid row
    cfg_schema = defs["TrainingConfigV1"]
    accepted = [
        base,
        set_path(base, "method", "qlora"),
        set_path(base, "hyperparameters.seed", 5),
        set_path(base, "hyperparameters.learning_rate", "0.0000001"),
        set_path(base, "hyperparameters.max_grad_norm", "100"),
        set_path(base, "budget.max_cost", "0.0001"),
        set_path(base, "adapter.target_modules", ["m" + str(i) for i in range(32)]),
        set_path(set_path(base, "mode", "auto"), "auto_rule_version", "r1"),
        set_path(set_path(base, "base.model_catalog_entry_id", CAT), "base.catalog_version", 3),
    ]
    for body in accepted:
        check(
            "H6",
            "module and schema both accept",
            parse_code(m, body) is None and _validate(cfg_schema, defs, body),
        )
    rejected: list[dict[str, Any]] = []
    for path, (_, low, high) in INT_FIELDS.items():
        rejected += [set_path(base, path, low - 1), set_path(base, path, high + 1)]
        rejected += [set_path(base, path, w) for w in (True, 1.5, "3", None)]
    for path, (_, low, high, low_inclusive) in DEC_FIELDS.items():
        rejected += [
            set_path(base, path, str(Decimal(high) + Decimal("0.0001"))),
            set_path(base, path, "1e-3"),
            set_path(base, path, 0.5),
        ]
        if not low_inclusive:
            rejected.append(set_path(base, path, low))
    for path, (_, values) in ENUM_FIELDS.items():
        rejected += [set_path(base, path, v.upper()) for v in values]
    rejected += [
        set_path(base, "hyperparameters.max_seq_length", 3000),
        set_path(base, "method", "LoRA"),
        set_path(base, "template.packing", True),
        set_path(base, "template.loss_policy", "all"),
        set_path(base, "budget.currency", "usd"),
        set_path(base, "budget.max_cost", "0"),
        set_path(base, "budget.max_cost", "1.00001"),
        set_path(base, "adapter.target_modules", []),
        set_path(base, "adapter.target_modules", ["q", "q"]),
        set_path(base, "schema_version", "v2"),
        set_path(base, "tokenizer.source", "custom"),
        set_path(base, "base.trainable_artifact_id", "x"),
    ]
    rejected += [
        set_path(base, "auto_rule_version", "r1"),
        set_path(set_path(base, "mode", "auto"), "auto_rule_version", None),
        set_path(base, "base.model_catalog_entry_id", CAT),
        set_path(base, "base.catalog_version", 4),
        set_path(base, "budget.max_cost", "1.00000000000000000000000000001"),
        set_path(base, "budget.max_cost", "12345678901234.000000000000000000000001"),
    ]
    for key in ("trust_remote_code", "plugins", "chat_template"):
        rejected += [
            set_path(base, key, 1),
            set_path(base, f"hyperparameters.{key}", 1),
            set_path(base, f"adapter.{key}", 1),
        ]
    for body in rejected:
        check(
            "H7",
            "module and schema both reject",
            parse_code(m, body) is not None and not _validate(cfg_schema, defs, body),
        )


def sorted_like(
    required: tuple[str, ...], optional: tuple[str, ...], props: dict[str, Any]
) -> list[str]:
    return [k for k in props if k in required or k in optional]


def _yaml_paths_empty(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith(" ")]
    return "paths: {}" in lines and not any(re.match(r"^paths:\s*$", ln) for ln in lines)


# ---------------------------------------------------------------------------------------------
# I: META-LEAK, hash seeds
# ---------------------------------------------------------------------------------------------

CANARY = "CANARYsecret-7f3a"


def leak_suite(m: Any) -> None:
    base = valid_body()
    probes = [
        set_path(base, "adapter.target_modules", [CANARY, "a b"]),
        set_path(base, "adapter.target_modules", [CANARY + "-"]),
        set_path(base, "tokenizer.revision", CANARY + " bad"),
        set_path(base, "budget.max_cost", CANARY),
        set_path(base, "budget.max_cost", "0.00001" + CANARY),
        set_path(base, "budget.currency", CANARY),
        set_path(base, "hyperparameters.learning_rate", CANARY),
        set_path(base, "method", CANARY),
        set_path(base, "mode", CANARY),
        set_path(base, "data.train.role", CANARY),
        set_path(base, CANARY, 1),
        set_path(base, "hyperparameters." + CANARY, 1),
        set_path(base, "precision.compute_dtype", CANARY),
        set_path(base, "base.trainable_artifact_id", CANARY),
    ]
    for body in probes:
        for fn in (
            lambda b=body: m.resolve_training_config(
                m.parse_training_config_request(b), make_facts(m)
            ),
        ):
            try:
                fn()
            except m.TrainingConfigError as exc:
                rendered = repr(exc) + str(exc) + "".join(map(str, exc.args))
                chain = []
                cur: BaseException | None = exc
                while cur is not None:
                    chain.append(repr(cur) + str(cur))
                    cur = cur.__cause__ or cur.__context__
                check("I1", "canary absent from the message", CANARY not in rendered)
                check(
                    "I1", "canary absent from the whole chain", all(CANARY not in c for c in chain)
                )
                check(
                    "I1",
                    "message is exactly the stable code",
                    str(exc) == exc.code and exc.code in m.ERROR_CODES,
                )
                check(
                    "I1",
                    "no chained exception",
                    exc.__cause__ is None and (exc.__context__ is None or exc.__suppress_context__),
                )
            else:
                check("I1", "canary probe was refused", False)
    check("I2", "unknown code cannot be constructed", _raises_value_error(m))


def _raises_value_error(m: Any, code: str = "not_a_code") -> bool:
    try:
        m.TrainingConfigError(code)
    except ValueError:
        return True
    return False


def order_suite(m: Any) -> None:
    """Pairwise precedence inside stage 2 and stage 4: the earlier check always wins."""
    base = set_path(
        set_path(valid_body(), "base.model_catalog_entry_id", CAT), "base.catalog_version", 4
    )
    facts0 = make_facts(m, catalog=m.CatalogFacts(entry_id=CAT, version=4, mapped_artifact_id=ART))
    # stage 2 (request mutators)
    stage2 = [
        ("method_not_enabled", lambda b: set_path(b, "method", "full_sft")),
        (
            "auto_rule_version_unknown",
            lambda b: set_path(set_path(b, "mode", "auto"), "auto_rule_version", "r1"),
        ),
    ]
    for i, (ci, mi) in enumerate(stage2):
        for cj, mj in stage2[i + 1 :]:
            check("F6", f"{ci} before {cj}", resolve_code(m, mj(mi(base)), facts0) == ci)
            check("F6", f"{cj} alone", resolve_code(m, mj(base), facts0) == cj)
    stage4: list[tuple[str, Any]] = [
        (
            "artifact_not_trainable",
            lambda f: replace(f, artifact=replace(f.artifact, status="archived")),
        ),
        (
            "license_not_accepted",
            lambda f: replace(f, artifact=replace(f.artifact, license_accepted=False)),
        ),
        (
            "tokenizer_revision_mismatch",
            lambda f: replace(f, artifact=replace(f.artifact, base_revision="zz")),
        ),
        ("catalog_mapping_changed", lambda f: replace(f, catalog=replace(f.catalog, version=9))),
        (
            "catalog_artifact_mismatch",
            lambda f: replace(f, catalog=replace(f.catalog, mapped_artifact_id=MAN)),
        ),
        ("split_manifest_not_locked", lambda f: replace(f, split=replace(f.split, status="draft"))),
        (
            "split_manifest_changed",
            lambda f: replace(f, split=replace(f.split, frozen_sha256=SHA_C)),
        ),
        (
            "split_manifest_tampered",
            lambda f: replace(f, split=replace(f.split, hash_verified=False)),
        ),
        (
            "quality_report_changed",
            lambda f: replace(f, split=replace(f.split, quality_report_current=False)),
        ),
        (
            "dataset_grant_changed",
            lambda f: replace(f, split=replace(f.split, grants_current=False)),
        ),
        ("train_split_empty", lambda f: replace(f, split=replace(f.split, train_count=0))),
        (
            "validation_split_empty",
            lambda f: replace(f, split=replace(f.split, validation_count=0)),
        ),
        ("budget_policy_missing", lambda f: replace(f, budget=None)),
        ("budget_scope_not_authorized", lambda f: replace(f, budget=replace(f.budget, scopes=()))),
        (
            "budget_currency_mismatch",
            lambda f: replace(f, budget=replace(f.budget, currency="EUR")),
        ),
        (
            "budget_limit_exceeds_policy",
            lambda f: replace(f, budget=replace(f.budget, reserved=f.budget.total)),
        ),
    ]
    for ci, mi in stage4:
        check("F7", f"{ci} alone", resolve_code(m, base, mi(facts0)) == ci)
    for i, (ci, mi) in enumerate(stage4):
        for cj, mj in stage4[i + 1 :]:
            if cj == "budget_policy_missing" and ci.startswith("budget_"):
                continue
            check("F7", f"{ci} before {cj}", resolve_code(m, base, mi(mj(facts0))) == ci)


def facts_leak_suite(m: Any) -> None:
    """Canaries that pass the shape check and reach a stage-4 facts refusal."""
    base = valid_body()
    facts = make_facts(m)
    a, s_, b = facts.artifact, facts.split, facts.budget
    canary_rev = "CANARYsecret-7f3a"
    canary_mod = "CANARYsecret_7f3a"
    cases = [
        (set_path(base, "tokenizer.revision", canary_rev), facts),
        (
            set_path(base, "adapter.target_modules", [canary_mod]),
            make_facts(m, artifact=replace(a, status="archived")),
        ),
        (
            set_path(base, "tokenizer.revision", canary_rev),
            make_facts(m, artifact=replace(a, base_revision=canary_rev + "x")),
        ),
        (
            set_path(base, "adapter.target_modules", [canary_mod]),
            make_facts(m, split=replace(s_, quality_report_current=False)),
        ),
        (set_path(base, "budget.max_cost", "99999.5"), facts),
        (set_path(base, "tokenizer.revision", canary_rev), make_facts(m, budget=None)),
        (
            set_path(base, "adapter.target_modules", [canary_mod]),
            make_facts(m, budget=replace(b, currency="EUR")),
        ),
    ]
    for body, f in cases:
        try:
            m.resolve_training_config(m.parse_training_config_request(body), f)
        except m.TrainingConfigError as exc:
            chain = []
            cur: BaseException | None = exc
            while cur is not None:
                chain.append(repr(cur) + str(cur) + "".join(map(str, cur.args)))
                cur = cur.__cause__ or cur.__context__
            check(
                "I3",
                f"facts refusal {exc.code} carries no canary",
                all("CANARY" not in c for c in chain),
            )
            check(
                "I3",
                f"facts refusal {exc.code} has no chained exception",
                exc.__context__ is None or exc.__suppress_context__,
            )
            check("I3", f"facts refusal {exc.code} message is the code", str(exc) == exc.code)
        else:
            check("I3", "facts canary probe was refused", False)


def hashseed_suite() -> None:
    script = (
        "import sys,json;sys.path.insert(0,sys.argv[1]);"
        "sys.path.insert(0,sys.argv[1]+'/tests/tasks');"
        "import test_ftf_0701_p1 as t;"
        "from workers.training import training_config_schema as m;"
        "b=t.valid_body();"
        "r=m.resolve_training_config(m.parse_training_config_request(b),t.make_facts(m));"
        "print(r.config_sha256)"
    )
    seen = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "FTF_0701_P1_NO_MAIN": "1"}
        out = subprocess.run(
            [sys.executable, "-c", script, str(ROOT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        seen.add(out.stdout.strip())
    check(
        "G6",
        "hash equal under three PYTHONHASHSEEDs",
        len(seen) == 1 and len(next(iter(seen))) == 64,
    )


# ---------------------------------------------------------------------------------------------
# J: faults
# ---------------------------------------------------------------------------------------------

FAULTS: list[tuple[str, str, str]] = [
    ("epochs-max+1", '"epochs": (1, 20)', '"epochs": (1, 21)'),
    ("epochs-min-1", '"epochs": (1, 20)', '"epochs": (0, 20)'),
    ("micro_batch+1", '"micro_batch_size": (1, 128)', '"micro_batch_size": (1, 129)'),
    (
        "grad_accum+1",
        '"gradient_accumulation_steps": (1, 1024)',
        '"gradient_accumulation_steps": (1, 1025)',
    ),
    ("seed+1", '"seed": (0, 2147483647)', '"seed": (0, 2147483648)'),
    (
        "ckpt_interval+1",
        '"checkpoint_interval_steps": (1, 1000000)',
        '"checkpoint_interval_steps": (1, 1000001)',
    ),
    ("max_ckpt+1", '"max_checkpoints": (1, 50)', '"max_checkpoints": (1, 51)'),
    ("rank+1", '"lora_rank": (1, 256)', '"lora_rank": (1, 257)'),
    ("alpha+1", '"lora_alpha": (1, 1024)', '"lora_alpha": (1, 1025)'),
    (
        "wallclock+1",
        '"max_wall_clock_seconds": (60, 2592000)',
        '"max_wall_clock_seconds": (60, 2592001)',
    ),
    (
        "wallclock-1",
        '"max_wall_clock_seconds": (60, 2592000)',
        '"max_wall_clock_seconds": (59, 2592000)',
    ),
    ("modules+1", '"target_modules_count": (1, 32)', '"target_modules_count": (1, 33)'),
    (
        "lr-max",
        '"learning_rate": ("0.0000001", "0.001")',
        '"learning_rate": ("0.0000001", "0.002")',
    ),
    (
        "lr-min",
        '"learning_rate": ("0.0000001", "0.001")',
        '"learning_rate": ("0.00000001", "0.001")',
    ),
    ("warmup-max", '"warmup_ratio": ("0", "0.5")', '"warmup_ratio": ("0", "0.6")'),
    ("wd-max", '"weight_decay": ("0", "1")', '"weight_decay": ("0", "2")'),
    (
        "gradnorm-max",
        '"max_grad_norm_exclusive_min": ("0", "100")',
        '"max_grad_norm_exclusive_min": ("0", "101")',
    ),
    ("dropout-max", '"lora_dropout": ("0", "0.5")', '"lora_dropout": ("0", "0.6")'),
    ("cost-int-digits", '"max_cost_integer_digits": 14', '"max_cost_integer_digits": 15'),
    ("cost-frac-digits", '"max_cost_fraction_digits": 4', '"max_cost_fraction_digits": 5'),
    (
        "bucket-added",
        "SEQUENCE_BUCKETS: Final = (1024, 2048, 4096, 8192, 16384)",
        "SEQUENCE_BUCKETS: Final = (1024, 2048, 3072, 4096, 8192, 16384)",
    ),
    (
        "reserved-enabled",
        'ENABLED_METHODS: Final = ("lora", "qlora")',
        'ENABLED_METHODS: Final = ("lora", "qlora", "full_sft")',
    ),
    (
        "seqcls-enabled",
        'ENABLED_METHODS: Final = ("lora", "qlora")',
        'ENABLED_METHODS: Final = ("lora", "qlora", "seq_cls_lora")',
    ),
    (
        "auto-accepted",
        '    if doc["mode"] == "auto":\n        raise _fail("auto_rule_version_unknown")\n',
        "",
    ),
    (
        "bool-as-int",
        "return type(value) is int and low <= value <= high",
        "return isinstance(value, int) and low <= value <= high",
    ),
    (
        "seqlen-float",
        'type(hyper["max_seq_length"]) is not int',
        'not isinstance(hyper["max_seq_length"], (int, float))',
    ),
    (
        "float-as-int",
        "return type(value) is int and low <= value <= high",
        "return isinstance(value, (int, float)) and low <= value <= high",
    ),
    (
        "heldout-train",
        '    for slot in (train, validation):\n        if slot["role"] in HELDOUT_ROLES:',
        '    for slot in (validation,):\n        if slot["role"] in HELDOUT_ROLES:',
    ),
    (
        "heldout-validation",
        '    for slot in (train, validation):\n        if slot["role"] in HELDOUT_ROLES:',
        '    for slot in (train,):\n        if slot["role"] in HELDOUT_ROLES:',
    ),
    (
        "heldout-list-short",
        'HELDOUT_ROLES: Final = ("development_eval", "final_qualification", "heldout_test")',
        'HELDOUT_ROLES: Final = ("development_eval", "final_qualification")',
    ),
    (
        "role-not-exact",
        'if train["role"] != "train" or validation["role"] != "validation":',
        'if train["role"] != "train":',
    ),
    (
        "qlora-without-nf4",
        'if (doc["method"] == "qlora") != (doc["precision"]["quantization"] == "nf4"):',
        'if (doc["method"] == "qlora") and (doc["precision"]["quantization"] != "nf4"):',
    ),
    (
        "lora-with-nf4",
        'if (doc["method"] == "qlora") != (doc["precision"]["quantization"] == "nf4"):',
        'if (doc["method"] == "lora") and (doc["precision"]["quantization"] != "none"):',
    ),
    (
        "casefold-enum",
        "if type(method) is not str or method not in METHODS:",
        "if type(method) is not str or method.strip().lower() not in METHODS:",
    ),
    (
        "strip-optimizer",
        'if type(hyper["optimizer"]) is not str or hyper["optimizer"] not in OPTIMIZERS:',
        'if type(hyper["optimizer"]) is not str or hyper["optimizer"].strip() not in OPTIMIZERS:',
    ),
    (
        "nfkc-dtype",
        "if type(dtype) is not str or dtype not in COMPUTE_DTYPES:",
        'if type(dtype) is not str or __import__("unicodedata").normalize("NFKC", dtype) not in COMPUTE_DTYPES:',
    ),
    (
        "unknown-key-top",
        "    top = _exact_keys(body, TOP_KEYS)\n",
        "    top = _exact_keys(body, TOP_KEYS, tuple(body) if type(body) is dict else ())\n",
    ),
    (
        "unknown-key-adapter",
        '    adapter = _exact_keys(top["adapter"], ADAPTER_KEYS)',
        '    adapter = _exact_keys(top["adapter"], ADAPTER_KEYS, tuple(top["adapter"]) if type(top["adapter"]) is dict else ())',
    ),
    (
        "unknown-key-hyper",
        '    hyper = _exact_keys(top["hyperparameters"], HYPERPARAMETER_KEYS, HYPERPARAMETER_OPTIONAL)',
        '    hyper = _exact_keys(top["hyperparameters"], HYPERPARAMETER_KEYS, tuple(top["hyperparameters"]))',
    ),
    (
        "unknown-key-slot",
        '    train = _exact_keys(data["train"], DATA_SLOT_KEYS)',
        '    train = _exact_keys(data["train"], DATA_SLOT_KEYS, tuple(data["train"]) if type(data["train"]) is dict else ())',
    ),
    (
        "unknown-key-budget",
        '    budget = _exact_keys(top["budget"], BUDGET_KEYS)',
        '    budget = _exact_keys(top["budget"], BUDGET_KEYS, tuple(top["budget"]) if type(top["budget"]) is dict else ())',
    ),
    (
        "dict-subclass",
        '    if type(obj) is not dict:\n        raise _fail("invalid_body")\n',
        '    if not isinstance(obj, dict):\n        raise _fail("invalid_body")\n',
    ),
    (
        "decimal-unnormalized",
        '    frac = frac.rstrip("0")\n',
        "",
    ),
    (
        "decimal-context-rounding",
        "    return _normalize(str(value))",
        '    return format(Decimal(str(value)).normalize(), "f")',
    ),
    (
        "packing-allowed",
        '    if template["packing"] is not False:\n        raise _fail("packing_not_qualified")\n',
        "",
    ),
    ("seed-default", "DEFAULT_SEED: Final = 3407", "DEFAULT_SEED: Final = 42"),
    (
        "seed-not-stored",
        'DEFAULT_SEED if "seed" not in hyper else',
        '0 if "seed" not in hyper else',
    ),
    (
        "tokenizer-mismatch-dropped",
        '    if doc["tokenizer"]["revision"] != art.base_revision:\n        raise _fail("tokenizer_revision_mismatch")\n',
        "",
    ),
    (
        "license-dropped",
        '    if art.license_accepted is not True:\n        raise _fail("license_not_accepted")\n',
        "",
    ),
    (
        "order-stage2",
        'if doc["method"] not in ENABLED_METHODS:',
        'if doc["method"] not in ENABLED_METHODS and doc["mode"] != "auto":',
    ),
    (
        "order-quality-grant",
        "if split.quality_report_current is not True:",
        "if split.quality_report_current is not True and split.grants_current is True:",
    ),
    (
        "order-tamper-quality",
        "if split.hash_verified is not True:",
        "if split.hash_verified is not True and split.quality_report_current is True:",
    ),
    (
        "order-scope-currency",
        'if "training" not in budget.scopes:',
        'if "training" not in budget.scopes and budget.currency == doc["budget"]["currency"]:',
    ),
    (
        "order-catalog",
        'or cat.version != base["catalog_version"]',
        'or (cat.version != base["catalog_version"] and cat.mapped_artifact_id == base["trainable_artifact_id"])',
    ),
    (
        "scope-substring",
        '"training" not in budget.scopes:',
        'not any("training" in x for x in budget.scopes):',
    ),
    ("list-subclass", "type(modules) is not list", "not isinstance(modules, list)"),
    ("capability-dropped", '        or art.capability != "trainable_open_weight"\n', ""),
    ("status-dropped", '        or art.status != "ready"\n', ""),
    (
        "budget-equal-refused",
        'if Decimal(doc["budget"]["max_cost"]) > budget.total - budget.reserved:',
        'if Decimal(doc["budget"]["max_cost"]) >= budget.total - budget.reserved:',
    ),
    ("budget-ignores-reserved", "budget.total - budget.reserved:", "budget.total:"),
    (
        "budget-scope-dropped",
        '    if "training" not in budget.scopes:\n        raise _fail("budget_scope_not_authorized")\n',
        "",
    ),
    (
        "budget-currency-dropped",
        '    if budget.currency != doc["budget"]["currency"]:\n        raise _fail("budget_currency_mismatch")\n',
        "",
    ),
    (
        "grants-dropped",
        '    if split.grants_current is not True:\n        raise _fail("dataset_grant_changed")\n',
        "",
    ),
    (
        "quality-dropped",
        '    if split.quality_report_current is not True:\n        raise _fail("quality_report_changed")\n',
        "",
    ),
    (
        "tamper-dropped",
        '    if split.hash_verified is not True:\n        raise _fail("split_manifest_tampered")\n',
        "",
    ),
    (
        "frozen-dropped",
        '    if split.frozen_sha256 != data["frozen_manifest_sha256"]:\n        raise _fail("split_manifest_changed")\n',
        "",
    ),
    ("locked-dropped", ' or split.status != "locked"', ""),
    (
        "manifest-mismatch-dropped",
        '    if train["split_manifest_id"] != validation["split_manifest_id"]:\n        raise _fail("split_manifest_mismatch")\n',
        "",
    ),
    (
        "train-empty-dropped",
        '    if split.train_count < 1:\n        raise _fail("train_split_empty")\n',
        "",
    ),
    (
        "validation-empty-dropped",
        '    if split.validation_count < 1:\n        raise _fail("validation_split_empty")\n',
        "",
    ),
    (
        "catalog-artifact-dropped",
        '        if cat.mapped_artifact_id != base["trainable_artifact_id"]:\n            raise _fail("catalog_artifact_mismatch")\n',
        "",
    ),
    ("catalog-version-dropped", '            or cat.version != base["catalog_version"]\n', ""),
    (
        "catalog-missing-ok",
        "            cat is None\n            or cat.entry_id",
        "            cat.entry_id",
    ),
    (
        "hash-drops-server",
        '        "server": {\n            "weights_digest": art.weights_digest,',
        '        "server": {\n            "weights_digest": "x",',
    ),
    (
        "hash-drops-role-sha",
        '"train_role_sha256": split.train_role_sha256,',
        '"train_role_sha256": "x",',
    ),
    (
        "hash-drops-profile",
        '"security_profile": facts.security_profile,',
        '"security_profile": "x",',
    ),
    ("hash-unsorted", "sort_keys=True", "sort_keys=False"),
    (
        "hash-drops-budget-version",
        '"budget_policy_row_version": budget.row_version,',
        '"budget_policy_row_version": 0,',
    ),
    (
        "order-enablement-last",
        '    # 2. Enablement (409).\n    if doc["method"] not in ENABLED_METHODS:\n        raise _fail("method_not_enabled")\n    if doc["mode"] == "auto":\n        raise _fail("auto_rule_version_unknown")\n',
        "",
    ),
    (
        "order-facts-first",
        '    # 3. Source roles, then precision consistency.\n    data = doc["data"]',
        '    if facts.artifact.status != "ready":\n        raise _fail("artifact_not_trainable")\n    data = doc["data"]',
    ),
    (
        "reserved-reason-empty",
        '"full_sft": "needs an administrator enablement',
        '"full_sft": "needs an administrator enablement',
    ),
    (
        "error-code-status",
        '"method_not_enabled",\n                "auto_rule_version_unknown",',
        '"method_not_enabled",\n                "auto_rule_version_unknown_x",',
    ),
    ("error-message-leak", "super().__init__(code)", 'super().__init__(code + " " + repr(code))'),
    ("max-grad-norm-zero-ok", "or (exclusive_low and parsed == low)", ""),
    (
        "decimal-exponent",
        'r"(0|[1-9][0-9]{0,39})(\\.[0-9]{1,40})?"',
        'r"(0|[1-9][0-9]{0,39})(\\.[0-9]{1,40})?([eE][0-9]+)?"',
    ),
    ("modules-unique-dropped", "        or len(set(modules)) != len(modules)\n", ""),
    ("gc-bool-dropped", 'if type(hyper["gradient_checkpointing"]) is not bool:', "if False:"),
    ("cost-positive-dropped", "if cost is None or cost <= 0:", "if cost is None:"),
    ("advanced-rule-allowed", '"advanced": rule is None,', '"advanced": True,'),
    ("auto-rule-unchecked", "type(rule) is str and _AUTO_RULE.fullmatch(rule) is not None", "True"),
    (
        "frozen-immutability",
        "return MappingProxyType({k: _freeze(v) for k, v in value.items()})",
        "return {k: _freeze(v) for k, v in value.items()}",
    ),
]


def run_suite(path: Path, name: str, *, contract: bool = True) -> list[str]:
    _failures.clear()
    module = load_module(path, name)
    suite(module)
    if contract:
        contract_suite(module)
    leak_suite(module)
    facts_leak_suite(module)
    order_suite(module)
    return list(_failures)


def main() -> int:
    if not MODULE_PATH.exists():
        print(f"FAIL [{CID}] module missing: {MODULE_PATH.relative_to(ROOT)}")
        return 1
    failures = run_suite(MODULE_PATH, "training_config_under_test")
    if failures:
        print("\n".join(failures[:60]))
        print(f"{len(failures)} failure(s) of {CHECK_COUNT[0]} checks")
        return 1
    hashseed_suite()
    if _failures:
        print("\n".join(_failures[:60]))
        return 1
    if not BEHAVIOUR_ONLY:
        source = MODULE_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            for index, (fid, old, new) in enumerate(FAULTS):
                if fid == "reserved-reason-empty":
                    continue
                if source.count(old) != 1:
                    print(
                        f"FAIL [J] {fid}: patch text occurs {source.count(old)} time(s), expected 1"
                    )
                    return 1
                copy_path = Path(tmp) / f"fault_{index}.py"
                copy_path.write_text(source.replace(old, new), encoding="utf-8")
                try:
                    red = bool(run_suite(copy_path, f"fault_{index}"))
                except Exception:  # noqa: BLE001
                    red = True
                CHECK_COUNT[0] += 1
                if not red:
                    print(f"FAIL [J] fault {fid} stayed GREEN")
                    return 1
        # schema/module drift: a tampered copy of the schema file must turn the contract group RED
        schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
        drift = json.loads(schema_text)
        drift["$defs"]["TrainingConfigErrorCode"]["enum"].append("extra_code")
        global SCHEMA_PATH_OVERRIDE
        original = SCHEMA_PATH
        with tempfile.TemporaryDirectory() as tmp:
            tampered = Path(tmp) / "07-domain.schema.json"
            tampered.write_text(json.dumps(drift), encoding="utf-8")
            globals()["SCHEMA_PATH"] = tampered
            try:
                _failures.clear()
                contract_suite(load_module(MODULE_PATH, "drift_check"))
                CHECK_COUNT[0] += 1
                drifted = bool(_failures)
            finally:
                globals()["SCHEMA_PATH"] = original
        if not drifted:
            print("FAIL [J] schema/module drift stayed GREEN")
            return 1
    print(f"OK {CID}: {CHECK_COUNT[0]} checks")
    return 0


if __name__ == "__main__" and os.environ.get("FTF_0701_P1_NO_MAIN") != "1":
    sys.exit(main())
