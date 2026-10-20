#!/usr/bin/env python3
"""FTF-04A16 -- Runtime trace/deployment contract discovery.

Runnable without any database and without any external network egress:
`run_runtime_trace_deployment_contract_discovery` is a pure, synchronous,
no-I/O-of-its-own function over a caller-supplied `SourceFileRef` sequence
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.").

This task's own acceptance clause ("Identify trace/deployment configuration
and actual runtime-binding inputs without contacting arbitrary URLs") is
covered by `test_dockerfile_expose_and_env_declarations_are_captured`,
`test_procfile_process_types_and_var_references_captured`,
`test_compose_ports_and_dict_environment_captured`,
`test_compose_list_environment_kv_string_captured`,
`test_python_os_environ_get_reference_detected`,
`test_python_os_getenv_reference_detected`,
`test_python_os_environ_subscript_reference_detected`,
`test_js_process_env_dot_reference_detected`,
`test_js_process_env_bracket_reference_detected`, and
`test_top_level_config_key_is_captured`.

This task's own negative_acceptance ("Detected logs are not automatically
harvested and a string in config is not proof a production endpoint is
reachable") is covered by
`test_url_shaped_value_is_disclosed_as_unverified_never_reachable`,
`test_non_url_value_has_no_reachability_marker`,
`test_code_reference_has_no_literal_value_or_reachability`,
`test_detected_log_path_is_never_opened`,
`test_dockerfile_command_substitution_is_never_executed`,
`test_procfile_command_is_never_executed`,
`test_python_module_is_never_executed`,
`test_unrecognized_key_name_is_never_treated_as_binding_or_trace`,
`test_non_manifest_non_source_file_is_skipped`,
`test_oversized_file_is_skipped_not_read`,
`test_total_byte_budget_truncates_run`,
`test_max_files_budget_truncates_run`, and
`test_negative_size_is_rejected`.

Run directly: python3 tests/tasks/test_ftf_04a16.py
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.target_connectors.runtime_trace_deployment_contract_discovery import (  # noqa: E402
    ResourceLimits,
    RuntimeTraceDeploymentDiscoveryError,
    run_runtime_trace_deployment_contract_discovery,
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


def _manifest(evidence, relative_path: str):
    matches = [m for m in evidence.manifests if m.relative_path == relative_path]
    assert len(matches) == 1
    return matches[0]


def _refs_for_key(evidence, key_name: str):
    return [r for r in evidence.binding_references if r.key_name == key_name]


def test_dockerfile_expose_and_env_declarations_are_captured() -> None:
    content = (
        b"FROM python:3.12-slim\n"
        b"EXPOSE 8080/tcp 9090\n"
        b"ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318\n"
        b"ARG DEPLOY_HOST\n"
    )
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("Dockerfile", content)])
    manifest = _manifest(evidence, "Dockerfile")
    otel_refs = _refs_for_key(evidence, "OTEL_EXPORTER_OTLP_ENDPOINT")
    deploy_refs = _refs_for_key(evidence, "DEPLOY_HOST")
    check(
        "FTF-04A16",
        "a Dockerfile's EXPOSE ports are captured and its ENV/ARG "
        "declarations whose key name matches this task's own closed "
        "trace/runtime-binding marker set are recorded as evidence",
        manifest.manifest_kind == "dockerfile"
        and manifest.resolved is True
        and set(manifest.declared_ports) == {"8080", "9090"}
        and len(otel_refs) == 1
        and otel_refs[0].category == "trace_observability"
        and otel_refs[0].access_pattern == "docker_env_declaration"
        and len(deploy_refs) == 1
        and deploy_refs[0].category == "runtime_binding"
        and deploy_refs[0].access_pattern == "docker_arg_declaration"
        and deploy_refs[0].literal_value is None,
    )


def test_dockerfile_secret_like_env_value_is_redacted() -> None:
    raw_secret = "sk-live-super-secret-value-123456"
    content = f"ENV DEPLOY_TOKEN={raw_secret}\n".encode()
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("Dockerfile", content)])
    refs = _refs_for_key(evidence, "DEPLOY_TOKEN")
    serialized = repr(evidence)
    check(
        "FTF-04A16",
        "a secret-shaped ENV value is never leaked into the returned "
        "evidence, including its own repr form",
        len(refs) == 1
        and refs[0].literal_value is not None
        and refs[0].literal_value.startswith("<redacted:")
        and raw_secret not in serialized,
    )


def test_procfile_process_types_and_var_references_captured() -> None:
    content = b"web: gunicorn app:app --bind 0.0.0.0:$PORT\nrelease: alembic upgrade head\n"
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("Procfile", content)])
    manifest = _manifest(evidence, "Procfile")
    port_refs = _refs_for_key(evidence, "PORT")
    check(
        "FTF-04A16",
        "a Procfile's process types are structurally parsed and a "
        "$VAR-shaped runtime-binding reference inside a process command is "
        "captured, without ever running the command",
        manifest.manifest_kind == "procfile"
        and len(port_refs) == 1
        and port_refs[0].source_kind == "procfile"
        and port_refs[0].category == "runtime_binding",
    )


def test_compose_ports_and_dict_environment_captured() -> None:
    content = (
        b"services:\n"
        b"  web:\n"
        b"    ports:\n"
        b'      - "3000:3000"\n'
        b"    environment:\n"
        b"      PORT: '3000'\n"
        b"      SENTRY_DSN: https://example.invalid/1\n"
    )
    evidence = run_runtime_trace_deployment_contract_discovery(
        [_ref("docker-compose.yml", content)]
    )
    manifest = _manifest(evidence, "docker-compose.yml")
    port_refs = _refs_for_key(evidence, "PORT")
    sentry_refs = _refs_for_key(evidence, "SENTRY_DSN")
    check(
        "FTF-04A16",
        "a compose file's dict-form ports/environment mapping is parsed by "
        "reusing FTF-04A11's own worker, and each real variable name "
        "matching this task's own marker set is captured under its own "
        "real key name (never the container array key 'environment')",
        manifest.manifest_kind == "compose"
        and manifest.resolved is True
        and manifest.declared_ports == ("3000:3000",)
        and len(port_refs) == 1
        and port_refs[0].category == "runtime_binding"
        and len(sentry_refs) == 1
        and sentry_refs[0].category == "trace_observability",
    )


def test_compose_list_environment_kv_string_captured() -> None:
    content = b"services:\n  web:\n    environment:\n      - RAILWAY_STATIC_URL=example.invalid\n"
    evidence = run_runtime_trace_deployment_contract_discovery(
        [_ref("docker-compose.yaml", content)]
    )
    refs = _refs_for_key(evidence, "RAILWAY_STATIC_URL")
    check(
        "FTF-04A16",
        "a compose file's list-form 'KEY=VALUE' environment entry is split "
        "into its real variable name and value, not reported under the "
        "array's own literal key name 'environment'",
        len(refs) == 1
        and refs[0].category == "runtime_binding"
        and refs[0].literal_value == "example.invalid",
    )


def test_top_level_config_key_is_captured() -> None:
    content = b'{"otel_exporter_endpoint": "http://collector:4318"}'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("railway.json", content)])
    refs = _refs_for_key(evidence, "otel_exporter_endpoint")
    check(
        "FTF-04A16",
        "a top-level (non-environment-nested) config key matching this "
        "task's own marker set is still captured",
        len(refs) == 1 and refs[0].category == "trace_observability",
    )


def test_compose_malformed_is_disclosed_unresolved() -> None:
    content = b"a: [1, 2\nb: unterminated\n"
    evidence = run_runtime_trace_deployment_contract_discovery(
        [_ref("docker-compose.yml", content)]
    )
    manifest = _manifest(evidence, "docker-compose.yml")
    check(
        "FTF-04A16",
        "malformed compose YAML is disclosed as unresolved, never raised "
        "out of this worker and never silently reported as parsed",
        manifest.resolved is False and evidence.parse_errors == 1,
    )


def test_python_os_environ_get_reference_detected() -> None:
    content = b'import os\nvalue = os.environ.get("OTEL_SERVICE_NAME")\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    refs = _refs_for_key(evidence, "OTEL_SERVICE_NAME")
    check(
        "FTF-04A16",
        "a real os.environ.get(...) call with a literal string key "
        "matching this task's own marker set is statically traced via AST",
        len(refs) == 1
        and refs[0].source_kind == "python"
        and refs[0].access_pattern == "os.environ.get"
        and refs[0].line == 2
        and refs[0].literal_value is None,
    )


def test_python_os_getenv_reference_detected() -> None:
    content = b'import os\nhost = os.getenv("DEPLOY_HOST", "0.0.0.0")\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    refs = _refs_for_key(evidence, "DEPLOY_HOST")
    check(
        "FTF-04A16",
        "a real os.getenv(...) call with a literal string key is statically traced via AST",
        len(refs) == 1 and refs[0].access_pattern == "os.getenv",
    )


def test_python_os_environ_subscript_reference_detected() -> None:
    content = b'import os\nport = os.environ["PORT"]\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    refs = _refs_for_key(evidence, "PORT")
    check(
        "FTF-04A16",
        "a real os.environ[...] subscript access with a literal string key "
        "is statically traced via AST",
        len(refs) == 1 and refs[0].access_pattern == "os.environ[]",
    )


def test_python_dynamic_key_is_unresolved_not_guessed() -> None:
    content = b'import os\nname = "PORT"\nvalue = os.environ.get(name)\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    check(
        "FTF-04A16",
        "a non-literal (dynamically computed) key argument is never "
        "guessed at -- no evidence is fabricated for it",
        len(evidence.binding_references) == 0,
    )


def test_python_module_is_never_executed() -> None:
    marker = Path("/tmp/ftf_04a16_python_should_never_execute")
    if marker.exists():
        marker.unlink()
    content = (
        f'import os\nos.system("touch {marker}")\nvalue = os.environ.get("OTEL_SERVICE_NAME")\n'
    ).encode()
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    check(
        "FTF-04A16",
        "Python source is only ever ast.parse'd, never imported/executed "
        "-- the module-level os.system call in the fixture never actually "
        "runs, while the real AST-traceable reference is still captured",
        not marker.exists() and len(_refs_for_key(evidence, "OTEL_SERVICE_NAME")) == 1,
    )


def test_js_process_env_dot_reference_detected() -> None:
    content = b"const port = process.env.PORT;\nconsole.log(port);\n"
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("src/server.ts", content)])
    refs = _refs_for_key(evidence, "PORT")
    check(
        "FTF-04A16",
        "a process.env.NAME dot-access reference in TypeScript source is "
        "captured by this task's own disclosed regex scan",
        len(refs) == 1
        and refs[0].source_kind == "javascript_typescript"
        and refs[0].access_pattern == "process.env.NAME"
        and refs[0].line == 1,
    )


def test_js_process_env_bracket_reference_detected() -> None:
    content = b'const dsn = process.env["SENTRY_DSN"];\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("src/server.js", content)])
    refs = _refs_for_key(evidence, "SENTRY_DSN")
    check(
        "FTF-04A16",
        'a process.env["NAME"] bracket-access reference in JavaScript '
        "source is captured by this task's own disclosed regex scan",
        len(refs) == 1 and refs[0].access_pattern == "process.env[NAME]",
    )


def test_unrecognized_key_name_is_never_treated_as_binding_or_trace() -> None:
    content = b'import os\nvalue = os.environ.get("SOME_UNRELATED_APPLICATION_SETTING")\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    check(
        "FTF-04A16",
        "an env-var reference whose key name matches none of this task's "
        "own closed, named marker sets is never fabricated as "
        "runtime-binding or trace evidence",
        len(evidence.binding_references) == 0,
    )


def test_url_shaped_value_is_disclosed_as_unverified_never_reachable() -> None:
    content = b"ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318\n"
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("Dockerfile", content)])
    refs = _refs_for_key(evidence, "OTEL_EXPORTER_OTLP_ENDPOINT")
    check(
        "FTF-04A16",
        "a detected URL-shaped configuration value always carries the "
        "fixed 'unverified_static_reference' marker -- this worker never "
        "claims or infers the endpoint is actually reachable",
        len(refs) == 1 and refs[0].endpoint_reachability == "unverified_static_reference",
    )


def test_non_url_value_has_no_reachability_marker() -> None:
    content = b"ENV DEPLOY_HOST=0.0.0.0\n"
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("Dockerfile", content)])
    refs = _refs_for_key(evidence, "DEPLOY_HOST")
    check(
        "FTF-04A16",
        "a non-URL-shaped literal value carries no reachability marker at "
        "all -- the marker is reserved for the specific claim this task's "
        "own negative_acceptance forbids",
        len(refs) == 1 and refs[0].endpoint_reachability is None,
    )


def test_code_reference_has_no_literal_value_or_reachability() -> None:
    content = b'import os\nvalue = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")\n'
    evidence = run_runtime_trace_deployment_contract_discovery([_ref("app/server.py", content)])
    refs = _refs_for_key(evidence, "OTEL_EXPORTER_OTLP_ENDPOINT")
    check(
        "FTF-04A16",
        "a code-level env-var reference has no knowable runtime value via "
        "static analysis -- literal_value and endpoint_reachability are "
        "both honestly None, never guessed",
        len(refs) == 1 and refs[0].literal_value is None and refs[0].endpoint_reachability is None,
    )


def test_detected_log_path_is_never_opened() -> None:
    forbidden_path = "/tmp/ftf_04a16_should_never_be_opened.log"
    content = f"ENV LOGFILE={forbidden_path}\n".encode()

    real_open = builtins.open

    def _guarded_open(file, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if isinstance(file, str) and forbidden_path in file:
            raise AssertionError("must never open a path discovered inside scanned content")
        return real_open(file, *args, **kwargs)

    builtins.open = _guarded_open
    try:
        evidence = run_runtime_trace_deployment_contract_discovery([_ref("Dockerfile", content)])
    finally:
        builtins.open = real_open

    refs = _refs_for_key(evidence, "LOGFILE")
    check(
        "FTF-04A16",
        "a detected log-file path is recorded as evidence text only -- "
        "this worker never opens the referenced path itself (logs are not "
        "automatically harvested)",
        len(refs) == 1 and refs[0].literal_value == forbidden_path,
    )


def test_dockerfile_command_substitution_is_never_executed() -> None:
    marker = Path("/tmp/ftf_04a16_dockerfile_should_never_execute")
    if marker.exists():
        marker.unlink()
    content = f"ENV DEPLOY_HOST=$(touch {marker})\n".encode()
    run_runtime_trace_deployment_contract_discovery([_ref("Dockerfile", content)])
    check(
        "FTF-04A16",
        "an ENV value containing shell command-substitution syntax is "
        "read as a literal string, never expanded or executed",
        not marker.exists(),
    )


def test_procfile_command_is_never_executed() -> None:
    marker = Path("/tmp/ftf_04a16_procfile_should_never_execute")
    if marker.exists():
        marker.unlink()
    content = f"web: touch {marker} && echo $PORT\n".encode()
    run_runtime_trace_deployment_contract_discovery([_ref("Procfile", content)])
    check(
        "FTF-04A16",
        "a Procfile process command is only ever pattern-matched for "
        "$VAR references, never executed",
        not marker.exists(),
    )


def test_non_manifest_non_source_file_is_skipped() -> None:
    evidence = run_runtime_trace_deployment_contract_discovery(
        [_ref("README.md", b"# hello\nEXPOSE 8080\n")]
    )
    check(
        "FTF-04A16",
        "a file whose name/extension this worker does not recognize as a "
        "deployment manifest or Python/JS/TS source is skipped outright, "
        "never guessed at",
        evidence.files_scanned == 0 and len(evidence.manifests) == 0,
    )


def test_oversized_file_is_skipped_not_read() -> None:
    content = b"EXPOSE 8080\n"
    evidence = run_runtime_trace_deployment_contract_discovery(
        [_ref("Dockerfile", content)],
        resource_limits=ResourceLimits(max_file_bytes=len(content) - 1),
    )
    check(
        "FTF-04A16",
        "a file above max_file_bytes is skipped without ever being read",
        evidence.files_skipped_oversized == 1 and evidence.files_scanned == 0,
    )


def test_total_byte_budget_truncates_run() -> None:
    content = b"EXPOSE 8080\n"
    refs = [_ref(f"pkg{i}/Dockerfile", content) for i in range(5)]
    evidence = run_runtime_trace_deployment_contract_discovery(
        refs, resource_limits=ResourceLimits(max_total_bytes=len(content))
    )
    check(
        "FTF-04A16",
        "once the cumulative byte budget is spent, further file content is "
        "never read and the run is disclosed as truncated",
        evidence.truncated is True and evidence.total_bytes_scanned <= len(content),
    )


def test_max_files_budget_truncates_run() -> None:
    content = b"EXPOSE 8080\n"
    refs = [_ref(f"pkg{i}/Dockerfile", content) for i in range(10)]
    evidence = run_runtime_trace_deployment_contract_discovery(
        refs, resource_limits=ResourceLimits(max_files=3)
    )
    check(
        "FTF-04A16",
        "the scanned-file count never exceeds max_files, and the run "
        "discloses truncated=True rather than silently reporting a "
        "complete scan",
        evidence.files_scanned == 3 and evidence.truncated is True,
    )


def test_negative_size_is_rejected() -> None:
    bad_ref = SourceFileRef(relative_path="Dockerfile", size=-1, read_content=lambda: b"")
    raised = False
    try:
        run_runtime_trace_deployment_contract_discovery([bad_ref])
    except RuntimeTraceDeploymentDiscoveryError:
        raised = True
    check(
        "FTF-04A16",
        "a structurally invalid (negative-size) SourceFileRef is rejected "
        "outright, not silently accepted",
        raised,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-04A16 Runtime trace/deployment contract discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-04A16 Runtime trace/deployment contract discovery: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
