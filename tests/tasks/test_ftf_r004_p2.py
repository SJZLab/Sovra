#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,call-arg,var-annotated,dict-item,index,union-attr,assignment,misc"
# ruff: noqa: E501
"""FTF-R004-P2 -- Inspect bridge provider-side execution boundary + pre-filter attempt evidence.

Runnable without any database or network: `screen_outbound_model_request`,
`capture_response_attempts` and `apply_approval_filter` are pure, synchronous,
no-I/O functions (no migration, no OpenAPI path, no UI).

Acceptance ("Block provider-side hosted tools, remote media dereferences and
unapproved bridged host tools outside the privacy boundary; capture attempted
forbidden tool calls before approval filtering can replace model output."):
test_default_allows_only_approved_function_tools (control),
test_each_hosted_tool_type_is_denied (fault FA),
test_mcp_entry_is_denied (fault FB),
test_remote_media_is_denied_inline_data_is_allowed (fault FC),
test_unapproved_function_tool_is_denied (fault FD),
test_zero_egress_and_air_gapped_deny_an_approved_hosted_tool (fault FE),
test_standard_with_exact_approval_allows_the_approved_capability_only,
test_capture_records_every_attempt_before_filtering (fault FF),
test_filter_refuses_without_matching_evidence (fault FG),
test_denied_attempt_survives_filtering_in_the_evidence (fault FH),
test_filtered_forbidden_attempt_still_grades_as_a_policy_failure (composition).

Negative acceptance ("Reject ... undeclared provider-side web/code/MCP
execution or remote-media fetch, and lost pre-filter forbidden-action
evidence in the bridge"): the same tests, plus
test_screening_makes_zero_network_calls (fault FI),
test_unknown_shapes_are_denied_never_allowed,
test_str_subclass_and_unhashable_values_never_allow_never_type_error (fault FJ),
test_url_classification_is_by_parsing_not_substring (fault FK).

Fault injection (Law 1): the mutation table at the bottom rewrites the real
module in a fresh subprocess; each behavioural probe key must change.

Run directly: python3 tests/tasks/test_ftf_r004_p2.py
"""

from __future__ import annotations

import copy
import itertools
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.security_profile_enforcement import (  # noqa: E402
    Destination,
    RouteAuthorization,
)
from workers.evaluation.adapters.inspect_adapter.provider_boundary import (  # noqa: E402
    CODE_HOSTED_TOOL_DENIED,
    CODE_MCP_DENIED,
    CODE_REMOTE_MEDIA_DEREFERENCE,
    CODE_UNAPPROVED_BRIDGED_TOOL,
    CODE_UNKNOWN_REQUEST_SHAPE,
    AttemptEvidence,
    HostedCapabilityApproval,
    ProviderBoundaryError,
    ScreeningPolicy,
    apply_approval_filter,
    capture_response_attempts,
    screen_outbound_model_request,
)
from workers.evaluation.forbidden_action_grader import (  # noqa: E402
    BackendEffect,
    grade_forbidden_actions,
)
from workers.evaluation.tool_name_grader import EmittedToolCall  # noqa: E402

MODULE = ROOT / "workers" / "evaluation" / "adapters" / "inspect_adapter" / "provider_boundary.py"
FAILURES: list[str] = []
CHECKS = [0]
CID = "FTF-R004-P2"
PROFILES = ("standard", "zero_egress", "air_gapped")
DATA_URI = "data:image/png;base64,iVBORw0KGgo="


class _Str(str):
    """A str subclass whose methods lie -- must never get through."""

    def __eq__(self, other: object) -> bool:  # noqa: D105
        return True

    __hash__ = str.__hash__


def check(criterion_id: str, description: str, condition: bool) -> None:
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _policy(
    org: str = "standard",
    project: str = "standard",
    job: str | None = None,
    tools: frozenset[str] = frozenset({"lookup_order", "send_email"}),
    approval: HostedCapabilityApproval | None = None,
) -> ScreeningPolicy:
    return ScreeningPolicy(
        organization_profile=org,
        project_profile=project,
        job_profile=job,
        approved_tool_names=tools,
        hosted_approval=approval,
    )


def _approval(
    capabilities: frozenset[str] = frozenset({"web_search_preview"}),
) -> HostedCapabilityApproval:
    return HostedCapabilityApproval(
        capabilities=capabilities,
        destination=Destination("managed_provider", "acme"),
        authorization=RouteAuthorization(
            provider="acme",
            surfaces=frozenset({"model"}),
            approved_by="approver",
            account="acct",
            region="eu",
            retention_terms="30d",
        ),
    )


def _chat_function_tool(name: str = "lookup_order") -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def _responses_function_tool(name: str = "lookup_order") -> dict[str, Any]:
    return {"type": "function", "name": name, "parameters": {"type": "object"}}


def _chat_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "target-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [_chat_function_tool()],
        "temperature": 0.2,
    }
    body.update(overrides)
    return body


def _codes(body: Any, policy: ScreeningPolicy | None = None) -> list[str]:
    decision = screen_outbound_model_request(body, policy or _policy())
    return [d.code for d in decision.denials]


def _allowed(body: Any, policy: ScreeningPolicy | None = None) -> bool:
    return screen_outbound_model_request(body, policy or _policy()).allowed


def _raises(fn: Any, *args: Any, **kwargs: Any) -> str:
    try:
        fn(*args, **kwargs)
    except ProviderBoundaryError as exc:
        return exc.code
    except BaseException as exc:  # noqa: BLE001
        return "CRASH:" + type(exc).__name__
    return "NO_RAISE"


# --------------------------------------------------------------------------- screening


def test_default_allows_only_approved_function_tools() -> None:
    check(
        CID,
        "control: a chat body with an approved function tool is allowed",
        _allowed(_chat_body()),
    )
    check(
        CID,
        "control: a responses body with an approved function tool is allowed",
        _allowed({"model": "m", "input": "hi", "tools": [_responses_function_tool()]}),
    )
    check(CID, "a body with no tools at all is allowed", _allowed({"model": "m", "messages": []}))
    decision = screen_outbound_model_request(_chat_body(), _policy())
    check(CID, "an allowed decision carries no denial", decision.denials == ())
    check(CID, "the decision names the effective profile", decision.effective_profile == "standard")


HOSTED_TYPES = (
    "web_search",
    "web_search_preview",
    "web_search_preview_2025_03_11",
    "web_search_20250305",
    "code_interpreter",
    "file_search",
    "computer_use",
    "computer_use_preview",
    "image_generation",
    "code_execution_20250522",
    "x_code_execution_tool",
    "local_shell",
)


def test_each_hosted_tool_type_is_denied() -> None:
    for tool_type in HOSTED_TYPES:
        body = _chat_body(tools=[_chat_function_tool(), {"type": tool_type}])
        check(
            "FTF-R004-P2-FA",
            f"hosted tool type {tool_type!r} is denied as hosted_tool_denied",
            _codes(body) == [CODE_HOSTED_TOOL_DENIED],
        )
        check("FTF-R004-P2-FA", f"{tool_type!r} makes the decision not allowed", not _allowed(body))
    for key in ("web_search_options", "plugins", "plugin"):
        body = _chat_body(**{key: {"anything": 1}})
        check(
            "FTF-R004-P2-FA",
            f"the {key!r} request key is denied as hosted_tool_denied",
            _codes(body) == [CODE_HOSTED_TOOL_DENIED],
        )
        check(
            "FTF-R004-P2-FA",
            f"an unset (null) {key!r} key is not a capability",
            _allowed(_chat_body(**{key: None})),
        )
    for tool_type in ("web_search", "code_interpreter"):
        responses = {"model": "m", "input": "x", "tools": [{"type": tool_type}]}
        check(
            "FTF-R004-P2-FA",
            f"{tool_type!r} in a responses body is denied",
            not _allowed(responses),
        )


def test_mcp_entry_is_denied() -> None:
    body = _chat_body(
        tools=[{"type": "mcp", "server_label": "x", "server_url": "https://mcp.example/"}]
    )
    check(
        "FTF-R004-P2-FB",
        "an mcp tool entry is denied as mcp_denied",
        _codes(body) == [CODE_MCP_DENIED],
    )
    check("FTF-R004-P2-FB", "an mcp entry makes the decision not allowed", not _allowed(body))
    mixed = _chat_body(tools=[{"type": "mcp"}, {"type": "web_search"}, _chat_function_tool("evil")])
    check(
        "FTF-R004-P2-FB",
        "every denial is reported, not only the first",
        _codes(mixed) == [CODE_MCP_DENIED, CODE_HOSTED_TOOL_DENIED, CODE_UNAPPROVED_BRIDGED_TOOL],
    )


MEDIA_URLS = (
    "http://example.com/a.png",
    "https://example.com/a.png",
    "file:///etc/passwd",
    "ftp://example.com/a",
    "gopher://example.com/",
    "//example.com/a.png",
    "https://data:80/a.png",
    "https://example.com/?next=data:image/png;base64,AAAA",
    " data:image/png;base64,AAAA",
    "DATA:image/png;base64,AAAA",
    "data",
    "",
    "s3://bucket/key",
)


def _image_chat(url: Any) -> dict[str, Any]:
    return _chat_body(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "see"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ]
    )


def test_remote_media_is_denied_inline_data_is_allowed() -> None:
    check(
        "FTF-R004-P2-FC",
        "control: an inline data: image is allowed",
        _allowed(_image_chat(DATA_URI)),
    )
    for url in MEDIA_URLS:
        check(
            "FTF-R004-P2-FC",
            f"image_url {url!r} is a remote_media_dereference",
            _codes(_image_chat(url)) == [CODE_REMOTE_MEDIA_DEREFERENCE],
        )
    # A bare-string image_url, the responses input_image and file shapes.
    bare = _chat_body(
        messages=[
            {"role": "user", "content": [{"type": "image_url", "image_url": "https://x.test/a"}]}
        ]
    )
    check(
        "FTF-R004-P2-FC",
        "a bare-string image_url is checked too",
        _codes(bare) == [CODE_REMOTE_MEDIA_DEREFERENCE],
    )
    for url, ok in (
        (DATA_URI, True),
        ("https://x.test/a.png", False),
        ("file:///etc/hosts", False),
    ):
        body = {
            "model": "m",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": url}],
                }
            ],
        }
        check("FTF-R004-P2-FC", f"input_image {url!r} allowed={ok}", _allowed(body) is ok)
    file_id_image = {
        "model": "m",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "file_id": "file-1"}],
            }
        ],
    }
    check(
        "FTF-R004-P2-FC",
        "an input_image file_id (provider-side dereference) is denied",
        not _allowed(file_id_image),
    )
    for part, ok in (
        ({"type": "file", "file": {"file_data": DATA_URI, "filename": "a.png"}}, True),
        ({"type": "file", "file": {"file_id": "file-1"}}, False),
        ({"type": "file", "file": {"file_data": "https://x.test/a.pdf"}}, False),
        ({"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}, True),
        (
            {
                "type": "input_audio",
                "input_audio": {"data": "https://x.test/a.wav", "format": "wav"},
            },
            False,
        ),
        (
            {
                "type": "input_audio",
                "input_audio": {"data": "AAAA", "format": "wav", "url": "https://x.test/a"},
            },
            False,
        ),
    ):
        body = _chat_body(messages=[{"role": "user", "content": [part]}])
        check("FTF-R004-P2-FC", f"media part {part!r} allowed={ok}", _allowed(body) is ok)
    for part, ok in (
        ({"type": "input_file", "file_data": DATA_URI}, True),
        ({"type": "input_file", "file_url": "https://x.test/a.pdf"}, False),
        ({"type": "input_file", "file_id": "file-1"}, False),
    ):
        body = {"model": "m", "input": [{"type": "message", "role": "user", "content": [part]}]}
        check("FTF-R004-P2-FC", f"input_file part {part!r} allowed={ok}", _allowed(body) is ok)
    # A remote URL in a tool-result message is just as much a dereference.
    nested = _chat_body(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": [{"type": "image_url", "image_url": {"url": "http://x.test/a"}}],
            }
        ]
    )
    check("FTF-R004-P2-FC", "a remote image in a tool message is denied", not _allowed(nested))


def test_url_classification_is_by_parsing_not_substring() -> None:
    # The substring 'data:' appears in all of these, none is an inline data URI.
    for url in (
        "https://example.com/data:image/png",
        "http://example.com/?u=data:text/plain,hi",
        "https://data:443@example.com/",
        "https://example.com/#data:image/png;base64,AAAA",
    ):
        check(
            "FTF-R004-P2-FK",
            f"{url!r} (contains 'data:') is still remote",
            not _allowed(_image_chat(url)),
        )
    # And a genuine data URI that contains 'http' is still inline.
    check(
        "FTF-R004-P2-FK",
        "a data: URI that merely mentions http is inline",
        _allowed(_image_chat("data:text/plain,see%20http://x.test/")),
    )


def test_unapproved_function_tool_is_denied() -> None:
    for name in (
        "delete_everything",
        "Lookup_Order",
        "lookup_order ",
        " lookup_order",
        "lookup_order\n",
        "",
    ):
        check(
            "FTF-R004-P2-FD",
            f"function tool {name!r} outside the manifest set is unapproved_bridged_tool",
            _codes(_chat_body(tools=[_chat_function_tool(name)])) == [CODE_UNAPPROVED_BRIDGED_TOOL],
        )
    check(
        "FTF-R004-P2-FD",
        "a responses-shape unapproved function tool is denied",
        _codes({"model": "m", "input": "x", "tools": [_responses_function_tool("nope")]})
        == [CODE_UNAPPROVED_BRIDGED_TOOL],
    )
    check(
        "FTF-R004-P2-FD",
        "legacy `functions` entries are tool declarations too",
        _codes(_chat_body(tools=[], functions=[{"name": "nope"}]))
        == [CODE_UNAPPROVED_BRIDGED_TOOL],
    )
    check(
        "FTF-R004-P2-FD",
        "a legacy approved function is allowed",
        _allowed(_chat_body(tools=[], functions=[{"name": "send_email"}])),
    )
    check(
        "FTF-R004-P2-FD",
        "an empty manifest set allows no function tool",
        _codes(_chat_body(), _policy(tools=frozenset())) == [CODE_UNAPPROVED_BRIDGED_TOOL],
    )


def test_zero_egress_and_air_gapped_deny_an_approved_hosted_tool() -> None:
    approval = _approval()
    body = _chat_body(tools=[_chat_function_tool(), {"type": "web_search_preview"}])
    check(
        "FTF-R004-P2-FE",
        "control: under standard the exactly-approved hosted tool is allowed",
        _allowed(body, _policy(approval=approval)),
    )
    for org, project, job in (
        ("zero_egress", "standard", None),
        ("air_gapped", "standard", None),
        ("standard", "zero_egress", None),
        ("standard", "air_gapped", None),
        ("standard", "standard", "zero_egress"),
        ("standard", "standard", "air_gapped"),
    ):
        policy = _policy(org, project, job, approval=approval)
        check(
            "FTF-R004-P2-FE",
            f"profile ({org}, {project}, {job}) denies the approved hosted tool",
            _codes(body, policy) == [CODE_HOSTED_TOOL_DENIED],
        )
    check(
        "FTF-R004-P2-FE",
        "a weaker project setting never lowers a stricter organization profile",
        not _allowed(body, _policy("zero_egress", "standard", None, approval=approval)),
    )
    check(
        "FTF-R004-P2-FE",
        "an omitted job profile cannot lower the effective profile",
        screen_outbound_model_request(
            body, _policy("air_gapped", "standard", None, approval=approval)
        ).effective_profile
        == "air_gapped",
    )
    plugin = _chat_body(plugins=[{"id": "web"}])
    check(
        "FTF-R004-P2-FE",
        "zero_egress denies an approved plugin capability",
        not _allowed(plugin, _policy("zero_egress", approval=_approval(frozenset({"plugins"})))),
    )


def test_standard_with_exact_approval_allows_the_approved_capability_only() -> None:
    approval = _approval(frozenset({"web_search_preview"}))
    policy = _policy(approval=approval)
    for tool_type in HOSTED_TYPES:
        body = _chat_body(tools=[{"type": tool_type}])
        expected_allowed = tool_type == "web_search_preview"
        check(
            CID,
            f"standard + approval for web_search_preview: {tool_type!r} allowed={expected_allowed}",
            _allowed(body, policy) is expected_allowed,
        )
    check(
        CID,
        "no approval at all denies the hosted tool",
        not _allowed(_chat_body(tools=[{"type": "web_search_preview"}])),
    )
    check(
        CID,
        "an approval never allows an mcp entry it does not name",
        not _allowed(_chat_body(tools=[{"type": "mcp"}]), policy),
    )
    check(
        CID,
        "an approval naming mcp allows it under standard",
        _allowed(
            _chat_body(tools=[{"type": "mcp"}]), _policy(approval=_approval(frozenset({"mcp"})))
        ),
    )
    # An approval never approves remote media or an unapproved function tool.
    check(
        CID,
        "an approval does not approve remote media",
        not _allowed(_image_chat("https://x.test/a.png"), policy),
    )
    check(
        CID,
        "an approval does not approve an unapproved function tool",
        not _allowed(_chat_body(tools=[_chat_function_tool("nope")]), policy),
    )
    wrong_provider = HostedCapabilityApproval(
        capabilities=frozenset({"web_search_preview"}),
        destination=Destination("managed_provider", "other"),
        authorization=approval.authorization,
    )
    check(
        CID,
        "an authorization for a different provider than the destination denies",
        not _allowed(
            _chat_body(tools=[{"type": "web_search_preview"}]), _policy(approval=wrong_provider)
        ),
    )
    wrong_surface = HostedCapabilityApproval(
        capabilities=frozenset({"web_search_preview"}),
        destination=approval.destination,
        authorization=RouteAuthorization(
            provider="acme",
            surfaces=frozenset({"storage"}),
            approved_by="a",
            account="x",
            region="eu",
            retention_terms="30d",
        ),
    )
    check(
        CID,
        "an authorization that does not cover the model surface denies",
        not _allowed(
            _chat_body(tools=[{"type": "web_search_preview"}]), _policy(approval=wrong_surface)
        ),
    )


def test_unknown_shapes_are_denied_never_allowed() -> None:
    cases: list[tuple[str, Any]] = [
        ("tools is not a list", _chat_body(tools={"type": "function"})),
        ("a tool entry is not an object", _chat_body(tools=["function"])),
        ("a tool has no type", _chat_body(tools=[{"name": "lookup_order"}])),
        ("a tool type is not a string", _chat_body(tools=[{"type": 7}])),
        ("a tool type is unhashable", _chat_body(tools=[{"type": []}])),
        ("a tool type is an object", _chat_body(tools=[{"type": {}}])),
        ("an unknown tool type", _chat_body(tools=[{"type": "teleport"}])),
        ("a function tool without a name", _chat_body(tools=[{"type": "function"}])),
        (
            "a function tool with a non-string name",
            _chat_body(tools=[{"type": "function", "name": ["lookup_order"]}]),
        ),
        (
            "a function tool with both name shapes",
            _chat_body(
                tools=[
                    {
                        "type": "function",
                        "name": "lookup_order",
                        "function": {"name": "lookup_order"},
                    }
                ]
            ),
        ),
        (
            "a function tool whose function is not an object",
            _chat_body(tools=[{"type": "function", "function": "lookup_order"}]),
        ),
        ("messages is not a list", _chat_body(messages="hi")),
        ("a message is not an object", _chat_body(messages=["hi"])),
        ("message content is a number", _chat_body(messages=[{"role": "user", "content": 7}])),
        (
            "a content part has no type",
            _chat_body(messages=[{"role": "user", "content": [{"text": "x"}]}]),
        ),
        (
            "a content part has an unhashable type",
            _chat_body(messages=[{"role": "user", "content": [{"type": []}]}]),
        ),
        (
            "an unknown content part type",
            _chat_body(messages=[{"role": "user", "content": [{"type": "hologram"}]}]),
        ),
        (
            "an image part with no reference",
            _chat_body(messages=[{"role": "user", "content": [{"type": "image_url"}]}]),
        ),
        (
            "an image_url object with a non-string url",
            _chat_body(
                messages=[
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": 7}}]}
                ]
            ),
        ),
        ("input is a number", {"model": "m", "input": 7}),
        ("an unknown input item type", {"model": "m", "input": [{"type": "hologram"}]}),
        ("an input item with no type", {"model": "m", "input": [{"role": "user"}]}),
    ]
    for label, body in cases:
        codes = _codes(body)
        check(
            "FTF-R004-P2-UNK",
            f"{label} is denied as unknown_request_shape (got {codes})",
            codes != [] and CODE_UNKNOWN_REQUEST_SHAPE in codes,
        )
        check("FTF-R004-P2-UNK", f"{label} makes the decision not allowed", not _allowed(body))
    for bad_body in ([], "x", 7, None, ()):
        check(
            "FTF-R004-P2-UNK",
            f"a non-object body {bad_body!r} raises the module's typed error",
            _raises(screen_outbound_model_request, bad_body, _policy())
            == "provider_boundary_invalid_request",
        )


def test_str_subclass_and_unhashable_values_never_allow_never_type_error() -> None:
    sneaky = _Str("function")
    body = _chat_body(tools=[{"type": sneaky, "function": {"name": "lookup_order"}}])
    check(
        "FTF-R004-P2-FJ",
        "a str-subclass tool type raises the typed error",
        _raises(screen_outbound_model_request, body, _policy())
        == "provider_boundary_invalid_request",
    )
    name_body = _chat_body(tools=[{"type": "function", "function": {"name": _Str("not-approved")}}])
    check(
        "FTF-R004-P2-FJ",
        "a str-subclass tool name (always-equal) raises the typed error",
        _raises(screen_outbound_model_request, name_body, _policy())
        == "provider_boundary_invalid_request",
    )
    url_body = _image_chat(_Str("https://x.test/a"))
    check(
        "FTF-R004-P2-FJ",
        "a str-subclass media url raises the typed error",
        _raises(screen_outbound_model_request, url_body, _policy())
        == "provider_boundary_invalid_request",
    )
    key_body = _chat_body()
    key_body[_Str("web_search_options")] = {"a": 1}
    check(
        "FTF-R004-P2-FJ",
        "a str-subclass key raises the typed error",
        _raises(screen_outbound_model_request, key_body, _policy())
        == "provider_boundary_invalid_request",
    )
    for bad in (
        _chat_body(tools=[{"type": "function", "name": {"x": 1}}]),
        _chat_body(tools=[{"type": "function", "name": []}]),
        _chat_body(
            messages=[
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": []}}]}
            ]
        ),
        _chat_body(
            messages=[{"role": "user", "content": [{"type": "image_url", "image_url": []}]}]
        ),
    ):
        check(
            "FTF-R004-P2-FJ",
            f"unhashable value {bad!r} is denied, never TypeError, never allowed",
            not _allowed(bad),
        )
    for label, weird in (
        ("a tuple", _chat_body(tools=({"type": "function"},))),
        ("a set value", _chat_body(extra={1, 2})),
        ("bytes", _chat_body(extra=b"x")),
        ("NaN", _chat_body(temperature=float("nan"))),
        ("an int key", {1: "x"}),
        ("a cycle", None),
    ):
        if label == "a cycle":
            cyc: dict[str, Any] = {"model": "m"}
            cyc["self"] = cyc
            weird = cyc
        check(
            "FTF-R004-P2-FJ",
            f"{label} raises the typed error",
            _raises(screen_outbound_model_request, weird, _policy())
            == "provider_boundary_invalid_request",
        )
    deep: Any = "x"
    for _ in range(200):
        deep = [deep]
    check(
        "FTF-R004-P2-FJ",
        "an over-deep body raises the typed error, not RecursionError",
        _raises(screen_outbound_model_request, {"model": "m", "x": deep}, _policy())
        == "provider_boundary_invalid_request",
    )
    # A malformed policy is refused up front, not at screening time.
    for label, build in (
        (
            "subclass profile",
            lambda: ScreeningPolicy(_Str("standard"), "standard", None, frozenset(), None),
        ),
        ("unknown profile", lambda: ScreeningPolicy("loose", "standard", None, frozenset(), None)),
        ("unhashable profile", lambda: ScreeningPolicy([], "standard", None, frozenset(), None)),
        ("non-frozenset tools", lambda: ScreeningPolicy("standard", "standard", None, ["a"], None)),
        (
            "subclass tool name",
            lambda: ScreeningPolicy("standard", "standard", None, frozenset({_Str("a")}), None),
        ),
        (
            "blank tool name",
            lambda: ScreeningPolicy("standard", "standard", None, frozenset({""}), None),
        ),
        (
            "bad approval object",
            lambda: ScreeningPolicy("standard", "standard", None, frozenset(), "yes"),
        ),
    ):
        try:
            build()
            ok = False
        except ProviderBoundaryError:
            ok = True
        except BaseException:  # noqa: BLE001
            ok = False
        check("FTF-R004-P2-FJ", f"policy with {label} raises the typed ProviderBoundaryError", ok)
    for label, build_approval in (
        (
            "empty capabilities",
            lambda: HostedCapabilityApproval(
                frozenset(), Destination("managed_provider", "a"), _approval().authorization
            ),
        ),
        (
            "subclass capability",
            lambda: HostedCapabilityApproval(
                frozenset({_Str("web_search")}),
                Destination("managed_provider", "a"),
                _approval().authorization,
            ),
        ),
        (
            "internal destination",
            lambda: HostedCapabilityApproval(
                frozenset({"web_search"}), Destination("internal"), _approval().authorization
            ),
        ),
        (
            "bad authorization",
            lambda: HostedCapabilityApproval(
                frozenset({"web_search"}), Destination("managed_provider", "a"), "x"
            ),
        ),
    ):
        try:
            build_approval()
            ok = False
        except ProviderBoundaryError:
            ok = True
        except BaseException:  # noqa: BLE001
            ok = False
        check("FTF-R004-P2-FJ", f"approval with {label} raises the typed ProviderBoundaryError", ok)


def test_screening_makes_zero_network_calls() -> None:
    counts = {"connect": 0, "resolve": 0, "create": 0}
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo
    real_create = socket.create_connection

    def counting_connect(self: socket.socket, *args: Any) -> None:
        counts["connect"] += 1
        raise OSError("network disabled in test")

    def counting_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        counts["resolve"] += 1
        raise OSError("network disabled in test")

    def counting_create(*args: Any, **kwargs: Any) -> Any:
        counts["create"] += 1
        raise OSError("network disabled in test")

    socket.socket.connect = counting_connect  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = counting_getaddrinfo
    socket.create_connection = counting_create
    try:
        for profile, url in itertools.product(PROFILES, MEDIA_URLS + (DATA_URI,)):
            screen_outbound_model_request(_image_chat(url), _policy(profile, profile))
        screen_outbound_model_request(
            _chat_body(
                tools=[
                    {"type": "web_search"},
                    {"type": "mcp", "server_url": "https://mcp.example/"},
                ]
            ),
            _policy(approval=_approval()),
        )
        capture_response_attempts(_chat_response([("c1", "lookup_order", '{"a":1}')]))
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.getaddrinfo = real_getaddrinfo
        socket.create_connection = real_create
    check(
        "FTF-R004-P2-FI",
        "zero connects, zero create_connection and zero resolutions during screening and capture",
        counts == {"connect": 0, "resolve": 0, "create": 0},
    )


def test_decision_is_content_free_and_does_not_mutate_the_body() -> None:
    canary = "CANARY-prompt-9f3a"
    body = _chat_body(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": canary},
                    {"type": "image_url", "image_url": {"url": f"https://{canary}.example/a"}},
                ],
            }
        ],
        tools=[
            {"type": "function", "function": {"name": canary}},
            {"type": "mcp", "server_url": f"https://{canary}.example/"},
        ],
    )
    before = copy.deepcopy(body)
    decision = screen_outbound_model_request(body, _policy())
    check("FTF-R004-P2-CF", "screening does not mutate the request body", body == before)
    check(
        "FTF-R004-P2-CF", "the canary never appears in the decision", canary not in repr(decision)
    )
    check(
        "FTF-R004-P2-CF",
        "denials carry only a code and an index path",
        all(
            re.fullmatch(r"[a-z_]+", d.code)
            and re.fullmatch(r"[a-z_]+(\[\d+\]|\.[a-z_]+)*", d.path)
            for d in decision.denials
        ),
    )
    check(
        "FTF-R004-P2-CF",
        "every denial has a stable path",
        [d.path for d in decision.denials] == ["tools[0]", "tools[1]", "messages[0].content[1]"],
    )


# --------------------------------------------------------------------------- capture


def _chat_response(calls: list[tuple[str, str, str]], text: str = "ok") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": cid,
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        }
                        for cid, name, args in calls
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


def _responses_response() -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {"query": "q"},
            },
            {"type": "code_interpreter_call", "id": "ci_1", "code": "print(1)"},
            {
                "type": "mcp_call",
                "id": "mc_1",
                "name": "remote_tool",
                "arguments": '{"a":1}',
                "server_label": "s",
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "delete_account",
                "arguments": '{"id":7}',
            },
            {
                "type": "message",
                "id": "m_1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            },
        ],
    }


def test_capture_records_every_attempt_before_filtering() -> None:
    response = _chat_response(
        [("call_a", "lookup_order", '{"id":1}'), ("call_b", "delete_account", '{"id":7}')]
    )
    evidence = capture_response_attempts(response)
    check("FTF-R004-P2-FF", "evidence is an AttemptEvidence", isinstance(evidence, AttemptEvidence))
    check(
        "FTF-R004-P2-FF",
        "every function call is captured",
        [a.tool_call_id for a in evidence.attempts] == ["call_a", "call_b"],
    )
    check(
        "FTF-R004-P2-FF",
        "names are captured",
        [a.name for a in evidence.attempts] == ["lookup_order", "delete_account"],
    )
    check(
        "FTF-R004-P2-FF", "kinds are function", all(a.kind == "function" for a in evidence.attempts)
    )
    import hashlib

    check(
        "FTF-R004-P2-FF",
        "args are captured as a sha256 of the exact argument text",
        evidence.attempts[0].args_sha256 == hashlib.sha256(b'{"id":1}').hexdigest(),
    )
    check(
        "FTF-R004-P2-FF",
        "the response sha256 is bound to every attempt",
        all(a.response_sha256 == evidence.response_sha256 for a in evidence.attempts),
    )
    check(
        "FTF-R004-P2-FF",
        "the response sha256 is 64 hex chars",
        re.fullmatch(r"[0-9a-f]{64}", evidence.response_sha256) is not None,
    )

    hosted = capture_response_attempts(_responses_response())
    names = [(a.tool_call_id, a.name, a.kind) for a in hosted.attempts]
    check(
        "FTF-R004-P2-FF",
        "responses output: hosted and function calls are all captured, messages and reasoning are not",
        names
        == [
            ("ws_1", "web_search_call", "hosted"),
            ("ci_1", "code_interpreter_call", "hosted"),
            ("mc_1", "remote_tool", "hosted"),
            ("call_1", "delete_account", "function"),
        ],
    )
    check(
        "FTF-R004-P2-FF",
        "a response with no calls has empty evidence",
        capture_response_attempts(
            {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        ).attempts
        == (),
    )
    emitted = hosted.to_emitted_tool_calls()
    check(
        "FTF-R004-P2-FF",
        "evidence converts to FTF-0606 EmittedToolCall records",
        all(type(e) is EmittedToolCall for e in emitted)
        and [e.name for e in emitted]
        == ["web_search_call", "code_interpreter_call", "remote_tool", "delete_account"],
    )
    # Same body -> same hash; any change -> different hash.
    check(
        "FTF-R004-P2-FF",
        "capture is deterministic",
        capture_response_attempts(_responses_response()) == hosted,
    )
    changed = _responses_response()
    changed["output"][1]["status"] = "failed"
    check(
        "FTF-R004-P2-FF",
        "a changed body changes the response sha256",
        capture_response_attempts(changed).response_sha256 != hosted.response_sha256,
    )
    # Legacy single function_call and the arguments-as-object form.
    legacy = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "function_call": {"name": "x", "arguments": "{}"},
                }
            }
        ]
    }
    check(
        "FTF-R004-P2-FF",
        "a legacy function_call without an id raises, never loses the attempt",
        _raises(capture_response_attempts, legacy) == "provider_boundary_invalid_response",
    )
    dup = _chat_response([("same", "a", "{}"), ("same", "b", "{}")])
    check(
        "FTF-R004-P2-FF",
        "duplicate call ids raise rather than collapse",
        _raises(capture_response_attempts, dup) == "provider_boundary_invalid_response",
    )
    unknown_output = {"output": [{"type": "hologram_thing", "id": "h1"}]}
    check(
        "FTF-R004-P2-FF",
        "an unknown output item type raises, never silently dropped",
        _raises(capture_response_attempts, unknown_output) == "provider_boundary_invalid_response",
    )
    no_name = {
        "choices": [
            {
                "message": {
                    "tool_calls": [{"id": "c", "type": "function", "function": {"arguments": "{}"}}]
                }
            }
        ]
    }
    check(
        "FTF-R004-P2-FF",
        "a function call without a name raises",
        _raises(capture_response_attempts, no_name) == "provider_boundary_invalid_response",
    )
    sub = _chat_response([("c1", _Str("x"), "{}")])
    check(
        "FTF-R004-P2-FF",
        "a str-subclass name raises the typed error",
        _raises(capture_response_attempts, sub)
        in ("provider_boundary_invalid_request", "provider_boundary_invalid_response"),
    )
    for bad in ([], "x", None, 7):
        check(
            "FTF-R004-P2-FF",
            f"a non-object response {bad!r} raises the typed error",
            _raises(capture_response_attempts, bad) == "provider_boundary_invalid_request",
        )


def test_filter_refuses_without_matching_evidence() -> None:
    response = _chat_response(
        [("call_a", "lookup_order", "{}"), ("call_b", "delete_account", "{}")]
    )
    evidence = capture_response_attempts(response)
    approved = frozenset({"lookup_order"})
    outcome = apply_approval_filter(response, evidence, approved_tool_names=approved)
    kept = [c["id"] for c in outcome.body["choices"][0]["message"]["tool_calls"]]
    check(
        "FTF-R004-P2-FG",
        "control: with matching evidence the unapproved call is removed",
        kept == ["call_a"],
    )
    check(
        "FTF-R004-P2-FG",
        "the removed call ids are reported",
        outcome.removed_call_ids == ("call_b",),
    )
    check(
        "FTF-R004-P2-FG",
        "the input response is not mutated",
        len(response["choices"][0]["message"]["tool_calls"]) == 2,
    )
    other = capture_response_attempts(_chat_response([("x", "lookup_order", "{}")]))
    check(
        "FTF-R004-P2-FG",
        "evidence from another body is refused",
        _raises(apply_approval_filter, response, other, approved_tool_names=approved)
        == "attempt_evidence_mismatch",
    )
    for bad in (None, "evidence", {"response_sha256": evidence.response_sha256}, object()):
        check(
            "FTF-R004-P2-FG",
            f"evidence {bad!r} (not an AttemptEvidence) is refused",
            _raises(apply_approval_filter, response, bad, approved_tool_names=approved)
            in ("attempt_evidence_mismatch", "provider_boundary_invalid_request"),
        )
    forged = AttemptEvidence(response_sha256=evidence.response_sha256, attempts=())
    check(
        "FTF-R004-P2-FG",
        "evidence with the right hash but a different attempt list is refused",
        _raises(apply_approval_filter, response, forged, approved_tool_names=approved)
        == "attempt_evidence_mismatch",
    )
    # Filtering a body that was already filtered (evidence is for the unfiltered body).
    check(
        "FTF-R004-P2-FG",
        "filtering an already-filtered body with the original evidence is refused",
        _raises(apply_approval_filter, outcome.body, evidence, approved_tool_names=approved)
        == "attempt_evidence_mismatch",
    )
    check(
        "FTF-R004-P2-FG",
        "a str-subclass approved name is refused",
        _raises(
            apply_approval_filter, response, evidence, approved_tool_names=frozenset({_Str("a")})
        )
        == "provider_boundary_invalid_request",
    )
    hosted_response = _responses_response()
    hosted_evidence = capture_response_attempts(hosted_response)
    hosted_out = apply_approval_filter(
        hosted_response, hosted_evidence, approved_tool_names=frozenset({"delete_account"})
    )
    check(
        "FTF-R004-P2-FG",
        "hosted call items are removed by the filter, an approved function call is kept",
        [i["type"] for i in hosted_out.body["output"]] == ["reasoning", "function_call", "message"],
    )


def test_denied_attempt_survives_filtering_in_the_evidence() -> None:
    response = _chat_response([("call_b", "delete_account", '{"id":7}')])
    evidence = capture_response_attempts(response)
    outcome = apply_approval_filter(response, evidence, approved_tool_names=frozenset())
    check(
        "FTF-R004-P2-FH",
        "the filtered body no longer holds the call",
        outcome.body["choices"][0]["message"]["tool_calls"] == [],
    )
    check(
        "FTF-R004-P2-FH",
        "the outcome's evidence still names the removed attempt",
        [a.tool_call_id for a in outcome.evidence.attempts] == ["call_b"],
    )
    check(
        "FTF-R004-P2-FH",
        "the outcome's evidence is the pre-filter evidence, unchanged",
        outcome.evidence == evidence,
    )
    check(
        "FTF-R004-P2-FH",
        "capturing the filtered body would have lost the attempt (why order matters)",
        capture_response_attempts(outcome.body).attempts == (),
    )
    # Evidence is immutable.
    try:
        evidence.attempts = ()  # type: ignore[misc]
        mutated = True
    except Exception:  # noqa: BLE001
        mutated = False
    check("FTF-R004-P2-FH", "evidence is frozen", not mutated)


def test_filtered_forbidden_attempt_still_grades_as_a_policy_failure() -> None:
    response = _chat_response([("call_b", "delete_account", '{"id":7}')])
    evidence = capture_response_attempts(response)
    outcome = apply_approval_filter(response, evidence, approved_tool_names=frozenset())
    emitted = list(outcome.evidence.to_emitted_tool_calls())
    graded = grade_forbidden_actions(
        emitted,
        [BackendEffect("e1", "delete_account", "denied", "call_b")],
        forbidden_actions=["delete_account"],
    )
    check(
        "FTF-R004-P2-COMP",
        "the filtered forbidden attempt is a policy failure",
        graded.policy_failure is True,
    )
    check("FTF-R004-P2-COMP", "the run does not pass", graded.passed is False)
    check(
        "FTF-R004-P2-COMP",
        "the attempt is reported as denied, not executed",
        graded.denied_attempts == ("call_b",) and graded.effect_violation is False,
    )
    lost = grade_forbidden_actions(
        list(capture_response_attempts(outcome.body).to_emitted_tool_calls()),
        [],
        forbidden_actions=["delete_account"],
    )
    check(
        "FTF-R004-P2-COMP",
        "control: capturing after filtering would have PASSED the run (the evidence loss this module prevents)",
        lost.passed is True,
    )


# --------------------------------------------------------------------------- mutations

_PROBE = r"""
import json, sys, socket
sys.path.insert(0, {root!r}); sys.path.insert(0, {api!r})
from packages.security.security_profile_enforcement import Destination, RouteAuthorization
from workers.evaluation.adapters.inspect_adapter.provider_boundary import *
class S(str):
    def __eq__(self, o): return True
    __hash__ = str.__hash__
def pol(org="standard", project="standard", job=None, tools=("lookup_order",), approval=None):
    return ScreeningPolicy(org, project, job, frozenset(tools), approval)
def appr(caps=("web_search_preview",)):
    return HostedCapabilityApproval(frozenset(caps), Destination("managed_provider", "acme"),
        RouteAuthorization("acme", frozenset({{"model"}}), "a", "x", "eu", "30d"))
def body(tools=None, content=None, **extra):
    b = {{"model": "m", "messages": [{{"role": "user", "content": content if content is not None else "hi"}}],
          "tools": tools if tools is not None else []}}
    b.update(extra); return b
def fn(name):
    return {{"type": "function", "function": {{"name": name}}}}
def img(url):
    return [{{"type": "image_url", "image_url": {{"url": url}}}}]
def run(b, p=None):
    try:
        d = screen_outbound_model_request(b, p or pol())
        return [d.allowed] + [x.code for x in d.denials]
    except ProviderBoundaryError as e: return "E:" + e.code
    except BaseException as e: return "CRASH:" + type(e).__name__
def runc(f, *a, **k):
    try:
        r = f(*a, **k); return "OK"
    except ProviderBoundaryError as e: return "E:" + e.code
    except BaseException as e: return "CRASH:" + type(e).__name__
out = {{}}
out["ok_fn"] = run(body([fn("lookup_order")]))
for t in ("web_search", "web_search_preview", "code_interpreter", "file_search", "computer_use_preview",
          "image_generation", "code_execution_20250522", "local_shell"):
    out["hosted_" + t] = run(body([{{"type": t}}]))
out["mcp"] = run(body([{{"type": "mcp"}}]))
out["wso"] = run(body(web_search_options={{"a": 1}}))
out["plugins"] = run(body(plugins=[{{"id": "w"}}]))
out["wso_null"] = run(body(web_search_options=None))
for i, u in enumerate(("http://x.test/a", "https://x.test/a", "file:///etc/passwd", "ftp://x/a",
                       "https://x.test/?u=data:text/plain,a", "DATA:text/plain,a", " data:text/plain,a",
                       "data", "")):
    out["url%d" % i] = run(body(content=img(u)))
out["data_ok"] = run(body(content=img("data:image/png;base64,AAAA")))
out["data_http"] = run(body(content=img("data:text/plain,see%20http://x.test/")))
out["file_id"] = run(body(content=[{{"type": "file", "file": {{"file_id": "f"}}}}]))
out["file_data_ok"] = run(body(content=[{{"type": "file", "file": {{"file_data": "data:a/b;base64,AA"}}}}]))
out["audio_ok"] = run(body(content=[{{"type": "input_audio", "input_audio": {{"data": "AAAA", "format": "wav"}}}}]))
out["audio_url"] = run(body(content=[{{"type": "input_audio", "input_audio": {{"data": "https://x/a", "format": "wav"}}}}]))
out["in_file_url"] = run({{"model": "m", "input": [{{"type": "message", "role": "user", "content": [{{"type": "input_file", "file_url": "https://x/a"}}]}}]}})
out["in_img_id"] = run({{"model": "m", "input": [{{"type": "message", "role": "user", "content": [{{"type": "input_image", "file_id": "f"}}]}}]}})
out["fn_bad"] = run(body([fn("evil")]))
out["fn_case"] = run(body([fn("Lookup_Order")]))
out["fn_resp_bad"] = run({{"model": "m", "input": "x", "tools": [{{"type": "function", "name": "evil"}}]}})
out["fn_legacy_bad"] = run(body(functions=[{{"name": "evil"}}]))
out["fn_empty_set"] = run(body([fn("lookup_order")]), pol(tools=()))
ba = body([{{"type": "web_search_preview"}}])
out["appr_std"] = run(ba, pol(approval=appr()))
out["appr_other_cap"] = run(body([{{"type": "web_search"}}]), pol(approval=appr()))
for prof in ("zero_egress", "air_gapped"):
    out["appr_" + prof + "_org"] = run(ba, pol(org=prof, approval=appr()))
    out["appr_" + prof + "_project"] = run(ba, pol(project=prof, approval=appr()))
    out["appr_" + prof + "_job"] = run(ba, pol(job=prof, approval=appr()))
out["profile_name"] = screen_outbound_model_request(ba, pol("air_gapped", approval=appr())).effective_profile
out["unk_tool_type_list"] = run(body([{{"type": []}}]))
out["unk_tool_type_none"] = run(body([{{"name": "x"}}]))
out["unk_part"] = run(body(content=[{{"type": "hologram"}}]))
out["unk_input"] = run({{"model": "m", "input": [{{"type": "hologram"}}]}})
out["unk_tools_type"] = run(body(tools={{"type": "function"}}))
out["unh_name"] = run(body([{{"type": "function", "name": []}}]))
out["unh_url"] = run(body(content=[{{"type": "image_url", "image_url": {{"url": []}}}}]))
out["sub_type"] = run(body([{{"type": S("function"), "function": {{"name": "lookup_order"}}}}]))
out["sub_name"] = run(body([{{"type": "function", "function": {{"name": S("evil")}}}}]))
out["sub_url"] = run(body(content=img(S("https://x.test/a"))))
out["nonobj"] = run([])
cyc = {{"model": "m"}}; cyc["s"] = cyc
out["cycle"] = run(cyc)
deep = "x"
for _ in range(200): deep = [deep]
out["deep"] = run({{"model": "m", "x": deep}})
out["nan"] = run(body(temperature=float("nan")))
out["tuple"] = run(body(tools=({{"type": "function"}},)))
out["subclass_profile"] = runc(ScreeningPolicy, S("standard"), "standard", None, frozenset(), None)
out["subclass_tool"] = runc(ScreeningPolicy, "standard", "standard", None, frozenset({{S("a")}}), None)
out["bad_profile"] = runc(ScreeningPolicy, "loose", "standard", None, frozenset(), None)
out["paths"] = [d.path for d in screen_outbound_model_request(
    body([{{"type": "mcp"}}, fn("evil")], content=img("http://x/a")), pol()).denials]
# capture / filter
def chat(calls):
    return {{"choices": [{{"message": {{"role": "assistant", "content": "c", "tool_calls": [
        {{"id": i, "type": "function", "function": {{"name": n, "arguments": a}}}} for i, n, a in calls]}}}}]}}
r1 = chat([("a", "lookup_order", "{{}}"), ("b", "delete_account", "{{\"id\":1}}")])
ev = capture_response_attempts(r1)
out["cap_ids"] = [a.tool_call_id for a in ev.attempts]
out["cap_names"] = [a.name for a in ev.attempts]
out["cap_args"] = [a.args_sha256 for a in ev.attempts]
out["cap_resp_bound"] = all(a.response_sha256 == ev.response_sha256 for a in ev.attempts)
out["cap_resp"] = ev.response_sha256
resp = {{"output": [{{"type": "reasoning", "id": "r"}}, {{"type": "web_search_call", "id": "w"}},
        {{"type": "mcp_call", "id": "m", "name": "rt", "arguments": "{{}}"}},
        {{"type": "function_call", "call_id": "c1", "name": "delete_account", "arguments": "{{}}"}},
        {{"type": "message", "role": "assistant", "content": []}}]}}
hev = capture_response_attempts(resp)
out["cap_hosted"] = [(a.tool_call_id, a.name, a.kind) for a in hev.attempts]
out["cap_empty"] = len(capture_response_attempts({{"choices": [{{"message": {{"content": "x"}}}}]}}).attempts)
out["cap_legacy"] = runc(capture_response_attempts, {{"choices": [{{"message": {{"function_call": {{"name": "x", "arguments": "{{}}"}}}}}}]}})
out["cap_dup"] = runc(capture_response_attempts, chat([("s", "a", "{{}}"), ("s", "b", "{{}}")]))
out["cap_unknown_item"] = runc(capture_response_attempts, {{"output": [{{"type": "hologram", "id": "h"}}]}})
out["cap_noname"] = runc(capture_response_attempts, {{"choices": [{{"message": {{"tool_calls": [{{"id": "c", "type": "function", "function": {{"arguments": "{{}}"}}}}]}}}}]}})
out["cap_nonobj"] = runc(capture_response_attempts, [])
out["emitted"] = [(e.tool_call_id, e.name) for e in hev.to_emitted_tool_calls()]
o = apply_approval_filter(r1, ev, approved_tool_names=frozenset({{"lookup_order"}}))
out["flt_kept"] = [c["id"] for c in o.body["choices"][0]["message"]["tool_calls"]]
out["flt_removed"] = list(o.removed_call_ids)
out["flt_evidence_kept"] = [a.tool_call_id for a in o.evidence.attempts]
out["flt_input_untouched"] = len(r1["choices"][0]["message"]["tool_calls"])
other = capture_response_attempts(chat([("z", "lookup_order", "{{}}")]))
out["flt_other"] = runc(apply_approval_filter, r1, other, approved_tool_names=frozenset())
out["flt_none"] = runc(apply_approval_filter, r1, None, approved_tool_names=frozenset())
out["flt_forged"] = runc(apply_approval_filter, r1, AttemptEvidence(ev.response_sha256, ()), approved_tool_names=frozenset())
out["flt_twice"] = runc(apply_approval_filter, o.body, ev, approved_tool_names=frozenset())
out["flt_sub"] = runc(apply_approval_filter, r1, ev, approved_tool_names=frozenset({{S("a")}}))
ho = apply_approval_filter(resp, hev, approved_tool_names=frozenset({{"delete_account"}}))
out["flt_hosted_types"] = [i["type"] for i in ho.body["output"]]
# network
cnt = {{"c": 0}}
real = socket.socket.connect
def cc(self, *a):
    cnt["c"] += 1; raise OSError("x")
socket.socket.connect = cc
run(body(content=img("https://x.test/a")))
capture_response_attempts(r1)
socket.socket.connect = real
out["net"] = cnt["c"]
print(json.dumps(out, sort_keys=True, default=str))
"""


def _run_probe(source: str | None) -> dict[str, Any]:
    original = MODULE.read_bytes()
    cache_dir = tempfile.mkdtemp(prefix="ftf-r004-p2-pyc-")
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "pycache_prefix=" + cache_dir,
                "-c",
                _PROBE.format(root=str(ROOT), api=str(ROOT / "apps" / "api")),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
        shutil.rmtree(cache_dir, ignore_errors=True)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    if result.returncode != 0:
        return {"ERROR": result.stderr[-400:]}
    return cast(dict[str, Any], json.loads(result.stdout))


HOSTED_KEYS = tuple(
    "hosted_" + t
    for t in (
        "web_search",
        "web_search_preview",
        "code_interpreter",
        "file_search",
        "computer_use_preview",
        "image_generation",
        "code_execution_20250522",
        "local_shell",
    )
)
URL_KEYS = tuple(f"url{i}" for i in range(9))
APPROVED_HOSTED_BLOCKED_KEYS = tuple(
    f"appr_{p}_{w}" for p in ("zero_egress", "air_gapped") for w in ("org", "project", "job")
)

# (label, regex, replacement, probe keys that must change)
_MUTATIONS: list[tuple[str, str, str, tuple[str, ...]]] = []


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    baseline = _run_probe(None)
    check(CID, "the behavioural probe runs on the unmutated module", "ERROR" not in baseline)
    if "ERROR" in baseline:
        FAILURES.append(f"probe error: {baseline['ERROR']}")
    else:
        source = MODULE.read_text(encoding="utf-8")
        for label, pattern, replacement, keys in _MUTATIONS:
            mutated, count = re.subn(
                pattern, lambda _m, r=replacement: r, source, count=1, flags=re.S
            )
            check(CID, f"mutation {label!r} applies exactly once", count == 1)
            if count != 1:
                continue
            result = _run_probe(mutated)
            if "ERROR" in result:
                continue  # a crash at import is also a RED
            changed = [k for k in keys if result.get(k) != baseline.get(k)]
            check(
                "FTF-R004-P2-MUT",
                f"mutation {label!r} turns {keys} RED (changed: {changed})",
                bool(changed),
            )
    if FAILURES:
        print(f"FTF-R004-P2 provider boundary: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print(f"FTF-R004-P2 provider boundary: all {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
