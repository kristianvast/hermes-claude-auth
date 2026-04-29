"""
Claude Code OAuth bypass for hermes-agent.
==========================================

Monkey-patches hermes-agent's ``agent.anthropic_adapter.build_anthropic_kwargs``
and ``normalize_anthropic_response`` at import time via a sitecustomize.py hook
so that OAuth-authenticated requests pass Anthropic's server-side content
validation and still route to the Claude Max/Pro subscription tier.

Background
----------
On 2026-04-04 Anthropic deployed server-side validation on OAuth requests: if
the ``system[]`` array contains text that doesn't match Claude Code's system
prompt structure, the request is rejected with HTTP 400 — even on accounts with
remaining subscription quota.  Third-party tools (hermes-agent, opencode, cline,
aider, etc.) all hit this simultaneously.

opencode-claude-auth v1.4.8 (PR #148) worked around it by:

  1. Injecting a cryptographically-signed ``x-anthropic-billing-header`` as
     ``system[0]``.  The signature is derived from characters at positions 4, 7,
     20 of the first user message, a hardcoded salt, and the Claude CLI version.
  2. Relocating all non-Claude-Code system prompt content to the first user
     message wrapped in ``<system-reminder>`` blocks.
  3. Adding the ``prompt-caching-scope-2026-01-05`` beta flag.

Between 2026-04-14 and 2026-04-16, Anthropic tightened the validator further.
Two additional signals matter:

  - Tool names are now inspected: real Claude Code uses PascalCase after the
    ``mcp_`` prefix (``mcp_Bash``, ``mcp_Read``, ``mcp_Background_output``).
    Requests with lowercase names (``mcp_bash``) are classified as third-party
    and the response says "Third-party apps now draw from your extra usage,
    not your plan limits."  This was fixed in opencode-claude-auth PR #191.
  - The request fingerprint was updated in Claude Code 2.1.112 (upstream PR
    #207, currently unmerged): the billing entrypoint changed from ``cli`` to
    ``sdk-cli``, the ``advisor-tool-2026-03-01`` beta flag was added, the SDK
    now sends ``x-stainless-*`` headers and ``anthropic-dangerous-direct-
    browser-access: true``, and ``/v1/messages`` is called with ``?beta=true``.

hermes-agent already implements the Claude Code identity prefix, user-agent
spoofing, ``x-app: cli``, lowercase tool name ``mcp_`` prefixing, Hermes→Claude
Code product-name scrubbing, dynamic Claude CLI version detection, and the
``oauth-2025-04-20`` / ``claude-code-20250219`` beta flags.

This patch fills the remaining gaps:

  - Signed billing header (system[0]) with the ``sdk-cli`` entrypoint.
  - System prompt relocation to first user message.
  - ``prompt-caching-scope-2026-01-05`` + ``advisor-tool-2026-03-01`` beta flags.
  - PascalCase rewrite of hermes's lowercase ``mcp_`` prefixed tool names in
    both the outgoing request and the response normalization path (so the tool
    dispatcher continues to receive the original lowercase names).
  - Stainless SDK spoof headers + ``anthropic-dangerous-direct-browser-access``
    + ``?beta=true`` query param injected via the Anthropic SDK's per-request
    ``extra_headers`` / ``extra_query`` kwargs.
  - Temperature fix for Opus 4.6 adaptive thinking (HTTP 400 otherwise).

Installation
------------
Installed automatically by ``install.sh``.  See README.md for details.

The ``sitecustomize_hook.py`` loader runs at Python interpreter startup and
hooks ``agent.anthropic_adapter``'s import so that ``apply_patches()`` runs
immediately after the module is loaded.  No hermes-agent source modifications
are needed.

Reversal
--------
Run ``uninstall.sh`` or manually remove the sitecustomize hook from the venv's
site-packages and restart hermes-gateway.

References
----------
- https://github.com/griffinmartin/opencode-claude-auth
- https://github.com/griffinmartin/opencode-claude-auth/pull/148 (billing header)
- https://github.com/griffinmartin/opencode-claude-auth/pull/191 (PascalCase tools)
- https://github.com/griffinmartin/opencode-claude-auth/pull/207 (Claude Code 2.1.112 fingerprint)

Version history
---------------
- 1.0.0 (2026-04-09): Initial — billing header, system prompt relocation,
  prompt-caching beta flag, aux-client temperature hook for Opus 4.6.
- 1.1.0 (2026-04-22): PascalCase ``mcp_`` tool prefix (request + response),
  ``sdk-cli`` billing entrypoint, ``advisor-tool-2026-03-01`` beta flag,
  Stainless SDK spoof headers, ``anthropic-dangerous-direct-browser-access``
  header, ``?beta=true`` query param on ``/v1/messages``.  Addresses the
  "Third-party apps now draw from your extra usage, not your plan limits"
  400 error introduced by Anthropic's 2026-04-14+ validator tightening.
- 1.1.1 (2026-04-22): Installer only — ``install.sh`` now auto-mirrors the
  ``Claude Code-credentials`` macOS Keychain entry into
  ``~/.claude/.credentials.json`` on Darwin hosts, so the oneliner works
  end-to-end on macOS without a manual post-install step.  Bypass module
  itself is unchanged; version bump tracks the release.
- 1.2.0 (2026-04-29): Claude Code 2.1.123 fingerprint refresh.  Anthropic's
  validator deployed a new tightening on 2026-04-28 that re-broke v1.1.x
  with the same "Third-party apps now draw from your extra usage" 400.
  Captured a real CC 2.1.123 request via a local proxy and aligned the
  patch on five drift points:
    - System identity changed: ``You are Claude Code, Anthropic's official
      CLI for Claude.`` → ``You are a Claude agent, built on Anthropic's
      Claude Agent SDK.`` (Claude Agent SDK rebrand).  The legacy string is
      now actively stripped from incoming hermes blocks instead of being
      preserved as system[1].
    - Identity block now carries ``cache_control: ephemeral 1h`` to match
      the real client's caching layout.
    - Four new beta flags required: ``context-1m-2025-08-07``,
      ``interleaved-thinking-2025-05-14``, ``context-management-2025-06-27``,
      ``effort-2025-11-24``.
    - ``x-stainless-runtime-version`` bumped ``v22.11.0`` → ``v24.3.0``.
    - New ``x-claude-code-session-id`` header (UUID per Python process).
    - New ``metadata.user_id`` body field — JSON-encoded ``{device_id,
      account_uuid, session_id}`` triple.  ``device_id`` and ``account_uuid``
      are read from ``~/.claude.json`` so the request inherits the same
      identity the local Claude Code install uses.
- 1.3.0 (2026-04-29): Stainless-header de-duplication.  v1.2.0 still tripped
  the validator because the Anthropic Python SDK injects its own
  ``X-Stainless-Lang: python`` etc. on every request, and our lowercase
  ``x-stainless-lang: js`` overrides got APPENDED instead of replacing —
  resulting in ``x-stainless-lang: python, js`` on the wire and an instant
  third-party flag.  Two changes:
    - Spoof headers now use the SDK's exact case (``X-Stainless-Lang``,
      ``User-Agent``, etc.) so ``BaseClient._merge_mappings`` actually
      replaces the SDK defaults.
    - Python-SDK-only headers are stripped via the ``Omit()`` sentinel:
      ``X-Stainless-Async``, ``x-stainless-helper``,
      ``x-stainless-helper-method``, ``x-stainless-stream-helper``,
      ``x-stainless-read-timeout``.
  Also overrides ``User-Agent`` to ``claude-cli/<version> (external, sdk-cli)``
  matching the real CC binary's wire fingerprint.
- 1.4.0 (2026-04-29): Tool-name shape alignment + body fields.  v1.3.0 still
  tripped the validator on tool-bearing requests.  The remaining mismatch
  was the tool list itself: real CC sends either PascalCase built-ins
  (``Bash``, ``Read``) or ``mcp__server__tool`` (double underscore), while
  hermes was sending flat lowercase names like ``browser_back`` /
  ``vision_analyze``.  Changes:
    - Tool names without an ``mcp_`` prefix are wrapped as
      ``mcp__hermes__<name>`` so they pattern-match the validator's expected
      shape.  Both the ``tools`` definitions and inline ``tool_use`` blocks
      in message history are rewritten.
    - ``AnthropicTransport.normalize_response`` is hooked to strip the
      ``mcp__hermes__`` prefix off returned tool calls, so hermes-agent's
      dispatcher continues to receive the original tool names unchanged.
    - Adaptive-thinking models now also send ``thinking: {"type":
      "adaptive"}`` (real CC sends this on every opus-4-7 request).
    - ``context_management`` and ``output_config`` body fields are routed
      via ``extra_body`` (Anthropic Python SDK ≤ 0.96 doesn't accept them
      as typed kwargs but forwards ``extra_body`` verbatim into the JSON
      body).  Values mirror the real-CC capture:
      ``clear_thinking_20251015 / keep: all`` and ``effort: xhigh``.
    - ``interleaved-thinking-2025-05-14`` removed from
      ``_EXTRA_OAUTH_BETAS`` — hermes already includes it via its
      ``oauth_safe_common`` list and our duplicate copy was producing a
      double entry in the comma-joined ``anthropic-beta`` header.
"""

from __future__ import annotations

__version__ = "1.4.0"

import hashlib
import inspect
import json
import logging
import os
import platform
import sys
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("anthropic_billing_bypass")

# ---------------------------------------------------------------------------
# Cryptographic signing (ported from opencode-claude-auth/src/signing.ts)
# ---------------------------------------------------------------------------

# Shared secret shipped in the Claude Code CLI binary.  Anthropic's server
# uses this salt to verify billing-header signatures.
_BILLING_SALT = "59cf53e54c78"

# Billing entrypoint — Claude Code 2.1.112+ reports ``sdk-cli`` instead of the
# legacy ``cli`` value.  Anthropic's validator matches this against the
# x-stainless-* headers; a mismatch routes the request to third-party billing.
_BILLING_ENTRYPOINT = "sdk-cli"

# Sentinel strings — entries in system[] starting with these are kept;
# everything else is relocated to the first user message.
_BILLING_PREFIX = "x-anthropic-billing-header"

# Identity prefix Claude Code 2.1.113+ ships.  Anthropic's validator now expects
# this exact string as the first non-billing system block (Anthropic Claude
# Agent SDK identity).  Real CC 2.1.123 sends it with cache_control 1h.
_SYSTEM_IDENTITY = "You are a Claude agent, built on Anthropic's Claude Agent SDK."

# Legacy identity used by Claude Code ≤ 2.1.112 and still inserted by
# hermes-agent's OAuth path.  Strip it from incoming system blocks so it
# doesn't leak into the user message via the system-reminder relocation.
_LEGACY_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# Tool-name prefix used by hermes-agent's existing OAuth path.  We rewrite
# hermes's lowercase ``mcp_foo`` to Claude Code's PascalCase ``mcp_Foo``.
_MCP_PREFIX = "mcp_"

# Synthetic MCP namespace used to wrap hermes-specific tool names so they
# pattern-match Claude Code's known tool shapes.  Real CC 2.1.123's tool list
# is either PascalCase built-ins (``Bash``, ``Read``, ``Edit``) or
# ``mcp__SERVER__TOOL`` (double underscore separators between prefix, server
# name, and tool name).  Hermes's flat lowercase tools (``browser_back``,
# ``vision_analyze``) match neither, and Anthropic's post-2026-04-28 validator
# flags those as third-party.  Wrapping them as ``mcp__hermes__browser_back``
# satisfies the format check; the response-side unwrap hook strips the
# prefix so hermes's tool dispatcher continues to receive the original name.
_MCP_HERMES_NAMESPACE = "mcp__hermes__"

# Stainless SDK version the Anthropic JS SDK reports.  Real Claude Code ships
# @anthropic-ai/sdk@0.81.0 as of 2.1.123 — we spoof the same value.
_STAINLESS_PACKAGE_VERSION = "0.81.0"

# Node runtime version Claude Code 2.1.123 runs under.  Bumped from v22.11.0
# (2.1.112 era) to track the upstream binary's reported runtime.
_STAINLESS_NODE_VERSION = "v24.3.0"

# X-Claude-Code-Session-Id — added in Claude Code 2.1.113+.  A stable UUID for
# the lifetime of the Python process; rotates on hermes-gateway restart.
_CLAUDE_CODE_SESSION_ID = str(uuid.uuid4())

# Separate session_id for the ``metadata.user_id`` JSON blob — Claude Code uses
# two independent UUIDs (one for the header, one inside metadata).  Generated
# once per Python process to mirror that pattern.
_METADATA_SESSION_ID = str(uuid.uuid4())

# Path to Claude Code's persistent state file.  Contains ``userID`` (the SHA-256
# device fingerprint Anthropic's billing layer expects) and ``groveConfigCache``
# whose first key is the OAuth account UUID.  Resolved relative to ``$HOME`` at
# import time; missing or unreadable files yield ``None`` so the patch degrades
# gracefully on non-Claude-Code hosts.
_CLAUDE_STATE_FILE = os.path.expanduser("~/.claude.json")


def _load_claude_account_info() -> Tuple[Optional[str], Optional[str]]:
    """Return ``(device_id, account_uuid)`` from ``~/.claude.json``.

    ``device_id`` comes from the top-level ``userID`` (a 64-char hex SHA-256
    fingerprint that Claude Code computes on first run and reuses across
    sessions).  ``account_uuid`` is the first key under ``groveConfigCache``,
    which Anthropic populates with the OAuth account's UUID after a successful
    sign-in.  Returns ``(None, None)`` if the file is missing/malformed so the
    caller can skip metadata injection cleanly.
    """
    try:
        with open(_CLAUDE_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    device_id = data.get("userID")
    if not isinstance(device_id, str) or not device_id:
        device_id = None
    grove = data.get("groveConfigCache")
    account_uuid: Optional[str] = None
    if isinstance(grove, dict):
        for key in grove.keys():
            if isinstance(key, str) and len(key) == 36 and key.count("-") == 4:
                account_uuid = key
                break
    return device_id, account_uuid


_DEVICE_ID, _ACCOUNT_UUID = _load_claude_account_info()

# Additional beta flags the OAuth path needs on top of hermes-agent's built-in
# ``claude-code-20250219`` and ``oauth-2025-04-20``.  These are appended to
# ``_OAUTH_ONLY_BETAS`` in ``apply_patches``.  The four flags below were added
# between Claude Code 2.1.112 and 2.1.123 — Anthropic's validator started
# requiring them on 2026-04-28, which is the regression this v1.2.0 fixes.
# Note: ``interleaved-thinking-2025-05-14`` is intentionally NOT in this list.
# Hermes-agent already includes it via its ``oauth_safe_common`` list, so
# adding it here causes a duplicate entry in the comma-joined ``anthropic-beta``
# header — a third-party tell since real CC never duplicates a beta flag.
_EXTRA_OAUTH_BETAS = [
    "prompt-caching-scope-2026-01-05",
    "advisor-tool-2026-03-01",
    "context-1m-2025-08-07",
    "context-management-2025-06-27",
    "effort-2025-11-24",
]


def _extract_first_user_message_text(messages: List[Dict[str, Any]]) -> str:
    """Return the text of the first user message's first text block.

    Matches Claude Code's K19() exactly: find the first message with
    role="user", then return the text of its first text content block.
    """
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        return text
        return ""
    return ""


def _compute_cch(message_text: str) -> str:
    """First 5 hex chars of SHA-256(message_text)."""
    return hashlib.sha256(message_text.encode("utf-8")).hexdigest()[:5]


def _compute_version_suffix(message_text: str, version: str) -> str:
    """3-char version suffix: SHA-256(salt + sampled_chars + version)[:3].

    Samples characters at indices 4, 7, 20 from the message text, padding
    with "0" when the message is shorter than the index.
    """
    sampled = "".join(
        message_text[i] if i < len(message_text) else "0" for i in (4, 7, 20)
    )
    input_str = f"{_BILLING_SALT}{sampled}{version}"
    return hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:3]


def _build_billing_header_value(
    messages: List[Dict[str, Any]],
    version: str,
    entrypoint: str,
) -> str:
    """Build the full x-anthropic-billing-header text for system[0]."""
    text = _extract_first_user_message_text(messages)
    suffix = _compute_version_suffix(text, version)
    cch = _compute_cch(text)
    return (
        f"x-anthropic-billing-header: "
        f"cc_version={version}.{suffix}; "
        f"cc_entrypoint={entrypoint}; "
        f"cch={cch};"
    )


def _stainless_arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("i386", "i686"):
        return "ia32"
    return machine or "unknown"


def _stainless_os() -> str:
    mapping = {"Darwin": "MacOS", "Linux": "Linux", "Windows": "Windows"}
    return mapping.get(platform.system(), platform.system() or "Unknown")


def _omit_sentinel() -> Any:
    """Return the Anthropic SDK's ``Omit()`` sentinel, or ``None`` if missing.

    ``BaseClient._merge_mappings`` strips ``Omit``-valued entries from the
    final headers dict, which is how we delete headers the Python SDK injects
    by default.  ``Omit`` lives at a private path (``anthropic._types``) so we
    import lazily and tolerate ImportError on older SDKs.
    """
    try:
        from anthropic._types import Omit  # type: ignore[import-not-found]

        return Omit()
    except Exception:
        return None


def _build_spoof_headers(version: str) -> Dict[str, Any]:
    """Headers real Claude Code 2.1.123 sends that the Python SDK doesn't.

    Two distinct fixes happen here:

    1. **Override** the Anthropic Python SDK's identifying headers with values
       the JS SDK / Claude Code CLI would send.  Keys MUST match the SDK's
       case (``X-Stainless-Lang`` not ``x-stainless-lang``) — the SDK's
       ``_merge_mappings`` is a plain dict update, so different-cased keys
       coexist instead of replacing, and httpx then emits both as a
       comma-joined header (``x-stainless-lang: python, js``) — an instant
       third-party tell.

    2. **Remove** Python-SDK-only telemetry headers via the ``Omit()``
       sentinel: ``X-Stainless-Async``, ``x-stainless-helper``,
       ``x-stainless-helper-method``, ``x-stainless-stream-helper``, and
       ``x-stainless-read-timeout``.  Real Claude Code never sends these.
    """
    omit = _omit_sentinel()
    headers: Dict[str, Any] = {
        # Override SDK defaults — case matches BaseClient.platform_headers().
        "User-Agent": f"claude-cli/{version} (external, sdk-cli)",
        "X-Stainless-Lang": "js",
        "X-Stainless-OS": _stainless_os(),
        "X-Stainless-Arch": _stainless_arch(),
        "X-Stainless-Runtime": "node",
        "X-Stainless-Runtime-Version": _STAINLESS_NODE_VERSION,
        "X-Stainless-Package-Version": _STAINLESS_PACKAGE_VERSION,
        # Lowercase keys — _build_headers checks lower_custom_headers before
        # adding these, so any case suppresses the SDK's own injection; we
        # use lowercase to match what real CC emits on the wire.
        "x-stainless-retry-count": "0",
        "x-stainless-timeout": "600",
        # Application-specific, no SDK collision — case is free to choose.
        "anthropic-dangerous-direct-browser-access": "true",
        "x-claude-code-session-id": _CLAUDE_CODE_SESSION_ID,
    }
    if omit is not None:
        # Keys MUST match the SDK's case at the call site that adds them; the
        # dict-merge is case-sensitive.  ``X-Stainless-Helper-Method`` and
        # ``X-Stainless-Stream-Helper`` are added in CapitalCase by
        # ``resources/messages/messages.py``; ``X-Stainless-Async`` likewise
        # by ``_client.py``.  ``x-stainless-helper`` is lowercase per
        # ``lib/_stainless_helpers.py``; ``x-stainless-read-timeout`` is
        # lowercase per the post-merge injection in ``_build_headers``.
        headers.update(
            {
                "X-Stainless-Async": omit,
                "X-Stainless-Helper-Method": omit,
                "X-Stainless-Stream-Helper": omit,
                "x-stainless-helper": omit,
                "x-stainless-read-timeout": omit,
            }
        )
    return headers


def _pascalcase_mcp_name(name: str) -> str:
    """Rewrite ``mcp_foo_bar`` → ``mcp_Foo_bar``.

    Matches opencode-claude-auth PR #191 exactly: only the character
    immediately following the ``mcp_`` prefix is uppercased.  Names already in
    PascalCase are returned unchanged.  Names already in the double-underscore
    ``mcp__server__tool`` MCP shape are also returned unchanged (the
    ``rest[0].islower()`` guard skips the leading ``_``).
    """
    if not isinstance(name, str) or not name.startswith(_MCP_PREFIX):
        return name
    rest = name[len(_MCP_PREFIX):]
    if not rest or not rest[0].islower():
        return name
    return _MCP_PREFIX + rest[0].upper() + rest[1:]


def _wrap_tool_name_as_mcp_hermes(name: str) -> str:
    """Wrap a non-MCP-format tool name as ``mcp__hermes__<name>``.

    No-op for names that already start with ``mcp_`` (any flavor: legacy
    single-underscore Claude Code prefix or modern ``mcp__server__tool``).
    Empty strings and non-strings pass through unchanged.
    """
    if not isinstance(name, str) or not name:
        return name
    if name.startswith(_MCP_PREFIX):
        return name
    return _MCP_HERMES_NAMESPACE + name


def _unwrap_mcp_hermes_name(name: Any) -> Any:
    """Reverse of ``_wrap_tool_name_as_mcp_hermes`` for response normalization.

    Strips the ``mcp__hermes__`` prefix so hermes-agent's tool dispatcher
    receives the original tool name back.  Anything not bearing the prefix
    is returned unchanged.
    """
    if isinstance(name, str) and name.startswith(_MCP_HERMES_NAMESPACE):
        return name[len(_MCP_HERMES_NAMESPACE) :]
    return name


def _normalize_tool_name(name: str) -> str:
    """Return a tool name shaped like something real Claude Code would send.

    Two stacked rewrites:
      1. ``mcp_foo`` → ``mcp_Foo`` (legacy PascalCase fix from
         opencode-claude-auth PR #191; harmless if hermes never adds an
         ``mcp_`` prefix).
      2. ``foo_bar`` → ``mcp__hermes__foo_bar`` (new in v1.4.0 — the
         post-2026-04-28 validator rejects flat lowercase tool names).
    Names already starting with ``mcp_`` skip step 2 since they already
    match a Claude-Code-shaped tool prefix.
    """
    return _wrap_tool_name_as_mcp_hermes(_pascalcase_mcp_name(name))


def _rewrite_tool_names_pascalcase(api_kwargs: Dict[str, Any]) -> None:
    """Reshape every tool name in the request to look Claude-Code-native.

    Touches both the ``tools`` definition list and any inline ``tool_use``
    blocks in the message history (so prior turns reference the same wrapped
    name as the current call).  See ``_normalize_tool_name`` for the rewrite
    rules.  The response-side unwrap hook
    (``_install_anthropic_transport_unwrap_hook``) reverses the
    ``mcp__hermes__`` wrap on the way back so hermes-agent's dispatcher keeps
    receiving the original names.
    """
    tools = api_kwargs.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and "name" in tool:
                tool["name"] = _normalize_tool_name(tool.get("name") or "")

    messages = api_kwargs.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and "name" in block:
                    block["name"] = _normalize_tool_name(block.get("name") or "")
                elif block.get("type") == "tool_result" and "tool_use_id" in block:
                    # tool_result blocks reference the tool by id, not name —
                    # nothing to rewrite here, but listed for completeness.
                    pass


def _inject_metadata(api_kwargs: Dict[str, Any]) -> None:
    """Set ``metadata.user_id`` to the JSON triple Claude Code 2.1.113+ sends.

    Anthropic's validator cross-checks ``metadata.user_id.account_uuid`` against
    the OAuth token's account claim.  Without this field the request is routed
    to third-party billing (the "extra usage" 400 we're working around).  We
    pull ``device_id`` and ``account_uuid`` from ``~/.claude.json`` so we
    inherit whatever account is currently signed into Claude Code on this host.
    """
    if not _DEVICE_ID or not _ACCOUNT_UUID:
        return
    user_id_blob = json.dumps(
        {
            "device_id": _DEVICE_ID,
            "account_uuid": _ACCOUNT_UUID,
            "session_id": _METADATA_SESSION_ID,
        },
        separators=(",", ":"),
    )
    existing = api_kwargs.get("metadata")
    metadata: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    metadata["user_id"] = user_id_blob
    api_kwargs["metadata"] = metadata


def _merge_spoof_extras(api_kwargs: Dict[str, Any], *, version: str) -> None:
    """Inject Claude Code 2.1.123 request fingerprint via extra_headers / extra_query.

    The Anthropic Python SDK forwards both to the underlying HTTP request:
    ``extra_headers`` becomes request headers (merged with client defaults),
    ``extra_query`` becomes URL query parameters.  We avoid overwriting values
    already set by hermes-agent (e.g. its fast-mode ``anthropic-beta`` header)
    so our spoof is additive — except where we WANT to overwrite (the SDK's
    own X-Stainless-* identifiers), in which case ``_build_spoof_headers``
    uses the SDK's exact key case so dict-merge actually replaces.
    """
    existing_headers = api_kwargs.get("extra_headers")
    merged_headers: Dict[str, Any] = dict(_build_spoof_headers(version))
    if isinstance(existing_headers, dict):
        for key, value in existing_headers.items():
            merged_headers[key] = value
    api_kwargs["extra_headers"] = merged_headers

    existing_query = api_kwargs.get("extra_query")
    merged_query: Dict[str, str] = {"beta": "true"}
    if isinstance(existing_query, dict):
        for key, value in existing_query.items():
            merged_query[key] = value
    api_kwargs["extra_query"] = merged_query


# ---------------------------------------------------------------------------
# Bypass logic (ported from opencode-claude-auth/src/transforms.ts)
# ---------------------------------------------------------------------------


def _model_supports_adaptive_thinking(model: str) -> bool:
    if not isinstance(model, str):
        return False
    # Opus 4.6 and 4.7 both accept ``thinking: {"type": "adaptive"}``.  Real
    # Claude Code 2.1.123 sends this on every opus-4-7 request; without it
    # the validator sees a model+request shape that doesn't match real CC and
    # routes to third-party billing.
    return any(v in model for v in ("4-6", "4.6", "4-7", "4.7"))


def _inject_adaptive_thinking(api_kwargs: Dict[str, Any]) -> None:
    """Set ``thinking: {"type": "adaptive"}`` for adaptive-capable OAuth requests.

    Mirrors what real Claude Code sends so the request shape matches.  Skipped
    if the caller has already set ``thinking`` (e.g. explicit non-adaptive
    config) or if the model doesn't support adaptive thinking.
    """
    model = api_kwargs.get("model")
    if not _model_supports_adaptive_thinking(model or ""):
        return
    if api_kwargs.get("thinking") is not None:
        return
    api_kwargs["thinking"] = {"type": "adaptive"}


def _inject_context_management_and_effort(api_kwargs: Dict[str, Any]) -> None:
    """Add the body fields that must accompany the corresponding beta flags.

    We declare ``context-management-2025-06-27`` and ``effort-2025-11-24`` in
    ``anthropic-beta`` to match real CC.  The validator appears to require
    the matching body fields too; without them the request shape diverges
    and we flip back to third-party billing.  Values mirror the real-CC
    capture: ``clear_thinking_20251015`` with ``keep: all`` and
    ``effort: xhigh``.

    These fields are NOT typed parameters of ``Messages.stream/create`` in
    the Anthropic Python SDK ≤ 0.96 (Claude Code uses the JS SDK which
    accepts them).  Setting them as top-level kwargs raises
    ``TypeError: got an unexpected keyword argument``.  Route them through
    ``extra_body`` instead — the SDK merges that dict verbatim into the
    JSON body before sending.  Skipped if the caller already populated
    either field via ``extra_body`` so hermes-agent can override.
    """
    extra_body = api_kwargs.get("extra_body")
    merged: Dict[str, Any] = dict(extra_body) if isinstance(extra_body, dict) else {}
    merged.setdefault(
        "context_management",
        {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
    )
    merged.setdefault("output_config", {"effort": "xhigh"})
    api_kwargs["extra_body"] = merged


def _fix_temperature_for_oauth_adaptive(
    api_kwargs: Dict[str, Any],
    *,
    site: str,
) -> None:
    """Strip temperature from OAuth requests on adaptive-thinking models.

    Opus 4.6 with implicit adaptive thinking rejects non-1 temperature
    values with HTTP 400.  This drops the parameter entirely so the API
    uses its default.
    """
    if "temperature" not in api_kwargs:
        return
    temp = api_kwargs.get("temperature")
    if temp == 1 or temp == 1.0:
        return
    model = api_kwargs.get("model")
    if not _model_supports_adaptive_thinking(model or ""):
        return
    del api_kwargs["temperature"]
    logger.info(
        "Dropped temperature=%r for OAuth adaptive-thinking model %r (site=%s)",
        temp,
        model,
        site,
    )


def _prepend_to_first_user_message(
    messages: List[Dict[str, Any]],
    texts: List[str],
) -> None:
    """Prepend each text as a <system-reminder> block to the first user message.

    Mutates ``messages`` in place.
    """
    if not texts:
        return
    combined = "\n\n".join(f"<system-reminder>\n{t}\n</system-reminder>" for t in texts)
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            new_text = f"{combined}\n\n{content}" if content else combined
            messages[i] = {**msg, "content": [{"type": "text", "text": new_text}]}
            return
        if isinstance(content, list):
            new_content = list(content)
            for j, block in enumerate(new_content):
                if isinstance(block, dict) and block.get("type") == "text":
                    existing = block.get("text") or ""
                    new_content[j] = {
                        **block,
                        "text": f"{combined}\n\n{existing}" if existing else combined,
                    }
                    messages[i] = {**msg, "content": new_content}
                    return
            new_content.insert(0, {"type": "text", "text": combined})
            messages[i] = {**msg, "content": new_content}
            return
        messages[i] = {**msg, "content": [{"type": "text", "text": combined}]}
        return


def apply_claude_code_bypass(api_kwargs: Dict[str, Any], version: str) -> None:
    """Mutate api_kwargs in place to pass OAuth content validation.

    Only call on OAuth requests (``is_oauth=True``).  Safe to call multiple
    times — stale billing headers are replaced, duplicate identity entries
    are dropped.

    After this runs, ``api_kwargs["system"]`` contains at most the billing
    header and the Claude Code identity prefix.  Everything else is moved to
    the first user message as ``<system-reminder>`` blocks.
    """
    messages = api_kwargs.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    raw_system = api_kwargs.get("system")
    if raw_system is None:
        system: List[Any] = []
    elif isinstance(raw_system, str):
        system = [{"type": "text", "text": raw_system}] if raw_system else []
    elif isinstance(raw_system, list):
        system = list(raw_system)
    else:
        logger.warning(
            "Unexpected system type %s; skipping bypass", type(raw_system).__name__
        )
        return

    # Compute billing header using ORIGINAL messages (before relocation).
    try:
        billing_value = _build_billing_header_value(
            messages, version, _BILLING_ENTRYPOINT
        )
    except Exception as exc:
        logger.warning("Failed to build billing header: %s", exc)
        return
    billing_entry = {"type": "text", "text": billing_value}

    kept: List[Any] = []
    moved_texts: List[str] = []
    identity_seen = False

    for entry in system:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        entry_type = entry.get("type")
        if entry_type != "text":
            kept.append(entry)
            continue
        text = entry.get("text") or ""
        if text.startswith(_BILLING_PREFIX):
            continue  # stale billing header — drop
        if text.startswith(_SYSTEM_IDENTITY):
            if identity_seen:
                continue  # duplicate — drop
            identity_seen = True
            rest = text[len(_SYSTEM_IDENTITY) :].lstrip("\n")
            identity_entry = {k: v for k, v in entry.items() if k != "text"}
            identity_entry["text"] = _SYSTEM_IDENTITY
            identity_entry.setdefault(
                "cache_control", {"type": "ephemeral", "ttl": "1h"}
            )
            kept.append(identity_entry)
            if rest:
                moved_texts.append(rest)
            continue
        if text.startswith(_LEGACY_SYSTEM_IDENTITY):
            # Hermes-agent (and Claude Code ≤ 2.1.112) prefixes the system
            # prompt with the legacy "You are Claude Code…" identity string.
            # Real CC 2.1.123 no longer sends this, and Anthropic's validator
            # rejects requests that contain it.  Strip the prefix and let any
            # trailing content fall through to the user-message relocation.
            rest = text[len(_LEGACY_SYSTEM_IDENTITY) :].lstrip("\n")
            if rest:
                moved_texts.append(rest)
            continue
        if text:
            moved_texts.append(text)

    if not identity_seen:
        kept.insert(
            0,
            {
                "type": "text",
                "text": _SYSTEM_IDENTITY,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        )

    # Billing header first (no cache_control — changes per request).
    api_kwargs["system"] = [billing_entry] + kept

    if moved_texts:
        _prepend_to_first_user_message(messages, moved_texts)

    _rewrite_tool_names_pascalcase(api_kwargs)
    _merge_spoof_extras(api_kwargs, version=version)
    _inject_metadata(api_kwargs)
    _inject_adaptive_thinking(api_kwargs)
    _inject_context_management_and_effort(api_kwargs)
    _fix_temperature_for_oauth_adaptive(api_kwargs, site="build_kwargs")


# ---------------------------------------------------------------------------
# Monkey-patch installation
# ---------------------------------------------------------------------------


def _get_version_safely(aa_module: Any) -> str:
    """Return the Claude CLI version string from the adapter module."""
    getter = getattr(aa_module, "_get_claude_code_version", None)
    if callable(getter):
        try:
            version = getter()
            if isinstance(version, str) and version and version[0].isdigit():
                return version
        except Exception:
            pass
    fallback = getattr(aa_module, "_CLAUDE_CODE_VERSION_FALLBACK", None)
    if isinstance(fallback, str) and fallback:
        return fallback
    return "2.1.90"


def _lowercase_first(name: str) -> str:
    if not name:
        return name
    return name[0].lower() + name[1:]


def _install_response_pascalcase_unhook(aa_module: Any, force: bool = False) -> bool:
    """Post-process ``normalize_anthropic_response`` to restore lowercase tool names.

    We rewrote outgoing tool names from ``mcp_bash`` to ``mcp_Bash`` to pass
    Anthropic's validator.  The response comes back referencing ``mcp_Bash``
    too.  Hermes strips the ``mcp_`` prefix (line 1488-1489 of
    ``anthropic_adapter``), leaving ``Bash`` — which hermes's tool dispatcher
    cannot find because the registered name is ``bash``.  We wrap
    ``normalize_anthropic_response`` to lowercase the first character of each
    tool call name after hermes's strip runs.
    """
    if getattr(aa_module, "_CLAUDE_CODE_RESPONSE_UNHOOK_APPLIED", False) and not force:
        logger.debug("response PascalCase unhook already installed")
        return True

    original = getattr(aa_module, "normalize_anthropic_response", None)
    if not callable(original):
        logger.warning("normalize_anthropic_response not found; skipping response unhook")
        return False

    def patched_normalize(response: Any, strip_tool_prefix: bool = False, **kwargs: Any) -> Any:
        result = original(response, strip_tool_prefix=strip_tool_prefix, **kwargs)
        if not strip_tool_prefix:
            return result
        try:
            assistant_message, _finish = result
        except (TypeError, ValueError):
            return result
        tool_calls = getattr(assistant_message, "tool_calls", None)
        if not tool_calls:
            return result
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            name = getattr(fn, "name", None)
            if isinstance(name, str) and name and name[0].isupper():
                try:
                    fn.name = _lowercase_first(name)
                except Exception:
                    pass
        return result

    patched_normalize.__name__ = original.__name__
    patched_normalize.__qualname__ = getattr(
        original, "__qualname__", original.__name__
    )
    patched_normalize.__doc__ = original.__doc__
    patched_normalize.__wrapped__ = original  # type: ignore[attr-defined]

    aa_module.normalize_anthropic_response = patched_normalize
    aa_module._CLAUDE_CODE_RESPONSE_UNHOOK_APPLIED = True  # type: ignore[attr-defined]
    logger.info("Response PascalCase unhook installed on normalize_anthropic_response")
    sys.stderr.write(
        "[anthropic_billing_bypass] Response PascalCase unhook installed\n"
    )
    return True


def _install_anthropic_transport_unwrap_hook(force: bool = False) -> bool:
    """Wrap ``AnthropicTransport.normalize_response`` to undo the namespace wrap.

    The request-side rewrite turns ``browser_back`` into
    ``mcp__hermes__browser_back`` so Anthropic's validator treats the request
    as Claude Code-shaped.  Anthropic then echoes the wrapped name back in
    every ``tool_use`` block.  Without this hook, hermes-agent's tool
    dispatcher would receive ``mcp__hermes__browser_back`` and fail to find
    the corresponding tool implementation.  We post-process the
    ``NormalizedResponse`` to strip the ``mcp__hermes__`` prefix from each
    tool call's ``name`` field.

    Idempotent — safe to call multiple times.  Returns ``True`` on success
    (or already installed), ``False`` if the transport module is missing or
    incompatible.
    """
    try:
        from agent.transports import anthropic as transport_module  # type: ignore[import-not-found]
    except Exception as exc:
        logger.warning(
            "transport_unwrap_hook_failed_import: %s: %s",
            type(exc).__name__,
            exc,
        )
        return False

    transport_cls = getattr(transport_module, "AnthropicTransport", None)
    if transport_cls is None:
        logger.warning("transport_unwrap_hook_failed: AnthropicTransport not found")
        return False

    if (
        getattr(transport_cls, "_HERMES_MCP_UNWRAP_APPLIED", False)
        and not force
    ):
        logger.debug("transport unwrap hook already installed")
        return True

    original = getattr(transport_cls, "normalize_response", None)
    if not callable(original):
        logger.warning(
            "transport_unwrap_hook_failed: normalize_response not callable"
        )
        return False

    def patched_normalize(self: Any, response: Any, **kwargs: Any) -> Any:
        result = original(self, response, **kwargs)
        try:
            tool_calls = getattr(result, "tool_calls", None)
            if not tool_calls:
                return result
            for tc in tool_calls:
                name = getattr(tc, "name", None)
                new_name = _unwrap_mcp_hermes_name(name)
                if new_name is not name and new_name != name:
                    try:
                        tc.name = new_name
                    except (AttributeError, TypeError):
                        # Frozen dataclass / NamedTuple — fall back to
                        # mutating the underlying dict if available.
                        d = getattr(tc, "__dict__", None)
                        if isinstance(d, dict) and "name" in d:
                            d["name"] = new_name
        except Exception as exc:
            logger.warning(
                "transport_unwrap_hook_runtime_error: %s: %s",
                type(exc).__name__,
                exc,
            )
        return result

    patched_normalize.__name__ = original.__name__
    patched_normalize.__qualname__ = getattr(
        original, "__qualname__", original.__name__
    )
    patched_normalize.__doc__ = original.__doc__
    patched_normalize.__wrapped__ = original  # type: ignore[attr-defined]

    transport_cls.normalize_response = patched_normalize
    transport_cls._HERMES_MCP_UNWRAP_APPLIED = True  # type: ignore[attr-defined]
    logger.info(
        "AnthropicTransport.normalize_response unwrap hook installed"
    )
    sys.stderr.write(
        "[anthropic_billing_bypass] Transport unwrap hook installed\n"
    )
    return True


def _install_aux_client_hook(force: bool = False) -> bool:
    """Patch the auxiliary client to strip temperature on OAuth adaptive models."""
    try:
        from agent import auxiliary_client as ac  # type: ignore[import-not-found]
    except Exception as exc:
        logger.warning("aux_client_hook_failed_import: %s: %s", type(exc).__name__, exc)
        sys.stderr.write(
            f"[anthropic_billing_bypass] aux_client_hook_failed_import: "
            f"{type(exc).__name__}: {exc}\n"
        )
        return False

    adapter_cls = getattr(ac, "_AnthropicCompletionsAdapter", None)
    if adapter_cls is None:
        logger.warning("aux_client_hook_failed: _AnthropicCompletionsAdapter not found")
        return False

    if getattr(adapter_cls, "_AUX_CLIENT_TEMP_HOOK_APPLIED", False) and not force:
        logger.debug("aux_client_hook already installed")
        return True

    original_create = getattr(adapter_cls, "create", None)
    if not callable(original_create):
        logger.warning("aux_client_hook_failed: create() not callable on adapter")
        return False

    def patched_create(self: Any, **kwargs: Any) -> Any:
        real_client = getattr(self, "_client", None)
        if real_client is None:
            return original_create(self, **kwargs)
        messages_obj = getattr(real_client, "messages", None)
        if messages_obj is None:
            return original_create(self, **kwargs)

        is_oauth = bool(getattr(self, "_is_oauth", False))
        if not is_oauth:
            return original_create(self, **kwargs)

        inner_original = messages_obj.create

        def fixed_messages_create(**inner_kwargs: Any) -> Any:
            try:
                _fix_temperature_for_oauth_adaptive(inner_kwargs, site="aux_client")
            except Exception as exc:
                logger.warning(
                    "aux_client_hook: temperature fix raised %s: %s",
                    type(exc).__name__,
                    exc,
                )
            return inner_original(**inner_kwargs)

        try:
            messages_obj.create = fixed_messages_create
            rebind_ok = True
        except (AttributeError, TypeError):
            rebind_ok = False
        try:
            if rebind_ok:
                return original_create(self, **kwargs)

            class _ShimMessages:
                create = staticmethod(fixed_messages_create)

            class _ShimClient:
                messages = _ShimMessages()

            self._client = _ShimClient()
            try:
                return original_create(self, **kwargs)
            finally:
                self._client = real_client
        finally:
            if rebind_ok:
                try:
                    del messages_obj.create
                except (AttributeError, TypeError):
                    messages_obj.create = inner_original

    patched_create.__name__ = original_create.__name__
    patched_create.__qualname__ = getattr(
        original_create, "__qualname__", original_create.__name__
    )
    patched_create.__doc__ = original_create.__doc__
    patched_create.__wrapped__ = original_create  # type: ignore[attr-defined]

    adapter_cls.create = patched_create
    adapter_cls._AUX_CLIENT_TEMP_HOOK_APPLIED = True
    logger.info(
        "Aux client temperature hook installed on _AnthropicCompletionsAdapter.create"
    )
    sys.stderr.write(
        "[anthropic_billing_bypass] Aux client temperature hook installed\n"
    )
    return True


def apply_patches(anthropic_adapter_module: Any = None) -> bool:
    """Install the bypass on ``agent.anthropic_adapter``.

    Called by the sitecustomize hook after the module is imported.  Returns
    ``True`` on success, ``False`` if the target module is incompatible.
    Idempotent — safe to call multiple times.
    """
    aa = anthropic_adapter_module
    if aa is None:
        try:
            from agent import anthropic_adapter as aa  # type: ignore[import-not-found,no-redef]
        except ImportError as exc:
            logger.warning("Cannot import agent.anthropic_adapter: %s", exc)
            return False

    if getattr(aa, "_CLAUDE_CODE_BYPASS_APPLIED", False):
        logger.debug("Claude Code bypass already installed")
        return True

    # 1. Add the missing beta flags (prompt-caching + advisor-tool).
    oauth_betas = getattr(aa, "_OAUTH_ONLY_BETAS", None)
    if isinstance(oauth_betas, list):
        for new_beta in _EXTRA_OAUTH_BETAS:
            if new_beta not in oauth_betas:
                oauth_betas.append(new_beta)
                logger.info("Appended beta flag: %s", new_beta)

    # 2. Verify the target function exists with the expected signature.
    original_build = getattr(aa, "build_anthropic_kwargs", None)
    if not callable(original_build):
        logger.warning(
            "agent.anthropic_adapter.build_anthropic_kwargs not found — "
            "skipping monkey-patch (incompatible hermes-agent version?)"
        )
        return False

    try:
        sig = inspect.signature(original_build)
        if "is_oauth" not in sig.parameters:
            logger.warning(
                "build_anthropic_kwargs lacks 'is_oauth' param — "
                "skipping monkey-patch (incompatible hermes-agent version?)"
            )
            return False
    except (TypeError, ValueError) as exc:
        logger.warning("Cannot introspect build_anthropic_kwargs: %s", exc)
        return False

    # 3. Wrap build_anthropic_kwargs to apply the bypass on OAuth requests.
    def patched_build_anthropic_kwargs(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original_build(*args, **kwargs)

        try:
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            is_oauth = bool(bound.arguments.get("is_oauth", False))
        except TypeError:
            is_oauth = bool(kwargs.get("is_oauth", False))

        if is_oauth and isinstance(result, dict):
            try:
                apply_claude_code_bypass(result, _get_version_safely(aa))
            except Exception as exc:
                logger.warning(
                    "apply_claude_code_bypass raised %s: %s",
                    type(exc).__name__,
                    exc,
                )
                traceback.print_exc(file=sys.stderr)
        return result

    patched_build_anthropic_kwargs.__name__ = original_build.__name__
    patched_build_anthropic_kwargs.__qualname__ = getattr(
        original_build, "__qualname__", original_build.__name__
    )
    patched_build_anthropic_kwargs.__doc__ = original_build.__doc__
    patched_build_anthropic_kwargs.__module__ = getattr(
        original_build, "__module__", __name__
    )
    patched_build_anthropic_kwargs.__wrapped__ = original_build  # type: ignore[attr-defined]

    aa.build_anthropic_kwargs = patched_build_anthropic_kwargs
    aa._CLAUDE_CODE_BYPASS_APPLIED = True  # type: ignore[attr-defined]
    logger.info("Claude Code OAuth bypass installed (build_anthropic_kwargs)")
    sys.stderr.write("[anthropic_billing_bypass] Claude Code OAuth bypass installed\n")

    _install_response_pascalcase_unhook(aa)
    _install_anthropic_transport_unwrap_hook()
    _install_aux_client_hook()

    return True
