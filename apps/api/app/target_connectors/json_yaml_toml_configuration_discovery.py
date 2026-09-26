"""JSON/YAML/TOML/env-template configuration discovery (FTF-04A11, Section
7A.4/66/77/81).

Section 7A.4's own "Supported deterministic discovery must include at
minimum" bullet list names "JSON/YAML/TOML/env-template configuration
discovery" as one of the baseline discovery types this task owns in
full -- reading bounded configuration documents from an already-captured,
read-only source snapshot and producing normalized, secret-redacted
per-file evidence. It deliberately does not itself resolve "model
configuration and LiteLLM aliases" (step 4) or "tool/function schemas"
(step 6) from the values it reads, and does not assemble a
`DiscoveredAgentCandidate` row (step 12, FTF-04A17's own already-CERTIFIED
storage schema) -- this task's own evidence is one deterministic input a
later composition task consumes, the same "detection is not the same act
as storage/selection" boundary FTF-04A08/FTF-04A09 already drew one layer
down.

This task's own acceptance clause ("Read bounded JSON/YAML/TOML/
env-template config and normalize evidence without secrets") is met by:
only `json.loads` (stdlib), `tomllib.loads` (stdlib) and `yaml.safe_load`
(PyYAML's own restricted-constructor entry point -- never `yaml.load` with
an unsafe/full loader) are ever used to parse scanned content; a
`.env`/`.env.*`-named file is read as literal `KEY=VALUE` lines only, never
shell-expanded or executed. Every scalar value reached under a key path
this module's own `_is_secret_like_key` recognizes as secret-shaped
(`SECRET`, `PASSWORD`, `TOKEN`, `APIKEY`, `PRIVATEKEY`, `ACCESSKEY`,
`CREDENTIAL`, `CLIENTSECRET`, `PASSPHRASE`, matched against the key's own
alphanumeric-only, upper-cased form) is replaced with a length-only
`<redacted:N chars>`/`<empty>` marker -- the same disclosed redaction shape
`apps/api/app/config/configuration_system.py`'s own `_redact` already
established for this repository -- never the real value. This is a
disclosed, non-exhaustive key-name heuristic, the same "known,
disclosed false-negative class" precedent `workers/data/
pii_secrets_detector.py`'s own module docstring already established one
task over; it is not a substitute for a real secret scanner, and does not
claim to be one.

This task's own negative_acceptance ("Unsafe YAML tags, malformed
TOML/JSON, huge nesting and dotenv secrets cannot execute/leak") is met
by: `yaml.safe_load`'s own restricted constructor refuses any tag outside
its safe set (`!!python/object/apply:...` and similar constructs raise a
caught `yaml.YAMLError`, confirmed never to execute); malformed
JSON/TOML/YAML content raises a caught, stdlib-typed decode error rather
than ever escaping this worker; adversarially deep nesting either raises a
caught `RecursionError` during parsing itself, or -- for content that
parses successfully -- is bounded by this module's own `ResourceLimits.
max_depth`/`max_keys_per_document` during the post-parse flatten walk,
disclosing `keys_truncated=True` rather than silently reporting a complete
scan; and no dotenv/config value ever passes through `eval`/`exec`/a
shell/a subprocess -- `_parse_dotenv` performs a pure, non-executing
line-oriented split, and no shell/environment-variable expansion of a
dotenv value is ever attempted.

Resource limits mirror FTF-04A08/FTF-04A09's own three-bound shape
(`max_files`/`max_file_bytes`/`max_total_bytes`), plus two limits unique to
this task's own tree-shaped input (`max_keys_per_document`/`max_depth`).
Every limit that stops a run or a document's own flatten walk before it
reaches the end sets the matching `truncated`/`keys_truncated` flag --
an honestly incomplete scan is disclosed, never silently reported as
complete.

Input shape: reuses FTF-04A08's own connector-agnostic `SourceFileRef`
(`apps/api/app/target_connectors/source_analysis_worker.py`) rather than
inventing a second one, so a caller that already assembled a
`Sequence[SourceFileRef]` for FTF-04A08/FTF-04A09 can hand the identical
sequence to this module with no adaptation. Only entries whose filename
this module's own `_detect_format` recognizes (`.json`, `.yaml`/`.yml`,
`.toml`, or a `.env`/`.env.*` basename) are ever considered; this module
does not re-implement FTF-04A08's own ignore-directory/secret-path rules
itself -- the same reliance FTF-04A09 already disclosed one task over.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass

import yaml

from app.target_connectors.source_analysis_worker import SourceFileRef

__all__ = [
    "ConfigDiscoveryError",
    "ConfigDiscoveryEvidence",
    "ConfigDocumentEvidence",
    "ConfigKeyEvidence",
    "ResourceLimits",
    "run_json_yaml_toml_configuration_discovery",
]

# Disclosed, non-exhaustive key-name heuristic -- see this module's own
# docstring. Matched against a key's own alphanumeric-only, upper-cased
# form, so "api_key"/"API-Key"/"apiKey" all normalize to "APIKEY" and match,
# while an unrelated key such as "author" ("AUTHOR") never contains any of
# these markers.
_SECRET_KEY_MARKERS: tuple[str, ...] = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "APIKEY",
    "PRIVATEKEY",
    "ACCESSKEY",
    "CREDENTIAL",
    "CLIENTSECRET",
    "PASSPHRASE",
)

_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]")

_DOTENV_LINE_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")

# `json.loads`/`tomllib.loads`/`yaml.safe_load` on real, adversarial or
# malformed content can raise any of these -- every one is caught and
# counted, never allowed to escape this worker (this task's own
# negative_acceptance, "malformed TOML/JSON... cannot execute/leak").
# `json.JSONDecodeError`/`tomllib.TOMLDecodeError` are themselves
# `ValueError` subclasses; `yaml.YAMLError` is not, so it is listed
# separately.
_PARSE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    UnicodeDecodeError,
    RecursionError,
    MemoryError,
    yaml.YAMLError,
)


class ConfigDiscoveryError(Exception):
    """A caller-supplied `SourceFileRef` violates this worker's own input
    shape (negative size) -- rejected at the boundary rather than
    propagated downstream."""


@dataclass(frozen=True)
class ResourceLimits:
    """This task's own real "resource-limited" negative_acceptance half --
    see this module's own docstring for what each bound independently
    protects against."""

    max_files: int = 5_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 50_000_000
    max_keys_per_document: int = 500
    max_depth: int = 20


@dataclass(frozen=True)
class ConfigKeyEvidence:
    """One real, bounded-depth scalar leaf reached while flattening a
    parsed document. `value` is the real scalar for a non-secret-shaped
    key path, or this module's own length-only redaction marker for a
    secret-shaped one -- never the real value in the latter case."""

    key_path: str
    value_type: str
    secret_like: bool
    value: str | int | float | bool | None

    def to_json(self) -> dict[str, object]:
        return {
            "key_path": self.key_path,
            "value_type": self.value_type,
            "secret_like": self.secret_like,
            "value": self.value,
        }


@dataclass(frozen=True)
class ConfigDocumentEvidence:
    """This task's own real per-file evidence -- see this module's own
    docstring for the full scope boundary."""

    relative_path: str
    format: str
    resolved: bool
    reason: str
    keys: tuple[ConfigKeyEvidence, ...]
    keys_truncated: bool

    def to_evidence_json(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "format": self.format,
            "resolved": self.resolved,
            "reason": self.reason,
            "keys": [key.to_json() for key in self.keys],
            "keys_truncated": self.keys_truncated,
        }


@dataclass(frozen=True)
class ConfigDiscoveryEvidence:
    documents: tuple[ConfigDocumentEvidence, ...]
    files_scanned: int
    files_skipped_oversized: int
    parse_errors: int
    total_bytes_scanned: int
    truncated: bool = False


def _redact(value: str) -> str:
    """Same disclosed redaction shape as `apps/api/app/config/
    configuration_system.py`'s own `_redact` -- length only, never the
    real value."""
    return "<empty>" if not value else f"<redacted:{len(value)} chars>"


def _is_secret_like_key(key: str) -> bool:
    compact = _NON_ALPHANUMERIC.sub("", key).upper()
    return any(marker in compact for marker in _SECRET_KEY_MARKERS)


def _detect_format(relative_path: str) -> str | None:
    basename = relative_path.rsplit("/", 1)[-1]
    lower = basename.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".yaml") or lower.endswith(".yml"):
        return "yaml"
    if lower.endswith(".toml"):
        return "toml"
    if basename == ".env" or basename.startswith(".env."):
        return "dotenv"
    return None


def _parse_dotenv(text: str) -> dict[str, str]:
    """A pure, non-executing line-oriented split -- never shell/variable
    expansion, never a subprocess. An unparseable line (no `#` comment, no
    `KEY=VALUE` shape) is skipped, not guessed."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DOTENV_LINE_PATTERN.match(line)
        if match is None:
            continue
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[match.group("key")] = value
    return values


def _value_type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "container"


def _normalize_leaf_value(value: object) -> str | int | float | bool | None:
    """TOML/YAML can yield scalar types outside this module's own declared
    union (for example a `datetime.date` from TOML); such a value is
    reported via its own `str()` form rather than left untyped or dropped."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _leaf_key_name(prefix: str) -> str:
    if not prefix:
        return ""
    last_segment = prefix.rsplit(".", 1)[-1]
    return last_segment.split("[", 1)[0]


def _flatten(
    value: object,
    *,
    prefix: str,
    depth: int,
    limits: ResourceLimits,
    keys: list[ConfigKeyEvidence],
) -> bool:
    """Depth- and key-count-bounded flatten of a parsed document into
    `ConfigKeyEvidence` leaves. Returns True once either bound stops the
    walk before it reached every leaf (this task's own `keys_truncated`)."""
    if len(keys) >= limits.max_keys_per_document:
        return True
    if depth > limits.max_depth:
        return True

    if isinstance(value, dict):
        for raw_key, child in value.items():
            key_name = raw_key if isinstance(raw_key, str) else str(raw_key)
            path = f"{prefix}.{key_name}" if prefix else key_name
            if _flatten(child, prefix=path, depth=depth + 1, limits=limits, keys=keys):
                return True
        return False

    if isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            if _flatten(item, prefix=path, depth=depth + 1, limits=limits, keys=keys):
                return True
        return False

    secret_like = _is_secret_like_key(_leaf_key_name(prefix))
    display_value: str | int | float | bool | None
    if secret_like:
        display_value = _redact(value if isinstance(value, str) else str(value))
    else:
        display_value = _normalize_leaf_value(value)
    keys.append(
        ConfigKeyEvidence(
            key_path=prefix or "<root>",
            value_type=_value_type_name(value),
            secret_like=secret_like,
            value=display_value,
        )
    )
    return len(keys) >= limits.max_keys_per_document


def _flatten_document(
    value: object, limits: ResourceLimits
) -> tuple[tuple[ConfigKeyEvidence, ...], bool]:
    keys: list[ConfigKeyEvidence] = []
    truncated = _flatten(value, prefix="", depth=0, limits=limits, keys=keys)
    return tuple(keys), truncated


def run_json_yaml_toml_configuration_discovery(
    files: Sequence[SourceFileRef], *, resource_limits: ResourceLimits | None = None
) -> ConfigDiscoveryEvidence:
    """Section 7A.4's own "JSON/YAML/TOML/env-template configuration
    discovery" baseline bullet -- see this module's own docstring for the
    full scope boundary. Never raises on adversarial config content
    (caught and counted per file, see each document's own `resolved`/
    `reason`); only raises `ConfigDiscoveryError` for a structurally
    unsafe caller-supplied `SourceFileRef` itself (negative size) -- a
    caller bug, not untrusted snapshot content."""
    limits = resource_limits or ResourceLimits()

    documents: list[ConfigDocumentEvidence] = []
    files_scanned = 0
    files_skipped_oversized = 0
    parse_errors = 0
    total_bytes_scanned = 0
    truncated = False

    for file_ref in files:
        fmt = _detect_format(file_ref.relative_path)
        if fmt is None:
            continue
        if not isinstance(file_ref.size, int) or file_ref.size < 0:
            raise ConfigDiscoveryError(
                f"size must be a real, non-negative integer for "
                f"{file_ref.relative_path!r}: {file_ref.size!r}"
            )

        if files_scanned >= limits.max_files:
            truncated = True
            break

        if file_ref.size > limits.max_file_bytes:
            files_skipped_oversized += 1
            continue
        if total_bytes_scanned + file_ref.size > limits.max_total_bytes:
            truncated = True
            continue

        content = file_ref.read_content()
        files_scanned += 1
        total_bytes_scanned += len(content)

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            parse_errors += 1
            documents.append(
                ConfigDocumentEvidence(
                    relative_path=file_ref.relative_path,
                    format=fmt,
                    resolved=False,
                    reason="not_utf8_text",
                    keys=(),
                    keys_truncated=False,
                )
            )
            continue

        try:
            if fmt == "json":
                parsed: object = json.loads(text)
            elif fmt == "yaml":
                parsed = yaml.safe_load(text)
            elif fmt == "toml":
                parsed = tomllib.loads(text)
            else:
                parsed = _parse_dotenv(text)
        except _PARSE_EXCEPTIONS:
            parse_errors += 1
            documents.append(
                ConfigDocumentEvidence(
                    relative_path=file_ref.relative_path,
                    format=fmt,
                    resolved=False,
                    reason="malformed_document",
                    keys=(),
                    keys_truncated=False,
                )
            )
            continue

        if parsed is None:
            documents.append(
                ConfigDocumentEvidence(
                    relative_path=file_ref.relative_path,
                    format=fmt,
                    resolved=True,
                    reason="empty_document",
                    keys=(),
                    keys_truncated=False,
                )
            )
            continue

        keys, keys_truncated = _flatten_document(parsed, limits)
        documents.append(
            ConfigDocumentEvidence(
                relative_path=file_ref.relative_path,
                format=fmt,
                resolved=True,
                reason="parsed",
                keys=keys,
                keys_truncated=keys_truncated,
            )
        )

    return ConfigDiscoveryEvidence(
        documents=tuple(documents),
        files_scanned=files_scanned,
        files_skipped_oversized=files_skipped_oversized,
        parse_errors=parse_errors,
        total_bytes_scanned=total_bytes_scanned,
        truncated=truncated,
    )
