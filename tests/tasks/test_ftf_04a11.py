#!/usr/bin/env python3
"""FTF-04A11 -- JSON/YAML/TOML/env-template configuration discovery.

Runnable without any database and without any external network egress:
`run_json_yaml_toml_configuration_discovery` is a pure, synchronous,
no-I/O-of-its-own function over a caller-supplied `SourceFileRef` sequence
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.").

This task's own acceptance clause ("Read bounded JSON/YAML/TOML/
env-template config and normalize evidence without secrets") is covered by
`test_json_object_parsed_into_normalized_keys`,
`test_yaml_object_parsed_into_normalized_keys`,
`test_toml_object_parsed_into_normalized_keys`,
`test_dotenv_parsed_ignores_comments_and_blank_lines`,
`test_dotenv_quoted_value_is_unquoted`,
`test_non_secret_value_is_reported_verbatim`,
`test_secret_like_key_value_is_redacted_not_leaked`, and
`test_secret_like_key_matches_regardless_of_separator_style`.

This task's own negative_acceptance ("Unsafe YAML tags, malformed
TOML/JSON, huge nesting and dotenv secrets cannot execute/leak") is
covered by `test_unsafe_yaml_tag_is_never_constructed`,
`test_malformed_json_does_not_raise`,
`test_malformed_toml_does_not_raise`,
`test_malformed_yaml_does_not_raise`,
`test_deeply_nested_document_does_not_raise_and_is_disclosed_truncated`,
`test_dotenv_value_is_never_shell_expanded`,
`test_non_config_file_is_skipped`,
`test_oversized_file_is_skipped_not_read`,
`test_total_byte_budget_truncates_run`,
`test_max_files_budget_truncates_run`, and
`test_negative_size_is_rejected`.

Run directly: python3 tests/tasks/test_ftf_04a11.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.json_yaml_toml_configuration_discovery import (  # noqa: E402
    ConfigDiscoveryError,
    ResourceLimits,
    run_json_yaml_toml_configuration_discovery,
)
from app.target_connectors.source_analysis_worker import SourceFileRef  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _ref(relative_path: str, content: bytes = b"") -> SourceFileRef:
    return SourceFileRef(
        relative_path=relative_path, size=len(content), read_content=lambda: content
    )


def _only_document(evidence, relative_path: str):
    matches = [doc for doc in evidence.documents if doc.relative_path == relative_path]
    assert len(matches) == 1
    return matches[0]


def _key(document, key_path: str):
    matches = [k for k in document.keys if k.key_path == key_path]
    assert len(matches) == 1
    return matches[0]


def test_json_object_parsed_into_normalized_keys() -> None:
    content = b'{"model": "gpt-4o", "nested": {"count": 3}}'
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.json", content)])
    document = _only_document(evidence, "config/app.json")
    check(
        "FTF-04A11",
        "a well-formed JSON document is resolved and its scalar leaves are "
        "flattened into normalized key-path evidence",
        document.resolved is True
        and document.format == "json"
        and _key(document, "model").value == "gpt-4o"
        and _key(document, "nested.count").value == 3,
    )


def test_yaml_object_parsed_into_normalized_keys() -> None:
    content = b"model: gpt-4o\nnested:\n  count: 3\n"
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.yaml", content)])
    document = _only_document(evidence, "config/app.yaml")
    check(
        "FTF-04A11",
        "a well-formed YAML document is resolved via yaml.safe_load and its "
        "scalar leaves are flattened into normalized key-path evidence",
        document.resolved is True
        and document.format == "yaml"
        and _key(document, "model").value == "gpt-4o"
        and _key(document, "nested.count").value == 3,
    )


def test_toml_object_parsed_into_normalized_keys() -> None:
    content = b'model = "gpt-4o"\n\n[nested]\ncount = 3\n'
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.toml", content)])
    document = _only_document(evidence, "config/app.toml")
    check(
        "FTF-04A11",
        "a well-formed TOML document is resolved via tomllib.loads and its "
        "scalar leaves are flattened into normalized key-path evidence",
        document.resolved is True
        and document.format == "toml"
        and _key(document, "model").value == "gpt-4o"
        and _key(document, "nested.count").value == 3,
    )


def test_dotenv_parsed_ignores_comments_and_blank_lines() -> None:
    content = b"# a comment\n\nMODEL_ALIAS=gpt-4o\nnot a valid line\n"
    evidence = run_json_yaml_toml_configuration_discovery([_ref(".env", content)])
    document = _only_document(evidence, ".env")
    check(
        "FTF-04A11",
        "a .env file is parsed as literal KEY=VALUE lines only; comments, "
        "blank lines and unparseable lines are skipped, not guessed",
        document.resolved is True
        and document.format == "dotenv"
        and len(document.keys) == 1
        and _key(document, "MODEL_ALIAS").value == "gpt-4o",
    )


def test_dotenv_quoted_value_is_unquoted() -> None:
    content = b'MODEL_ALIAS="gpt-4o"\n'
    evidence = run_json_yaml_toml_configuration_discovery([_ref(".env.example", content)])
    document = _only_document(evidence, ".env.example")
    check(
        "FTF-04A11",
        "a quoted dotenv value has its surrounding quotes stripped",
        _key(document, "MODEL_ALIAS").value == "gpt-4o",
    )


def test_non_secret_value_is_reported_verbatim() -> None:
    content = b'{"provider": "openai"}'
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.json", content)])
    document = _only_document(evidence, "config/app.json")
    key = _key(document, "provider")
    check(
        "FTF-04A11",
        "a key path that is not secret-shaped reports its real scalar value "
        "verbatim -- real evidence, not over-redacted",
        key.secret_like is False and key.value == "openai",
    )


def test_secret_like_key_value_is_redacted_not_leaked() -> None:
    raw_secret = "sk-live-super-secret-value-123456"
    content = f"API_KEY={raw_secret}\n".encode()
    evidence = run_json_yaml_toml_configuration_discovery([_ref(".env", content)])
    document = _only_document(evidence, ".env")
    key = _key(document, "API_KEY")
    serialized = repr(document.to_evidence_json())
    check(
        "FTF-04A11",
        "a secret-shaped dotenv key's real value never appears anywhere in "
        "the returned evidence, including its own serialized JSON form",
        key.secret_like is True
        and key.value != raw_secret
        and isinstance(key.value, str)
        and key.value.startswith("<redacted:")
        and raw_secret not in serialized,
    )


def test_secret_like_key_matches_regardless_of_separator_style() -> None:
    content = b'{"database_password": "hunter2", "author": "not-a-secret"}'
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.json", content)])
    document = _only_document(evidence, "config/app.json")
    check(
        "FTF-04A11",
        "'database_password' (snake_case, compound) is recognized as "
        "secret-shaped, while an unrelated key like 'author' (which merely "
        "contains the substring 'auth') is never guessed as secret-shaped",
        _key(document, "database_password").secret_like is True
        and _key(document, "database_password").value != "hunter2"
        and _key(document, "author").secret_like is False
        and _key(document, "author").value == "not-a-secret",
    )


def test_unsafe_yaml_tag_is_never_constructed() -> None:
    marker = Path(os.environ.get("TMPDIR", "/tmp")) / "ftf_04a11_would_be_pwned"
    if marker.exists():
        marker.unlink()
    content = f'a: !!python/object/apply:os.system ["touch {marker}"]\n'.encode()
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.yaml", content)])
    document = _only_document(evidence, "config/app.yaml")
    check(
        "FTF-04A11",
        "an unsafe YAML tag is never constructed/executed by yaml.safe_load "
        "(the referenced os.system call never actually runs) and the "
        "document is disclosed as unresolved, not silently accepted",
        document.resolved is False
        and document.reason == "malformed_document"
        and not marker.exists(),
    )


def test_malformed_json_does_not_raise() -> None:
    content = b'{"model": "gpt-4o", '  # truncated, invalid JSON
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.json", content)])
    document = _only_document(evidence, "config/app.json")
    check(
        "FTF-04A11",
        "malformed JSON is caught and disclosed as unresolved, never raised " "out of this worker",
        document.resolved is False and document.reason == "malformed_document",
    )


def test_malformed_toml_does_not_raise() -> None:
    content = b"model = \n"  # invalid TOML (no value)
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.toml", content)])
    document = _only_document(evidence, "config/app.toml")
    check(
        "FTF-04A11",
        "malformed TOML is caught and disclosed as unresolved, never raised " "out of this worker",
        document.resolved is False and document.reason == "malformed_document",
    )


def test_malformed_yaml_does_not_raise() -> None:
    content = b"a: [1, 2\nb: unterminated flow sequence\n"
    evidence = run_json_yaml_toml_configuration_discovery([_ref("config/app.yaml", content)])
    document = _only_document(evidence, "config/app.yaml")
    check(
        "FTF-04A11",
        "malformed YAML is caught and disclosed as unresolved, never raised " "out of this worker",
        document.resolved is False and document.reason == "malformed_document",
    )


def test_deeply_nested_document_does_not_raise_and_is_disclosed_truncated() -> None:
    nested: object = "leaf"
    for _ in range(200):
        nested = {"child": nested}
    import json as _json

    content = _json.dumps(nested).encode()
    evidence = run_json_yaml_toml_configuration_discovery(
        [_ref("config/deep.json", content)], resource_limits=ResourceLimits(max_depth=20)
    )
    document = _only_document(evidence, "config/deep.json")
    check(
        "FTF-04A11",
        "a document nested far deeper than this worker's own max_depth bound "
        "never raises (RecursionError caught during parsing, or the "
        "post-parse flatten walk stops at the bound) and discloses "
        "keys_truncated=True rather than silently reporting a complete scan",
        document.resolved is True or document.reason == "malformed_document",
    )
    check(
        "FTF-04A11",
        "when the document does parse, the flatten walk honestly discloses "
        "it stopped before reaching every leaf",
        document.reason == "malformed_document" or document.keys_truncated is True,
    )


def test_dotenv_value_is_never_shell_expanded() -> None:
    content = b"GREETING=$(touch /tmp/ftf_04a11_should_never_exist)\n"
    marker = Path("/tmp/ftf_04a11_should_never_exist")
    if marker.exists():
        marker.unlink()
    evidence = run_json_yaml_toml_configuration_discovery([_ref(".env", content)])
    document = _only_document(evidence, ".env")
    check(
        "FTF-04A11",
        "a dotenv value containing shell-command syntax is read as a "
        "literal string, never expanded or executed",
        _key(document, "GREETING").value == "$(touch /tmp/ftf_04a11_should_never_exist)"
        and not marker.exists(),
    )


def test_non_config_file_is_skipped() -> None:
    evidence = run_json_yaml_toml_configuration_discovery([_ref("src/agent.py", b"x = 1\n")])
    check(
        "FTF-04A11",
        "a file whose name this worker's own format detector does not "
        "recognize is skipped outright, never guessed at",
        len(evidence.documents) == 0 and evidence.files_scanned == 0,
    )


def test_oversized_file_is_skipped_not_read() -> None:
    content = b'{"a": 1}'
    evidence = run_json_yaml_toml_configuration_discovery(
        [_ref("config/app.json", content)],
        resource_limits=ResourceLimits(max_file_bytes=len(content) - 1),
    )
    check(
        "FTF-04A11",
        "a file above max_file_bytes is skipped without ever being read",
        evidence.files_skipped_oversized == 1 and evidence.files_scanned == 0,
    )


def test_total_byte_budget_truncates_run() -> None:
    content = b'{"a": 1}'
    refs = [_ref(f"config/app_{i}.json", content) for i in range(5)]
    evidence = run_json_yaml_toml_configuration_discovery(
        refs, resource_limits=ResourceLimits(max_total_bytes=len(content))
    )
    check(
        "FTF-04A11",
        "once the cumulative byte budget is spent, further file content is "
        "never read and the run is disclosed as truncated",
        evidence.truncated is True and evidence.total_bytes_scanned <= len(content),
    )


def test_max_files_budget_truncates_run() -> None:
    content = b'{"a": 1}'
    refs = [_ref(f"config/app_{i}.json", content) for i in range(10)]
    evidence = run_json_yaml_toml_configuration_discovery(
        refs, resource_limits=ResourceLimits(max_files=3)
    )
    check(
        "FTF-04A11",
        "the scanned-file count never exceeds max_files, and the run "
        "discloses truncated=True rather than silently reporting a "
        "complete scan",
        evidence.files_scanned == 3 and evidence.truncated is True,
    )


def test_negative_size_is_rejected() -> None:
    bad_ref = SourceFileRef(relative_path="config/app.json", size=-1, read_content=lambda: b"")
    raised = False
    try:
        run_json_yaml_toml_configuration_discovery([bad_ref])
    except ConfigDiscoveryError:
        raised = True
    check(
        "FTF-04A11",
        "a structurally invalid (negative-size) SourceFileRef is rejected "
        "outright, not silently accepted",
        raised,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A11 JSON/YAML/TOML configuration discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A11 JSON/YAML/TOML configuration discovery: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
