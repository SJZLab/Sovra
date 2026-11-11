"""Typed training configuration schema (FTF-0701-P1; FINE_TUNING_FACTORY_RELEASE_1.md Section 6.15,
14.1-14.2, 15.1-15.5, 16 steps 1-9, 68.2-68.4, 77.3).

Pure and stdlib-only: no database, network or filesystem access. A route (FTF-0701-P3) builds a
`TrainingConfigFacts` from rows it read itself and asks `resolve_training_config` to turn the
caller's request into a `ResolvedTrainingConfig` with a stable `config_sha256`. Nothing here trusts
request text for a fact the server can read.

The config is a Factory-owned, engine-neutral typed spec (Section 77.3). It carries no engine
passthrough: every object is an exact allow-list, so an Axolotl key such as `trust_remote_code`,
`plugins` or `chat_template` is `invalid_body`, never forwarded.

Check order (fixed, the first failing check wins, pinned by a test):
  1. shape and typed values (400)           `parse_training_config_request`
  2. method / mode enablement (409)          `resolve_training_config`
  3. data source roles, then precision (403/400)
  4. server facts: artifact, tokenizer, catalog, split, budget (409/403)

Errors carry only a stable code. No request value is ever placed in an exception message or chain
(META-LEAK).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Final

__all__ = [
    "DEFAULT_SEED",
    "ENABLED_METHODS",
    "ERROR_CODES",
    "LIMITS",
    "METHODS",
    "RESERVED_METHODS",
    "SCHEMA_VERSION",
    "SEQUENCE_BUCKETS",
    "ArtifactFacts",
    "BudgetFacts",
    "CatalogFacts",
    "ResolvedTrainingConfig",
    "SplitFacts",
    "TrainingConfigError",
    "TrainingConfigFacts",
    "TrainingConfigRequest",
    "canonical_config_json",
    "compute_config_sha256",
    "parse_training_config_request",
    "resolve_training_config",
    "verify_config_sha256",
]

SCHEMA_VERSION: Final = "training_config.v1"
DEFAULT_SEED: Final = 3407
MODES: Final = ("advanced", "auto")
METHODS: Final = ("lora", "qlora", "full_sft", "seq_cls_lora")
ENABLED_METHODS: Final = ("lora", "qlora")
RESERVED_METHODS: Final = MappingProxyType(
    {
        "full_sft": "needs an administrator enablement and a separate cost-approval gate "
        "(Section 15.1 item 3); no task has built either",
        "seq_cls_lora": "enabled only when FTF-0708A is CERTIFIED (ADR 0004)",
    }
)
SEQUENCE_BUCKETS: Final = (1024, 2048, 4096, 8192, 16384)
OPTIMIZERS: Final = ("adamw_torch", "adamw_torch_fused", "paged_adamw_8bit")
SCHEDULERS: Final = ("cosine", "linear", "constant_with_warmup")
COMPUTE_DTYPES: Final = ("bf16", "fp16", "fp32")
QUANTIZATIONS: Final = ("none", "nf4")
HELDOUT_ROLES: Final = ("development_eval", "final_qualification", "heldout_test")

# (min, max) inclusive; Decimal limits are strings so the contract can mirror them byte for byte.
LIMITS: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "epochs": (1, 20),
        "micro_batch_size": (1, 128),
        "gradient_accumulation_steps": (1, 1024),
        "seed": (0, 2147483647),
        "checkpoint_interval_steps": (1, 1000000),
        "max_checkpoints": (1, 50),
        "lora_rank": (1, 256),
        "lora_alpha": (1, 1024),
        "max_wall_clock_seconds": (60, 2592000),
        "target_modules_count": (1, 32),
        "learning_rate": ("0.0000001", "0.001"),
        "warmup_ratio": ("0", "0.5"),
        "weight_decay": ("0", "1"),
        "max_grad_norm_exclusive_min": ("0", "100"),
        "lora_dropout": ("0", "0.5"),
        "max_cost_integer_digits": 14,
        "max_cost_fraction_digits": 4,
    }
)

ERROR_CODES: Final = MappingProxyType(
    {
        **dict.fromkeys(
            (
                "invalid_body",
                "unsupported_schema_version",
                "invalid_mode",
                "invalid_method",
                "invalid_source_role",
                "split_manifest_mismatch",
                "invalid_tokenizer",
                "invalid_template",
                "packing_not_qualified",
                "invalid_lora_rank",
                "invalid_lora_alpha",
                "invalid_lora_dropout",
                "invalid_target_modules",
                "invalid_learning_rate",
                "invalid_epochs",
                "invalid_warmup_ratio",
                "invalid_max_seq_length",
                "invalid_batch_size",
                "invalid_gradient_accumulation",
                "invalid_optimizer",
                "invalid_scheduler",
                "invalid_weight_decay",
                "invalid_max_grad_norm",
                "invalid_seed",
                "invalid_checkpoint_policy",
                "unsupported_dtype",
                "invalid_quantization",
                "method_quantization_mismatch",
                "invalid_budget",
                "invalid_currency",
            ),
            400,
        ),
        **dict.fromkeys(("heldout_training_source_denied", "dataset_grant_changed"), 403),
        **dict.fromkeys(
            (
                "method_not_enabled",
                "auto_rule_version_unknown",
                "split_manifest_not_locked",
                "split_manifest_changed",
                "split_manifest_tampered",
                "quality_report_changed",
                "train_split_empty",
                "validation_split_empty",
                "artifact_not_trainable",
                "license_not_accepted",
                "tokenizer_revision_mismatch",
                "catalog_mapping_changed",
                "catalog_artifact_mismatch",
                "budget_policy_missing",
                "budget_scope_not_authorized",
                "budget_currency_mismatch",
                "budget_limit_exceeds_policy",
                "idempotency_key_reused",
            ),
            409,
        ),
    }
)


class TrainingConfigError(Exception):
    """A refusal with a stable code; the message is the code and nothing else."""

    def __init__(self, code: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError("unknown training config error code")
        super().__init__(code)
        self.code = code
        self.status = ERROR_CODES[code]


def _fail(code: str) -> TrainingConfigError:
    return TrainingConfigError(code)


# ---------------------------------------------------------------------------------------------
# Exact-type primitives. Nothing here ever echoes a value.
# ---------------------------------------------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DECIMAL = re.compile(r"(0|[1-9][0-9]{0,39})(\.[0-9]{1,40})?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_AUTO_RULE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _int_in(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _parse_decimal(value: object) -> Decimal | None:
    """A JSON-string decimal: plain digits, no sign, no exponent, no whitespace."""
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        return None
    return Decimal(value)


def _normalize(text: str) -> str:
    """Canonical decimal text of an already-validated plain decimal string.

    Textual, never `Decimal.normalize()`: that rounds to the caller's thread-local decimal context
    (28 digits by default) and would let two different values hash equal.
    """
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _exact_keys(
    obj: object, keys: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    if type(obj) is not dict:
        raise _fail("invalid_body")
    present = set(obj)
    if not (set(keys) <= present <= set(keys) | set(optional)):
        raise _fail("invalid_body")
    return obj


TOP_KEYS: Final = (
    "schema_version",
    "mode",
    "auto_rule_version",
    "method",
    "base",
    "tokenizer",
    "template",
    "data",
    "precision",
    "hyperparameters",
    "adapter",
    "budget",
)
BASE_KEYS: Final = ("trainable_artifact_id", "model_catalog_entry_id", "catalog_version")
TOKENIZER_KEYS: Final = ("source", "revision")
TEMPLATE_KEYS: Final = ("source", "loss_policy", "packing")
DATA_KEYS: Final = ("train", "validation", "frozen_manifest_sha256")
DATA_SLOT_KEYS: Final = ("split_manifest_id", "role")
PRECISION_KEYS: Final = ("compute_dtype", "quantization")
HYPERPARAMETER_KEYS: Final = (
    "learning_rate",
    "epochs",
    "warmup_ratio",
    "scheduler",
    "optimizer",
    "weight_decay",
    "max_grad_norm",
    "max_seq_length",
    "micro_batch_size",
    "gradient_accumulation_steps",
    "gradient_checkpointing",
    "checkpoint_interval_steps",
    "max_checkpoints",
)
HYPERPARAMETER_OPTIONAL: Final = ("seed",)
ADAPTER_KEYS: Final = ("lora_rank", "lora_alpha", "lora_dropout", "target_modules")
BUDGET_KEYS: Final = ("max_cost", "currency", "max_wall_clock_seconds")


@dataclass(frozen=True)
class TrainingConfigRequest:
    """A shape-valid request. Source roles and facts are not yet judged."""

    doc: Mapping[str, Any]


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _bounded_int(value: Any, name: str, code: str) -> int:
    low, high = LIMITS[name]
    if not _int_in(value, low, high):
        raise _fail(code)
    return value


def _bounded_decimal(value: object, name: str, code: str, *, exclusive_low: bool = False) -> str:
    parsed = _parse_decimal(value)
    if parsed is None:
        raise _fail(code)
    low_text, high_text = LIMITS[name]
    low, high = Decimal(str(low_text)), Decimal(str(high_text))
    if parsed > high or parsed < low or (exclusive_low and parsed == low):
        raise _fail(code)
    return _normalize(str(value))


def parse_training_config_request(body: object) -> TrainingConfigRequest:
    """Parse a request body; raises `TrainingConfigError` (stable code) on the first problem."""
    # 1. Shape of the whole tree before any value is judged, so a missing or unknown key at any
    #    level is `invalid_body` regardless of what the values are.
    top = _exact_keys(body, TOP_KEYS)
    base = _exact_keys(top["base"], BASE_KEYS)
    tokenizer = _exact_keys(top["tokenizer"], TOKENIZER_KEYS)
    template = _exact_keys(top["template"], TEMPLATE_KEYS)
    data = _exact_keys(top["data"], DATA_KEYS)
    train = _exact_keys(data["train"], DATA_SLOT_KEYS)
    validation = _exact_keys(data["validation"], DATA_SLOT_KEYS)
    precision = _exact_keys(top["precision"], PRECISION_KEYS)
    hyper = _exact_keys(top["hyperparameters"], HYPERPARAMETER_KEYS, HYPERPARAMETER_OPTIONAL)
    adapter = _exact_keys(top["adapter"], ADAPTER_KEYS)
    budget = _exact_keys(top["budget"], BUDGET_KEYS)

    # 2. Identity and envelope.
    if top["schema_version"] != "training_config.v1" or type(top["schema_version"]) is not str:
        raise _fail("unsupported_schema_version")
    mode, rule = top["mode"], top["auto_rule_version"]
    # advanced carries no rule version; auto must name one. One lookup, so the mode is read once.
    rule_ok = {
        "advanced": rule is None,
        "auto": type(rule) is str and _AUTO_RULE.fullmatch(rule) is not None,
    }
    if type(mode) is not str or not rule_ok.get(mode, False):
        raise _fail("invalid_mode")
    method = top["method"]
    if type(method) is not str or method not in METHODS:
        raise _fail("invalid_method")

    # 3. Identifiers (all `invalid_body`: they are not user-tunable values).
    artifact_id = base["trainable_artifact_id"]
    entry_id, catalog_version = base["model_catalog_entry_id"], base["catalog_version"]
    if type(artifact_id) is not str or _UUID.fullmatch(artifact_id) is None:
        raise _fail("invalid_body")
    if (entry_id is None) != (catalog_version is None):
        raise _fail("invalid_body")
    if entry_id is not None and (
        type(entry_id) is not str
        or _UUID.fullmatch(entry_id) is None
        or not _int_in(catalog_version, 1, 2147483647)
    ):
        raise _fail("invalid_body")
    for slot in (train, validation):
        manifest_id = slot["split_manifest_id"]
        if type(manifest_id) is not str or _UUID.fullmatch(manifest_id) is None:
            raise _fail("invalid_body")
        if type(slot["role"]) is not str:
            raise _fail("invalid_source_role")
    frozen = data["frozen_manifest_sha256"]
    if type(frozen) is not str or _SHA256.fullmatch(frozen) is None:
        raise _fail("invalid_body")

    # 4. Tokenizer and template.
    revision = tokenizer["revision"]
    if (
        type(tokenizer["source"]) is not str
        or tokenizer["source"] != "base_artifact"
        or type(revision) is not str
        or _REVISION.fullmatch(revision) is None
    ):
        raise _fail("invalid_tokenizer")
    if (
        type(template["source"]) is not str
        or template["source"] != "base_artifact"
        or type(template["loss_policy"]) is not str
        or template["loss_policy"] != "assistant_only"
        or type(template["packing"]) is not bool
    ):
        raise _fail("invalid_template")
    if template["packing"] is not False:
        raise _fail("packing_not_qualified")

    # 5. Precision.
    dtype, quant = precision["compute_dtype"], precision["quantization"]
    if type(dtype) is not str or dtype not in COMPUTE_DTYPES:
        raise _fail("unsupported_dtype")
    if type(quant) is not str or quant not in QUANTIZATIONS:
        raise _fail("invalid_quantization")

    # 6. Hyperparameters.
    norm_hyper: dict[str, Any] = {
        "learning_rate": _bounded_decimal(
            hyper["learning_rate"], "learning_rate", "invalid_learning_rate"
        ),
        "epochs": _bounded_int(hyper["epochs"], "epochs", "invalid_epochs"),
        "warmup_ratio": _bounded_decimal(
            hyper["warmup_ratio"], "warmup_ratio", "invalid_warmup_ratio"
        ),
    }
    if type(hyper["scheduler"]) is not str or hyper["scheduler"] not in SCHEDULERS:
        raise _fail("invalid_scheduler")
    norm_hyper["scheduler"] = hyper["scheduler"]
    if type(hyper["optimizer"]) is not str or hyper["optimizer"] not in OPTIMIZERS:
        raise _fail("invalid_optimizer")
    norm_hyper["optimizer"] = hyper["optimizer"]
    norm_hyper["weight_decay"] = _bounded_decimal(
        hyper["weight_decay"], "weight_decay", "invalid_weight_decay"
    )
    norm_hyper["max_grad_norm"] = _bounded_decimal(
        hyper["max_grad_norm"],
        "max_grad_norm_exclusive_min",
        "invalid_max_grad_norm",
        exclusive_low=True,
    )
    if type(hyper["max_seq_length"]) is not int or hyper["max_seq_length"] not in SEQUENCE_BUCKETS:
        raise _fail("invalid_max_seq_length")
    norm_hyper["max_seq_length"] = hyper["max_seq_length"]
    norm_hyper["micro_batch_size"] = _bounded_int(
        hyper["micro_batch_size"], "micro_batch_size", "invalid_batch_size"
    )
    norm_hyper["gradient_accumulation_steps"] = _bounded_int(
        hyper["gradient_accumulation_steps"],
        "gradient_accumulation_steps",
        "invalid_gradient_accumulation",
    )
    if type(hyper["gradient_checkpointing"]) is not bool:
        raise _fail("invalid_body")
    norm_hyper["gradient_checkpointing"] = hyper["gradient_checkpointing"]
    norm_hyper["seed"] = (
        DEFAULT_SEED if "seed" not in hyper else _bounded_int(hyper["seed"], "seed", "invalid_seed")
    )
    norm_hyper["checkpoint_interval_steps"] = _bounded_int(
        hyper["checkpoint_interval_steps"], "checkpoint_interval_steps", "invalid_checkpoint_policy"
    )
    norm_hyper["max_checkpoints"] = _bounded_int(
        hyper["max_checkpoints"], "max_checkpoints", "invalid_checkpoint_policy"
    )

    # 7. Adapter.
    modules = adapter["target_modules"]
    mlow, mhigh = LIMITS["target_modules_count"]
    if (
        type(modules) is not list
        or not (mlow <= len(modules) <= mhigh)
        or any(type(m) is not str or _IDENT.fullmatch(m) is None for m in modules)
        or len(set(modules)) != len(modules)
    ):
        raise _fail("invalid_target_modules")
    norm_adapter = {
        "lora_rank": _bounded_int(adapter["lora_rank"], "lora_rank", "invalid_lora_rank"),
        "lora_alpha": _bounded_int(adapter["lora_alpha"], "lora_alpha", "invalid_lora_alpha"),
        "lora_dropout": _bounded_decimal(
            adapter["lora_dropout"], "lora_dropout", "invalid_lora_dropout"
        ),
        "target_modules": list(modules),
    }

    # 8. Budget.
    cost = _parse_decimal(budget["max_cost"])
    if cost is None or cost <= 0:
        raise _fail("invalid_budget")
    cost_text = _normalize(str(budget["max_cost"]))
    whole, _, frac = cost_text.partition(".")
    if (
        len(whole) > LIMITS["max_cost_integer_digits"]
        or len(frac) > LIMITS["max_cost_fraction_digits"]
    ):
        raise _fail("invalid_budget")
    currency = budget["currency"]
    if type(currency) is not str or _CURRENCY.fullmatch(currency) is None:
        raise _fail("invalid_currency")
    norm_budget = {
        "max_cost": cost_text,
        "currency": currency,
        "max_wall_clock_seconds": _bounded_int(
            budget["max_wall_clock_seconds"], "max_wall_clock_seconds", "invalid_budget"
        ),
    }

    doc = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "auto_rule_version": rule,
        "method": method,
        "base": {
            "trainable_artifact_id": artifact_id,
            "model_catalog_entry_id": entry_id,
            "catalog_version": catalog_version,
        },
        "tokenizer": {"source": "base_artifact", "revision": revision},
        "template": {"source": "base_artifact", "loss_policy": "assistant_only", "packing": False},
        "data": {
            "train": {"split_manifest_id": train["split_manifest_id"], "role": train["role"]},
            "validation": {
                "split_manifest_id": validation["split_manifest_id"],
                "role": validation["role"],
            },
            "frozen_manifest_sha256": frozen,
        },
        "precision": {"compute_dtype": dtype, "quantization": quant},
        "hyperparameters": norm_hyper,
        "adapter": norm_adapter,
        "budget": norm_budget,
    }
    return TrainingConfigRequest(doc=_freeze(doc))


# ---------------------------------------------------------------------------------------------
# Server-side facts
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactFacts:
    artifact_id: str
    status: str
    capability: str
    weights_digest: str
    base_revision: str
    license_accepted: bool


@dataclass(frozen=True)
class CatalogFacts:
    entry_id: str
    version: int
    mapped_artifact_id: str


@dataclass(frozen=True)
class SplitFacts:
    manifest_id: str
    manifest_version: int
    status: str
    frozen_sha256: str
    hash_verified: bool
    quality_report_current: bool
    grants_current: bool
    train_count: int
    validation_count: int
    train_role_sha256: str
    validation_role_sha256: str


@dataclass(frozen=True)
class BudgetFacts:
    policy_id: str
    row_version: int
    currency: str
    scopes: tuple[str, ...]
    total: Decimal
    reserved: Decimal


@dataclass(frozen=True)
class TrainingConfigFacts:
    artifact: ArtifactFacts
    split: SplitFacts
    security_profile: str
    catalog: CatalogFacts | None = None
    budget: BudgetFacts | None = None


@dataclass(frozen=True)
class ResolvedTrainingConfig:
    doc: Mapping[str, Any]
    config_sha256: str

    @property
    def method(self) -> str:
        return str(self.doc["method"])


def canonical_config_json(doc: Mapping[str, Any]) -> str:
    """Key-order independent, whitespace-free JSON of a plain-JSON document."""
    return json.dumps(_thaw(doc), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_config_sha256(doc: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_config_json(doc).encode("ascii")).hexdigest()


def verify_config_sha256(resolved: ResolvedTrainingConfig) -> bool:
    return compute_config_sha256(resolved.doc) == resolved.config_sha256


def resolve_training_config(
    request: TrainingConfigRequest, facts: TrainingConfigFacts
) -> ResolvedTrainingConfig:
    # A hand-built request is re-parsed, so a doc that did not come from the parser fails closed.
    doc = parse_training_config_request(_thaw(request.doc)).doc

    # 2. Enablement (409).
    if doc["method"] not in ENABLED_METHODS:
        raise _fail("method_not_enabled")
    if doc["mode"] == "auto":
        raise _fail("auto_rule_version_unknown")

    # 3. Source roles, then precision consistency.
    data = doc["data"]
    train, validation = data["train"], data["validation"]
    for slot in (train, validation):
        if slot["role"] in HELDOUT_ROLES:
            raise _fail("heldout_training_source_denied")
    if train["role"] != "train" or validation["role"] != "validation":
        raise _fail("invalid_source_role")
    if train["split_manifest_id"] != validation["split_manifest_id"]:
        raise _fail("split_manifest_mismatch")
    if (doc["method"] == "qlora") != (doc["precision"]["quantization"] == "nf4"):
        raise _fail("method_quantization_mismatch")

    # 4. Facts.
    art, split = facts.artifact, facts.split
    base = doc["base"]
    if (
        art.artifact_id != base["trainable_artifact_id"]
        or art.status != "ready"
        or art.capability != "trainable_open_weight"
    ):
        raise _fail("artifact_not_trainable")
    if art.license_accepted is not True:
        raise _fail("license_not_accepted")
    if doc["tokenizer"]["revision"] != art.base_revision:
        raise _fail("tokenizer_revision_mismatch")
    if base["model_catalog_entry_id"] is not None:
        cat = facts.catalog
        if (
            cat is None
            or cat.entry_id != base["model_catalog_entry_id"]
            or cat.version != base["catalog_version"]
        ):
            raise _fail("catalog_mapping_changed")
        if cat.mapped_artifact_id != base["trainable_artifact_id"]:
            raise _fail("catalog_artifact_mismatch")
    if split.manifest_id != train["split_manifest_id"] or split.status != "locked":
        raise _fail("split_manifest_not_locked")
    if split.frozen_sha256 != data["frozen_manifest_sha256"]:
        raise _fail("split_manifest_changed")
    if split.hash_verified is not True:
        raise _fail("split_manifest_tampered")
    if split.quality_report_current is not True:
        raise _fail("quality_report_changed")
    if split.grants_current is not True:
        raise _fail("dataset_grant_changed")
    if split.train_count < 1:
        raise _fail("train_split_empty")
    if split.validation_count < 1:
        raise _fail("validation_split_empty")
    budget = facts.budget
    if budget is None:
        raise _fail("budget_policy_missing")
    if "training" not in budget.scopes:
        raise _fail("budget_scope_not_authorized")
    if budget.currency != doc["budget"]["currency"]:
        raise _fail("budget_currency_mismatch")
    if Decimal(doc["budget"]["max_cost"]) > budget.total - budget.reserved:
        raise _fail("budget_limit_exceeds_policy")

    resolved = {
        **_thaw(doc),
        "server": {
            "weights_digest": art.weights_digest,
            "base_revision": art.base_revision,
            "split_manifest_version": split.manifest_version,
            "frozen_manifest_sha256": split.frozen_sha256,
            "train_role_sha256": split.train_role_sha256,
            "validation_role_sha256": split.validation_role_sha256,
            "train_count": split.train_count,
            "validation_count": split.validation_count,
            "budget_policy_id": budget.policy_id,
            "budget_policy_row_version": budget.row_version,
            "security_profile": facts.security_profile,
        },
    }
    frozen = _freeze(resolved)
    return ResolvedTrainingConfig(doc=frozen, config_sha256=compute_config_sha256(frozen))
