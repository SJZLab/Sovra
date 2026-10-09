"""FTF-R004-P2 -- Inspect bridge provider-side execution boundary + pre-filter attempt evidence.

Section 78.2 ("Provider-side execution is a separate boundary"), 66.2, 66.4,
69.2 and 30 ("attempted prohibited calls reported separately"): an Inspect
bridge sits between the Factory and the target runtime's model provider. A
request that carries a provider-hosted tool (web search, code interpreter,
MCP, ...), a remote media reference or a function tool the manifest never
approved lets the *provider* act outside the privacy boundary; a response that
is approval-filtered before its forbidden tool calls are recorded loses the
evidence that the model tried.

Acceptance ("Block provider-side hosted tools, remote media dereferences and
unapproved bridged host tools outside the privacy boundary; capture attempted
forbidden tool calls before approval filtering can replace model output."):

* `screen_outbound_model_request` is default-deny. The only tools that pass are
  `type: "function"` tools whose name is in the approved manifest tool set;
  hosted tool types, `mcp` entries, `web_search_options` and plugin keys are
  denied (`hosted_tool_denied`, `mcp_denied`); an unapproved function tool is
  `unapproved_bridged_tool`; an `image_url`/`input_image`/`file`/`input_file`/
  `input_audio` part that is not an inline `data:` payload is
  `remote_media_dereference`; any shape this module does not recognise is
  `unknown_request_shape`. Every denial is reported, not only the first.
* A hosted capability is allowed only under the `standard` profile with an
  explicit FTF-0901 `RouteAuthorization` that covers the model surface and
  names the exact capability. The profile comes from FTF-0901
  `effective_profile`/`enforce_effect` (most restrictive of organization,
  project and job; ordering is not re-implemented here). `zero_egress` and
  `air_gapped` deny every hosted capability even when it is "approved".
* `capture_response_attempts` records every function call and every hosted
  call item of a response as an `AttemptEvidence` bound to the sha256 of that
  exact response body, exposed as FTF-0606 `EmittedToolCall` records for FTF-0609
  `grade_forbidden_actions`. `apply_approval_filter` refuses to run unless the
  evidence equals a fresh capture of the exact body it is given, so filtering
  can never come before capture and a filtered body can never be passed off as
  the original. The returned `FilterOutcome` carries the pre-filter evidence.

Screening performs no network call, no DNS resolution and no filesystem read.

A media reference is inline only when the raw value starts with the literal,
lower-case `data:` -- never decided by searching for the substring anywhere in the
value (`https://x/?u=data:...` is remote) and never by a URL parse that would
normalise `" data:"` or `"DATA:"` into an inline value. Every other string, whatever
its scheme (http, https, file, ftp, ...), is a remote dereference.

Decisions are content-free: a denial holds a stable code and an index path
(`tools[2]`), never a tool name, URL or prompt text.

Input is validated strictly before it is read: the body must be plain JSON
(`dict`/`list`/`str`/`int`/`float`/`bool`/`None`, string keys, finite numbers,
bounded depth and size). A `str` subclass (whose `==`/`in` can lie), a tuple, a
cycle or an over-deep body raises `ProviderBoundaryError`, never `TypeError`,
and never resolves to an allow. Unhashable values at a semantic position are
checked by exact type before any set membership.

This module is a pure library with no live caller yet: the runtime bridge that
screens each request and captures each response is FTF-R004-P3. That gap is
disclosed in docs/spec-matrix.yaml (blank `proving_symbol`). Real upstream-route
enforcement at the organization LiteLLM gateway is BLOCKED_EXTERNAL (it needs the
same gateway key as the 2026-10-08 FTF-0616 owner action).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from packages.security.security_profile_enforcement import (
    Destination,
    EffectRequest,
    RouteAuthorization,
    SecurityProfileEnforcementError,
    effective_profile,
    enforce_effect,
)

from workers.evaluation.tool_name_grader import EmittedToolCall

__all__ = [
    "CODE_HOSTED_TOOL_DENIED",
    "CODE_MCP_DENIED",
    "CODE_REMOTE_MEDIA_DEREFERENCE",
    "CODE_UNAPPROVED_BRIDGED_TOOL",
    "CODE_UNKNOWN_REQUEST_SHAPE",
    "ProviderBoundaryError",
    "HostedCapabilityApproval",
    "ScreeningPolicy",
    "Denial",
    "ScreeningDecision",
    "AttemptRecord",
    "AttemptEvidence",
    "FilterOutcome",
    "screen_outbound_model_request",
    "capture_response_attempts",
    "apply_approval_filter",
]

CODE_HOSTED_TOOL_DENIED = "hosted_tool_denied"
CODE_MCP_DENIED = "mcp_denied"
CODE_UNAPPROVED_BRIDGED_TOOL = "unapproved_bridged_tool"
CODE_REMOTE_MEDIA_DEREFERENCE = "remote_media_dereference"
CODE_UNKNOWN_REQUEST_SHAPE = "unknown_request_shape"

_INVALID_REQUEST = "provider_boundary_invalid_request"
_INVALID_RESPONSE = "provider_boundary_invalid_response"
_EVIDENCE_MISMATCH = "attempt_evidence_mismatch"

_MAX_DEPTH = 64
_MAX_NODES = 200_000
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_HOSTED_TYPE_RE = re.compile(
    r"(web_search\w*|computer_use\w*|\w*code_execution\w*|code_interpreter"
    r"|file_search|image_generation|local_shell)",
    re.ASCII,
)
_ATTEMPT_KINDS = ("function", "hosted")
_CALL_ITEM_RE = re.compile(r"[a-z0-9_]+_call", re.ASCII)
_BASE64_RE = re.compile(r"[A-Za-z0-9+/_=-]*", re.ASCII)
_EXTERNAL_KIND = {"internal": False, "managed_provider": True, "external": True}
_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text", "refusal"})
_NON_CALL_OUTPUT_TYPES = frozenset({"message", "reasoning"})
_KNOWN_INPUT_ITEM_TYPES = frozenset({"function_call", "function_call_output", "reasoning"})
_CAPABILITY_KEYS: tuple[tuple[str, str], ...] = (
    ("web_search_options", "web_search_options"),
    ("plugins", "plugins"),
    ("plugin", "plugins"),
)


class ProviderBoundaryError(Exception):
    """Malformed input or a refused operation; `code` is stable for callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: object, what: str) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ProviderBoundaryError(_INVALID_REQUEST, f"{what} must be a non-empty string")
    return value


def _name_set(value: object, what: str) -> frozenset[str]:
    if type(value) is not frozenset:
        raise ProviderBoundaryError(_INVALID_REQUEST, f"{what} must be a frozenset of strings")
    for item in value:
        _text(item, f"a member of {what}")
    return value


@dataclass(frozen=True)
class HostedCapabilityApproval:
    """An explicit approval for named hosted capabilities at one managed destination."""

    capabilities: frozenset[str]
    destination: Destination
    authorization: RouteAuthorization

    def __post_init__(self) -> None:
        if not _name_set(self.capabilities, "capabilities"):
            raise ProviderBoundaryError(_INVALID_REQUEST, "capabilities must not be empty")
        if type(self.destination) is not Destination or not _EXTERNAL_KIND[self.destination.kind]:
            raise ProviderBoundaryError(
                _INVALID_REQUEST, "a hosted capability needs a non-internal Destination"
            )
        if type(self.authorization) is not RouteAuthorization:
            raise ProviderBoundaryError(
                _INVALID_REQUEST, "authorization must be a RouteAuthorization"
            )


@dataclass(frozen=True)
class ScreeningPolicy:
    organization_profile: str
    project_profile: str
    job_profile: str | None
    approved_tool_names: frozenset[str]
    hosted_approval: HostedCapabilityApproval | None = None

    def __post_init__(self) -> None:
        for what, value in (
            ("organization profile", self.organization_profile),
            ("project profile", self.project_profile),
        ):
            _text(value, what)
        if self.job_profile is not None:
            _text(self.job_profile, "job profile")
        try:
            effective_profile(self.organization_profile, self.project_profile, self.job_profile)
        except SecurityProfileEnforcementError as exc:
            raise ProviderBoundaryError(_INVALID_REQUEST, str(exc)) from exc
        _name_set(self.approved_tool_names, "approved_tool_names")
        if self.hosted_approval is not None and type(self.hosted_approval) is not (
            HostedCapabilityApproval
        ):
            raise ProviderBoundaryError(
                _INVALID_REQUEST, "hosted_approval must be a HostedCapabilityApproval"
            )


@dataclass(frozen=True)
class Denial:
    """Content-free: a stable code and an index path such as `tools[2]`."""

    code: str
    path: str


@dataclass(frozen=True)
class ScreeningDecision:
    allowed: bool
    effective_profile: str
    denials: tuple[Denial, ...]


def _check_json(root: object, what: str, code: str) -> None:
    """Require plain JSON: exact types, string keys, finite numbers, bounded size."""
    stack: list[tuple[object, int]] = [(root, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > _MAX_DEPTH or nodes > _MAX_NODES:
            raise ProviderBoundaryError(code, f"{what} is too deep or too large")
        kind = type(value)
        if kind is dict:
            for key, item in value.items():  # type: ignore[attr-defined]
                if type(key) is not str:
                    raise ProviderBoundaryError(code, f"{what} has a non-string key")
                stack.append((item, depth + 1))
        elif kind is list:
            stack.extend((item, depth + 1) for item in value)  # type: ignore[attr-defined]
        elif kind is float:
            if value != value or value in (float("inf"), float("-inf")):
                raise ProviderBoundaryError(code, f"{what} holds a non-finite number")
        elif kind not in (str, int, bool, type(None)):
            raise ProviderBoundaryError(code, f"{what} holds a {kind.__name__}, not plain JSON")


def _json_object(value: object, what: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProviderBoundaryError(_INVALID_REQUEST, f"{what} must be a JSON object")
    _check_json(value, what, _INVALID_REQUEST)
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hex64(value: object, what: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise ProviderBoundaryError(
            _INVALID_RESPONSE, f"{what} must be 64 lowercase hex characters"
        )
    return value


# --------------------------------------------------------------------------- screening


def _hosted_allowed(capability: str, policy: ScreeningPolicy) -> bool:
    approval = policy.hosted_approval
    if approval is None or capability not in approval.capabilities:
        return False
    try:
        enforce_effect(
            EffectRequest(
                "model",
                approval.destination,
                policy.organization_profile,
                policy.project_profile,
                policy.job_profile,
            ),
            approval.authorization,
        )
    except SecurityProfileEnforcementError:
        return False
    return True


def _is_inline_data_uri(value: str) -> bool:
    """Exact, case-sensitive `data:` prefix of the raw value -- stricter than any URL parse.

    `urlsplit` strips leading whitespace and lower-cases the scheme, so `" data:..."`
    and `"DATA:..."` would classify as inline; a raw-prefix test refuses both.
    """
    return value.startswith("data:")


def _screen_function(name: object, path: str, policy: ScreeningPolicy, out: list[Denial]) -> None:
    if type(name) is not str:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
    elif name not in policy.approved_tool_names:
        out.append(Denial(CODE_UNAPPROVED_BRIDGED_TOOL, path))


def _screen_tool(tool: object, path: str, policy: ScreeningPolicy, out: list[Denial]) -> None:
    if type(tool) is not dict:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
        return
    tool_type = tool.get("type")
    if type(tool_type) is not str:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
    elif tool_type == "function":
        nested, direct = tool.get("function"), tool.get("name")
        if nested is None:
            _screen_function(direct, path, policy, out)
        elif direct is not None or type(nested) is not dict:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
        else:
            _screen_function(nested.get("name"), path, policy, out)
    elif tool_type == "mcp":
        if not _hosted_allowed(tool_type, policy):
            out.append(Denial(CODE_MCP_DENIED, path))
    elif _HOSTED_TYPE_RE.fullmatch(tool_type):
        if not _hosted_allowed(tool_type, policy):
            out.append(Denial(CODE_HOSTED_TOOL_DENIED, path))
    else:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))


def _screen_tools(body: dict[str, Any], policy: ScreeningPolicy, out: list[Denial]) -> None:
    tools = body.get("tools")
    if tools is not None:
        if type(tools) is not list:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, "tools"))
        else:
            for index, tool in enumerate(tools):
                _screen_tool(tool, f"tools[{index}]", policy, out)
    functions = body.get("functions")
    if functions is not None:
        if type(functions) is not list:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, "functions"))
        else:
            for index, function in enumerate(functions):
                path = f"functions[{index}]"
                if type(function) is not dict:
                    out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
                else:
                    _screen_function(function.get("name"), path, policy, out)


def _screen_media(value: object, path: str, out: list[Denial]) -> None:
    if type(value) is not str:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
    elif not _is_inline_data_uri(value):
        out.append(Denial(CODE_REMOTE_MEDIA_DEREFERENCE, path))


def _screen_references(
    part: dict[str, Any],
    path: str,
    out: list[Denial],
    *,
    inline: tuple[str, ...],
    remote: tuple[str, ...],
) -> None:
    """`inline` keys must hold a data: URI; a present `remote` key is a provider-side fetch."""
    found = False
    for key in inline:
        if part.get(key) is not None:
            found = True
            _screen_media(part[key], path, out)
    for key in remote:
        if part.get(key) is not None:
            found = True
            out.append(Denial(CODE_REMOTE_MEDIA_DEREFERENCE, path))
    if not found:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))


def _screen_part(part: object, path: str, out: list[Denial]) -> None:
    if type(part) is not dict:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
        return
    part_type = part.get("type")
    if type(part_type) is not str:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
    elif part_type in _TEXT_PART_TYPES:
        return
    elif part_type == "image_url":
        reference = part.get("image_url")
        if type(reference) is dict:
            reference = reference.get("url")
        _screen_media(reference, path, out)
    elif part_type == "input_image":
        _screen_references(part, path, out, inline=("image_url",), remote=("file_id",))
    elif part_type == "input_file":
        _screen_references(part, path, out, inline=("file_data",), remote=("file_id", "file_url"))
    elif part_type == "file":
        nested = part.get("file")
        if type(nested) is dict:
            _screen_references(nested, path, out, inline=("file_data",), remote=("file_id",))
        else:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
    elif part_type == "input_audio":
        audio = part.get("input_audio")
        data = audio.get("data") if type(audio) is dict else None
        if type(audio) is not dict or type(data) is not str or set(audio) - {"data", "format"}:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
        elif not _BASE64_RE.fullmatch(data):
            out.append(Denial(CODE_REMOTE_MEDIA_DEREFERENCE, path))
    else:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))


def _screen_content(content: object, path: str, out: list[Denial]) -> None:
    if content is None or type(content) is str:
        return
    if type(content) is not list:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
        return
    for index, part in enumerate(content):
        _screen_part(part, f"{path}[{index}]", out)


def _screen_messages(body: dict[str, Any], out: list[Denial]) -> None:
    messages = body.get("messages")
    if messages is None:
        return
    if type(messages) is not list:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, "messages"))
        return
    for index, message in enumerate(messages):
        path = f"messages[{index}]"
        if type(message) is not dict:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
        else:
            _screen_content(message.get("content"), f"{path}.content", out)


def _screen_input(body: dict[str, Any], out: list[Denial]) -> None:
    items = body.get("input")
    if items is None or type(items) is str:
        return
    if type(items) is not list:
        out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, "input"))
        return
    for index, item in enumerate(items):
        path = f"input[{index}]"
        if type(item) is not dict:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))
            continue
        item_type = item.get("type")
        if item_type is None and "role" in item and "content" in item:
            item_type = "message"
        if item_type == "message":
            _screen_content(item.get("content"), f"{path}.content", out)
        elif type(item_type) is not str or item_type not in _KNOWN_INPUT_ITEM_TYPES:
            out.append(Denial(CODE_UNKNOWN_REQUEST_SHAPE, path))


def screen_outbound_model_request(body: object, policy: ScreeningPolicy) -> ScreeningDecision:
    """Default-deny screening of one outbound OpenAI-compatible chat/responses body."""
    if type(policy) is not ScreeningPolicy:
        raise ProviderBoundaryError(_INVALID_REQUEST, "policy must be a ScreeningPolicy")
    request = _json_object(body, "request body")
    denials: list[Denial] = []
    _screen_tools(request, policy, denials)
    for key, capability in _CAPABILITY_KEYS:
        if request.get(key) is not None and not _hosted_allowed(capability, policy):
            denials.append(Denial(CODE_HOSTED_TOOL_DENIED, key))
    _screen_messages(request, denials)
    _screen_input(request, denials)
    return ScreeningDecision(
        allowed=not denials,
        effective_profile=effective_profile(
            policy.organization_profile, policy.project_profile, policy.job_profile
        ),
        denials=tuple(denials),
    )


# --------------------------------------------------------------------------- evidence


@dataclass(frozen=True)
class AttemptRecord:
    """One attempted call: identity, name, kind and content hashes -- never arguments."""

    tool_call_id: str
    name: str
    kind: str
    args_sha256: str
    response_sha256: str

    def __post_init__(self) -> None:
        _response_text(self.tool_call_id, "tool_call_id")
        _response_text(self.name, "name")
        if type(self.kind) is not str or self.kind not in _ATTEMPT_KINDS:
            raise ProviderBoundaryError(_INVALID_RESPONSE, "kind must be 'function' or 'hosted'")
        _hex64(self.args_sha256, "args_sha256")
        _hex64(self.response_sha256, "response_sha256")


@dataclass(frozen=True)
class AttemptEvidence:
    response_sha256: str
    attempts: tuple[AttemptRecord, ...]

    def __post_init__(self) -> None:
        _hex64(self.response_sha256, "response_sha256")
        if type(self.attempts) is not tuple:
            raise ProviderBoundaryError(_INVALID_RESPONSE, "attempts must be a tuple")
        for attempt in self.attempts:
            if type(attempt) is not AttemptRecord:
                raise ProviderBoundaryError(
                    _INVALID_RESPONSE, "every attempt must be an AttemptRecord"
                )
        if {a.response_sha256 for a in self.attempts} - {self.response_sha256}:
            raise ProviderBoundaryError(
                _INVALID_RESPONSE, "every attempt must be bound to this response"
            )

    def to_emitted_tool_calls(self) -> tuple[EmittedToolCall, ...]:
        """The FTF-0606 records FTF-0609 `grade_forbidden_actions` consumes."""
        return tuple(EmittedToolCall(a.tool_call_id, a.name) for a in self.attempts)


@dataclass(frozen=True)
class FilterOutcome:
    body: dict[str, Any]
    removed_call_ids: tuple[str, ...]
    evidence: AttemptEvidence


@dataclass(frozen=True)
class _Found:
    tool_call_id: str
    name: str
    hosted: bool
    args_sha256: str
    chat: bool
    path: tuple[int, ...]


def _response_text(value: object, what: str) -> str:
    if type(value) is not str or not value:
        raise ProviderBoundaryError(_INVALID_RESPONSE, f"{what} must be a non-empty string")
    return value


def _args_hash(arguments: object) -> str:
    if arguments is None:
        return _sha256(b"")
    if type(arguments) is str:
        return _sha256(arguments.encode())
    return _sha256(_canonical(arguments))


def _collect_chat(body: dict[str, Any], found: list[_Found]) -> None:
    choices = body.get("choices")
    if choices is None:
        return
    if type(choices) is not list:
        raise ProviderBoundaryError(_INVALID_RESPONSE, "choices must be a list")
    for choice_index, choice in enumerate(choices):
        message = choice.get("message") if type(choice) is dict else None
        if type(message) is not dict:
            raise ProviderBoundaryError(_INVALID_RESPONSE, "every choice needs a message object")
        if message.get("function_call") is not None:
            raise ProviderBoundaryError(
                _INVALID_RESPONSE, "a legacy function_call has no call id and cannot be recorded"
            )
        calls = message.get("tool_calls")
        if calls is None:
            continue
        if type(calls) is not list:
            raise ProviderBoundaryError(_INVALID_RESPONSE, "tool_calls must be a list")
        for call_index, call in enumerate(calls):
            function = call.get("function") if type(call) is dict else None
            if (
                type(call) is not dict
                or call.get("type") != "function"
                or type(function) is not dict
            ):
                raise ProviderBoundaryError(_INVALID_RESPONSE, "unrecognised tool call shape")
            found.append(
                _Found(
                    _response_text(call.get("id"), "tool call id"),
                    _response_text(function.get("name"), "tool call name"),
                    False,
                    _args_hash(function.get("arguments")),
                    True,
                    (choice_index, call_index),
                )
            )


def _collect_output(body: dict[str, Any], found: list[_Found]) -> None:
    output = body.get("output")
    if output is None:
        return
    if type(output) is not list:
        raise ProviderBoundaryError(_INVALID_RESPONSE, "output must be a list")
    for index, item in enumerate(output):
        item_type = item.get("type") if type(item) is dict else None
        if type(item) is not dict or type(item_type) is not str:
            raise ProviderBoundaryError(_INVALID_RESPONSE, "unrecognised output item")
        if item_type in _NON_CALL_OUTPUT_TYPES:
            continue
        if item_type == "function_call":
            found.append(
                _Found(
                    _response_text(item.get("call_id"), "function call id"),
                    _response_text(item.get("name"), "function call name"),
                    False,
                    _args_hash(item.get("arguments")),
                    False,
                    (index,),
                )
            )
        elif _CALL_ITEM_RE.fullmatch(item_type):
            call_id = item.get("id") if item.get("id") is not None else item.get("call_id")
            name = item.get("name") if item.get("name") is not None else item_type
            arguments = item["arguments"] if "arguments" in item else item
            found.append(
                _Found(
                    _response_text(call_id, "hosted call id"),
                    _response_text(name, "hosted call name"),
                    True,
                    _args_hash(arguments),
                    False,
                    (index,),
                )
            )
        else:
            raise ProviderBoundaryError(_INVALID_RESPONSE, "unrecognised output item type")


def _collect(body: dict[str, Any]) -> list[_Found]:
    found: list[_Found] = []
    _collect_chat(body, found)
    _collect_output(body, found)
    ids = [f.tool_call_id for f in found]
    if len(set(ids)) != len(ids):
        raise ProviderBoundaryError(_INVALID_RESPONSE, "duplicate tool call id in one response")
    return found


def capture_response_attempts(response_body: object) -> AttemptEvidence:
    """Record every function call and hosted call of a response, before any filtering."""
    body = _json_object(response_body, "response body")
    digest = _sha256(_canonical(body))
    return AttemptEvidence(
        digest,
        tuple(
            AttemptRecord(
                f.tool_call_id,
                f.name,
                "hosted" if f.hosted else "function",
                f.args_sha256,
                digest,
            )
            for f in _collect(body)
        ),
    )


def apply_approval_filter(
    response_body: object,
    evidence: AttemptEvidence,
    *,
    approved_tool_names: frozenset[str],
) -> FilterOutcome:
    """Drop unapproved function calls and every hosted call, only against exact evidence."""
    names = _name_set(approved_tool_names, "approved_tool_names")
    body = _json_object(response_body, "response body")
    if type(evidence) is not AttemptEvidence or evidence != capture_response_attempts(body):
        raise ProviderBoundaryError(
            _EVIDENCE_MISMATCH, "the evidence was not captured from this exact response body"
        )
    filtered = copy.deepcopy(body)
    removed: list[str] = []
    for found in reversed(_collect(filtered)):
        if not found.hosted and found.name in names:
            continue
        if found.chat:
            del filtered["choices"][found.path[0]]["message"]["tool_calls"][found.path[1]]
        else:
            del filtered["output"][found.path[0]]
        removed.append(found.tool_call_id)
    removed.reverse()
    return FilterOutcome(filtered, tuple(removed), evidence)
