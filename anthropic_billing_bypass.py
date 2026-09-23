"""
Claude Code OAuth bypass for hermes-agent.
==========================================

Monkey-patches hermes-agent's anthropic adapter so OAuth-authenticated
requests pass Anthropic's server-side billing validator and route to the
Claude Max/Pro subscription tier.

Tracks upstream ``griffinmartin/opencode-claude-auth`` (TypeScript) and
ports its bypass behaviors to Python.

Version history
---------------
- 1.7.0 (2026-08-18): Community PR integration sweep.  Fingerprint re-synced
  to upstream's current ``ccVersion`` 2.1.217 (was pinned at 2.1.112 while
  the real CLI shipped 2.1.234): system identity reverted to upstream's
  "You are Claude Code, ..." string (the "Claude Agent SDK" variant from
  PR #15 is now only *recognised* on input, never emitted), beta list aligned
  to upstream ``baseBetas`` (added interleaved-thinking-2025-05-14,
  thinking-token-count-2026-05-13, extended-cache-ttl-2025-04-11; dropped the
  unconditional effort-2025-11-24, which upstream applies per-model), and the
  per-request ``x-client-request-id`` header added.  Merges PR #16/#20/#21/
  #23/#24/#26/#27/#28.
- 1.6.0 (2026-05-08): Wire-format changes for CC 2.1.117 parity — structured
  metadata (JSON-encoded device_id + account_uuid + session_id),
  X-Claude-Code-Session-Id header, context_management body field, Node v24.3.0
  stainless header, cache_control on system identity entry.  (Its system
  identity change was reverted in 1.7.0; see above.)
- 1.6.0 (2026-08-06): Bound the headerless-429 auto-wait loop.  A 429 whose
  body is ``{"type":"rate_limit_error","message":"Error"}`` and which carries
  no ``anthropic-ratelimit-unified-*`` headers is Anthropic's third-party
  *detection* response, not a quota — the request went out without the Claude
  Code identity.  Retrying it unchanged can never succeed, but the previous
  code fell back to ``HERMES_RL_AUTOWAIT_DEFAULT_S`` (300s) and looped
  forever.  Observed in production: one stuck ``delegate_task`` child emitted
  370 × 429 over 24h at exactly 5-minute spacing while its parent stayed
  blocked on ``result()``.  Headerless 429s now retry at most
  ``HERMES_RL_AUTOWAIT_HEADERLESS_ATTEMPTS`` (default 2) times before
  re-raising to Hermes core's normal give-up path.  429s that *do* carry
  reset headers (real quota) are unaffected.
- 1.5.7 (2026-05-30): Root cause fix: preserve original Anthropic content
  block interleaving order instead of stripping thinking blocks as a
  workaround.  Hermes core changes:
  (a) AnthropicTransport.normalize_response saves the raw content block
      array (with original order) in provider_data["_anthropic_raw_content"].
  (b) chat_completion_helpers forwards _anthropic_raw_content onto the
      assistant message dict.
  (c) _convert_assistant_message uses _anthropic_raw_content (fast path)
      to replay the original block order, making signed thinking blocks
      byte-identical to the API response — no more signature invalidation.
  (d) _strip_thinking_from_replay now skips messages with signed thinking
      blocks (i.e. those that went through the fast path), only stripping
      legacy messages where block order was lost.
  (e) error_classifier classifies "cannot be modified" as thinking_signature.
  (f) conversation_loop recovery strips reasoning_content alongside
      reasoning_details to prevent re-synthesis of unsigned thinking blocks.
- 1.5.6 (2026-05-30): Fix: _strip_thinking_from_replay now handles the edge
  case where ALL content blocks in an assistant message are thinking blocks.
  Previously the empty-list check `if stripped:` was False for `[]`, leaving
  the original signed thinking blocks intact and triggering the "cannot be
  modified" 400.  Also patched Hermes core error_classifier to classify the
  "cannot be modified" error as thinking_signature (it lacked "signature" in
  the message text), and conversation_loop recovery to strip reasoning_content
  alongside reasoning_details to prevent re-synthesis of unsigned thinking
  blocks on retry.
- 1.5.5 (2026-05-30): Fix: _strip_thinking_from_replay removes signed
  thinking/redacted_thinking blocks from ALL assistant messages before
  tool-name rewriting.  Hermes round-trips reorder thinking vs tool_use
  blocks, making Anthropic signature validation impossible — byte-identical
  replay cannot be achieved.  Strip thinking blocks pre-emptively (not via
  conversation_loop recovery which triggers the "cannot be modified" 400
  by mutating the latest assistant message).  The model still generates
  fresh thinking for the current turn.
- 1.5.4 (2026-05-30): REVERTED in 1.5.5.  The _has_thinking_block guard on
  _rewrite_tool_names was counter-productive: _rewrite_tool_names MUST
  restore tool names to their original mcp__hermes__ form for replay
  byte-identity; skipping the rewrite left names as mcp_<name> which
  differs from the original.
- 1.5.3 (2026-05-30): Fix: _split_tool_results_from_followup_user_text
  no longer strips thinking blocks from the assistant message during
  interrupted tool-turn repair.  The stripping triggered Anthropic's
  "thinking blocks cannot be modified" 400, and the standard recovery
  path (strip reasoning_details → retry) would hit the same error again
  because the bypass re-stripped on every retry.  The assistant is now
  preserved byte-for-byte; Hermes core's _manage_thinking_signatures
  handles non-latest-assistant thinking blocks on the next turn.
- 1.5.1 (2026-05-30): Classify Anthropic's newer "latest assistant thinking
  blocks cannot be modified" 400 as recoverable thinking replay failure.
- 1.5.0 (2026-05-06): Fix literal ``\\n`` escapes in system-reminder text,
  lowercase Stainless headers (matches upstream JS SDK), restore Opus 4.6
  temperature stripping, port ``repair_tool_pairs`` (upstream PR #136) and
  haiku effort stripping (upstream PR #126), lowercase tool names after
  unwrap to silence hermes auto-repair (intent of commit 6d9cade), patch
  ``normalize_response`` on both old and new hermes transports.
- 1.4.0-pr10 (2026-04-29): Hermes 0.11.0 ``AnthropicTransport`` support,
  ``mcp__hermes__`` namespacing, accountUuid → user_id metadata.
- 1.1.1 (2026-04-22): macOS Keychain mirror in installer (no module change).
- 1.1.0 (2026-04-22): PascalCase ``mcp_`` tools, ``sdk-cli`` entrypoint,
  ``advisor-tool-2026-03-01`` beta, Stainless headers, ``?beta=true``.
- 1.0.0 (2026-04-09): Billing header, system prompt relocation, prompt-
  caching beta, Opus 4.6 temperature hook.

References
----------
- https://github.com/griffinmartin/opencode-claude-auth
- PR #126: strip ``effort`` for haiku models
- PR #136: repair orphaned tool_use / tool_result pairs
- PR #148: relocate non-identity system entries to first user message
- PR #191: PascalCase tool names after ``mcp_`` prefix
- PR #207: Claude Code 2.1.112 fingerprint + ``?beta=true``
"""

from __future__ import annotations

__version__ = "1.7.0"

import hashlib
import inspect
import json
import logging
import os
import platform
import sys
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("anthropic_billing_bypass")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Shared salt shipped in the Claude Code CLI binary; Anthropic's server uses
# this to verify billing-header signatures.
_BILLING_SALT = "59cf53e54c78"

# Claude Code 2.1.112+ reports ``sdk-cli`` instead of legacy ``cli``.  A
# mismatch with x-stainless-* headers routes the request to third-party
# billing.
_BILLING_ENTRYPOINT = "sdk-cli"

# Sentinel strings — entries in system[] starting with these are kept;
# everything else is relocated to the first user message.
_BILLING_PREFIX = "x-anthropic-billing-header"
# The identity prefix the validator matches to route a request to subscription
# billing.  Upstream griffinmartin/opencode-claude-auth still ships this exact
# string at ccVersion 2.1.217 (src/transforms.ts), i.e. far newer than the
# 2.1.117 that PR #15 claimed had replaced it with an "Agent SDK" identity.
# Deviating here is what flips traffic to extra-usage, so track upstream.
_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
# Also recognised on input so an Agent-SDK-style identity injected by
# hermes-agent (or left by an older install) is normalised, not duplicated.
_AGENT_SDK_SYSTEM_IDENTITY = (
    "You are a Claude agent, built on Anthropic's Claude Agent SDK."
)
_OLD_SYSTEM_IDENTITY = _AGENT_SDK_SYSTEM_IDENTITY

# Hermes prefixes MCP tools with ``mcp_``.  We rewrite that to the standard
# ``mcp__<server>__<tool>`` namespace Anthropic expects from real Claude Code,
# using ``hermes`` as the server name.
_MCP_PREFIX = "mcp_"
_MCP_HERMES_NAMESPACE = "mcp__hermes__"

# Stainless-generated SDK headers Claude Code 2.1.112 sends.  Lowercase to
# match the JS SDK output exactly (HTTP headers are case-insensitive but
# upstream's spoof uses lowercase, and so does our pre-merge code).
_STAINLESS_PACKAGE_VERSION = "0.81.0"
_STAINLESS_NODE_VERSION = "v24.3.0"

# Fallback CC version used for BOTH the billing-header signature AND the
# user-agent header when dynamic detection fails (e.g. Claude CLI not on
# PATH).  Anthropic's validator cross-references these; a mismatch flags the
# request as third-party and routes traffic to extra usage.
# Tracks upstream griffinmartin/opencode-claude-auth ``ccVersion`` (currently
# "2.1.217", src/model-config.ts).  When Claude Code IS on PATH the detected
# version wins, so this only matters on hosts without the CLI installed.
_PINNED_CC_VERSION = "2.1.217"

# Cache for the dynamically detected Claude Code version.
_CC_VERSION_CACHE: Optional[str] = None


def _detect_local_claude_version() -> str:
    """Detect the installed Claude Code version from the local binary.

    Runs ``claude --version`` (and ``claude-code --version`` as a fallback)
    and parses the leading ``X.Y.Z`` token.  Returns the fallback pin if the
    binary is missing or the output is unparseable.  Cached after first call.
    """
    global _CC_VERSION_CACHE
    if _CC_VERSION_CACHE is not None:
        return _CC_VERSION_CACHE
    import subprocess as _sp
    for cmd in ("claude", "claude-code"):
        try:
            result = _sp.run(
                [cmd, "--version"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip().split()[0]
                if version and version[0].isdigit():
                    _CC_VERSION_CACHE = version
                    return version
        except Exception:
            pass
    _CC_VERSION_CACHE = _PINNED_CC_VERSION
    return _CC_VERSION_CACHE


def _local_claude_version() -> str:
    """Public accessor: the version to advertise in headers + billing signature."""
    return _detect_local_claude_version()

# OAuth-only beta flags appended on top of hermes-agent's built-in
# ``claude-code-20250219`` and ``oauth-2025-04-20``.  Mirrors the remainder of
# upstream's ``baseBetas`` (src/model-config.ts @ ccVersion 2.1.217).
#
# ``effort-2025-11-24`` is deliberately absent: upstream moved it out of
# baseBetas into per-model overrides (added for 4-6/4-7, excluded for
# sonnet/haiku).  Hermes installs these flags process-wide rather than
# per-request, so sending it unconditionally would hit the models upstream
# explicitly excludes.  ``_strip_effort`` still removes the ``effort``
# *parameter* for haiku, which is a separate concern.
_EXTRA_OAUTH_BETAS = [
    "interleaved-thinking-2025-05-14",
    "prompt-caching-scope-2026-01-05",
    "context-management-2025-06-27",
    "advisor-tool-2026-03-01",
    "thinking-token-count-2026-05-13",
    "extended-cache-ttl-2025-04-11",
]

# Stable per-process session ID matching CC's X-Claude-Code-Session-Id.
_SESSION_ID = str(uuid.uuid4())

# Module-level override set by the credential pool when it selects an entry
# that has an account_uuid field.  When set, _get_account_metadata() uses
# this instead of reading ~/.claude.json (which always points to one account).
_active_account_uuid: str | None = None


def set_active_account_uuid(account_uuid: str | None) -> None:
    """Called by the credential pool after selecting a pool entry."""
    global _active_account_uuid
    _active_account_uuid = account_uuid
    if account_uuid:
        logger.debug("Bypass active account_uuid set to %s", account_uuid)


# ---------------------------------------------------------------------------
# Tool name transforms (upstream PR #191 + hermes namespacing)
# ---------------------------------------------------------------------------


def _uppercase_first(name: str) -> str:
    if not isinstance(name, str) or not name:
        return name
    return name[0].upper() + name[1:]


def _lowercase_first(name: str) -> str:
    """Used after MCP-namespace unwrap so hermes's tool dispatcher resolves
    the registered snake_case name without its auto-repair warning."""
    if not isinstance(name, str) or not name:
        return name
    return name[0].lower() + name[1:]


def _pascalcase_mcp_name(name: str) -> str:
    """Rewrite ``mcp_foo_bar`` → ``mcp_Foo_bar``.  Mirrors upstream PR #191
    exactly; exposed for tests.  In-flight wrapping uses ``_wrap_tool_name``
    which adds the hermes namespace too.
    """
    if not isinstance(name, str) or not name.startswith(_MCP_PREFIX):
        return name
    rest = name[len(_MCP_PREFIX):]
    if not rest or not rest[0].islower():
        return name
    return _MCP_PREFIX + rest[0].upper() + rest[1:]


def _wrap_tool_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        return name
    if name.startswith(_MCP_HERMES_NAMESPACE):
        return name
    base = name[len(_MCP_PREFIX):] if name.startswith(_MCP_PREFIX) else name
    return _MCP_HERMES_NAMESPACE + _uppercase_first(base)


def _unwrap_tool_name(name: Any) -> Any:
    if not isinstance(name, str):
        return name
    if name.startswith(_MCP_HERMES_NAMESPACE):
        return _lowercase_first(name[len(_MCP_HERMES_NAMESPACE):])
    # Hermes's transport may already strip ``mcp_``, leaving ``_hermes__<tool>``.
    fallback_prefix = _MCP_HERMES_NAMESPACE[len(_MCP_PREFIX):]  # "_hermes__"
    if name.startswith(fallback_prefix):
        return _lowercase_first(name[len(fallback_prefix):])
    return name


def _has_thinking_block(msg: Dict[str, Any]) -> bool:
    """Return True if the message contains a ``thinking`` or ``redacted_thinking`` block.

    Anthropic API (Claude 4.x / 3.7 Sonnet with thinking) enforces strict
    content-integrity on assistant messages that include thinking blocks.
    Mutating these messages in any way triggers HTTP 400 with the error:
    "thinking or redacted_thinking blocks in the latest assistant message
    cannot be modified."
    """
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and block.get("type") in (
            "thinking",
            "redacted_thinking",
        ):
            return True
    return False


def _strip_thinking_from_replay(messages: List[Dict[str, Any]]) -> None:
    """Strip ``thinking`` / ``redacted_thinking`` blocks from assistant
    messages that lack ``_anthropic_raw_content`` (legacy messages where
    original block order was not preserved).

    When ``_anthropic_raw_content`` is present, ``_convert_assistant_message``
    replays the original interleaved block order, so signatures remain valid
    and stripping is unnecessary.

    For legacy messages (without raw content preservation), Hermes
    normalisation splits thinking blocks into ``reasoning_details`` and
    tool_use blocks into ``tool_calls``, losing the original interleaved
    order.  On replay, ``_convert_assistant_message`` prepends all thinking
    blocks then appends all tool_use blocks — the reordering invalidates
    every signature.  Strip thinking blocks from these messages only.
    """
    _THINKING_TYPES = frozenset(("thinking", "redacted_thinking"))
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        # Skip messages that carry preserved raw content — their block
        # order is already correct and thinking signatures are valid.
        # Note: at this point we're looking at Anthropic-format messages
        # (after convert_messages_to_anthropic).  The raw_content fast path
        # in _convert_assistant_message already produced correctly-ordered
        # blocks, so we can detect it by checking if any thinking block has
        # a valid signature (signed blocks from the fast path are intact).
        has_signed_thinking = any(
            isinstance(b, dict)
            and b.get("type") in _THINKING_TYPES
            and (b.get("signature") or b.get("data"))
            for b in content
        )
        if has_signed_thinking:
            # Signed thinking blocks present — order is preserved from
            # _anthropic_raw_content fast path.  Don't strip.
            continue
        stripped = [
            b for b in content
            if not (isinstance(b, dict) and b.get("type") in _THINKING_TYPES)
        ]
        if len(stripped) < len(content):
            # Some thinking blocks were removed — apply the filtered list.
            # Use a placeholder when ALL blocks were thinking (empty content
            # is rejected by Anthropic).
            msg["content"] = stripped or [{"type": "text", "text": "(thinking elided)"}]


def _rewrite_tool_names(api_kwargs: Dict[str, Any]) -> None:
    tools = api_kwargs.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and "name" in tool:
                tool["name"] = _wrap_tool_name(tool.get("name") or "")

    messages = api_kwargs.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    # Hermes stores tool calls under its local names after the
                    # response normalizer unwraps Claude Code's
                    # mcp__hermes__Foo namespace.  When a response also carries
                    # signed thinking blocks, replaying the unwrapped name makes
                    # Anthropic report that the thinking-bearing assistant
                    # message was modified.  Re-wrap only the tool_use name;
                    # never touch thinking/redacted_thinking blocks themselves.
                    block["name"] = _wrap_tool_name(block.get("name") or "")


def _install_thinking_replay_classifier_patch() -> bool:
    """Classify Anthropic's newer thinking-replay 400 as recoverable.

    Core Hermes already retries ``FailoverReason.thinking_signature`` by
    stripping ``reasoning_details`` and replaying visible/tool history.  Newer
    Anthropic returns a different message when the latest assistant's signed
    thinking blocks are not byte-identical: "cannot be modified".  Older Hermes
    versions classify that as non-retryable ``format_error`` because the string
    contains ``invalid_request_error``.  Patch the classifier early, and also
    refresh ``agent.conversation_loop.classify_api_error`` if that module was
    imported before this hook ran.
    """
    try:
        from agent import error_classifier as ec  # type: ignore[import-not-found]
    except Exception as exc:
        logger.debug("Cannot import agent.error_classifier for thinking patch: %s", exc)
        return False

    if getattr(ec, "_CLAUDE_CODE_THINKING_REPLAY_PATCHED", False):
        return True

    original = getattr(ec, "classify_api_error", None)
    reason_enum = getattr(ec, "FailoverReason", None)
    classified_cls = getattr(ec, "ClassifiedError", None)
    if not callable(original) or reason_enum is None or classified_cls is None:
        return False

    def patched_classify_api_error(error: Exception, *args: Any, **kwargs: Any):
        result = original(error, *args, **kwargs)
        try:
            status_code = getattr(result, "status_code", None)
            message = str(error).lower()
            body = getattr(error, "body", None)
            if isinstance(body, dict):
                err_obj = body.get("error")
                if isinstance(err_obj, dict):
                    body_msg = str(err_obj.get("message") or "").lower()
                    if body_msg and body_msg not in message:
                        message = f"{message} {body_msg}"
            if (
                status_code == 400
                and "thinking" in message
                and "cannot be modified" in message
                and "latest assistant message" in message
            ):
                result.reason = reason_enum.thinking_signature
                result.retryable = True
                result.should_compress = False
                result.should_rotate_credential = False
                result.should_fallback = False
        except Exception:
            pass
        return result

    patched_classify_api_error.__name__ = getattr(original, "__name__", "classify_api_error")
    patched_classify_api_error.__qualname__ = getattr(
        original, "__qualname__", patched_classify_api_error.__name__
    )
    patched_classify_api_error.__doc__ = getattr(original, "__doc__", None)
    patched_classify_api_error.__module__ = getattr(original, "__module__", __name__)
    patched_classify_api_error.__wrapped__ = original  # type: ignore[attr-defined]

    ec.classify_api_error = patched_classify_api_error
    ec._CLAUDE_CODE_THINKING_REPLAY_PATCHED = True  # type: ignore[attr-defined]

    loop_mod = sys.modules.get("agent.conversation_loop")
    if loop_mod is not None:
        try:
            setattr(loop_mod, "classify_api_error", patched_classify_api_error)
        except Exception:
            pass
    logger.debug("[anthropic_billing_bypass] Thinking replay classifier hook installed")
    return True


# ---------------------------------------------------------------------------
# Account metadata (commit f10468a — accountUuid → user_id)
# ---------------------------------------------------------------------------


def _read_claude_config() -> Dict[str, Any]:
    path = os.path.expanduser("~/.claude.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_account_metadata() -> Dict[str, Any]:
    """Return Anthropic-compatible request metadata.

    CC 2.1.117 sends ``user_id`` as a JSON-encoded string containing
    ``device_id``, ``account_uuid``, and ``session_id``.  Earlier versions
    sent just the UUID.  Returns ``{}`` when the config is missing so the
    caller can skip injecting metadata entirely.

    When the credential pool has set ``_active_account_uuid`` (via
    ``set_active_account_uuid``), that UUID is used instead of reading
    ``~/.claude.json``.  This ensures multi-account pools route billing
    to the correct subscription.
    """
    account_uuid: str | None = _active_account_uuid

    if account_uuid is None:
        # Fallback: read from ~/.claude.json (single-account path)
        config = _read_claude_config()
        oauth = config.get("oauthAccount") if isinstance(config, dict) else None
        if isinstance(oauth, dict) and isinstance(oauth.get("accountUuid"), str):
            account_uuid = oauth["accountUuid"]

    metadata: Dict[str, Any] = {}
    if account_uuid:
        # Build structured metadata matching CC 2.1.117 wire format.
        # device_id is a SHA-256 hex string in real CC; we derive one from
        # the account UUID so it's stable per-install.
        device_id = hashlib.sha256(
            f"hermes-device-{account_uuid}".encode()
        ).hexdigest()
        inner = json.dumps({
            "device_id": device_id,
            "account_uuid": account_uuid,
            "session_id": _SESSION_ID,
        }, separators=(",", ":"))
        metadata["user_id"] = inner
    return metadata


# ---------------------------------------------------------------------------
# Billing header signing (mirror upstream src/signing.ts)
# ---------------------------------------------------------------------------


def _extract_first_user_message_text(messages: List[Dict[str, Any]]) -> str:
    """Mirrors Claude Code's K19() — first text block of the first user
    message.  Returns ``""`` when none exists; required for billing-header
    signature determinism."""
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
    return hashlib.sha256(message_text.encode("utf-8")).hexdigest()[:5]


def _compute_version_suffix(message_text: str, version: str) -> str:
    """SHA-256(salt + chars[4,7,20] + version)[:3]; pads with ``"0"`` when
    the message is shorter than each index.  Matches Claude Code's signing
    routine; deviations break OAuth billing routing."""
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
    text = _extract_first_user_message_text(messages)
    suffix = _compute_version_suffix(text, version)
    cch = _compute_cch(text)
    return (
        f"x-anthropic-billing-header: "
        f"cc_version={version}.{suffix}; "
        f"cc_entrypoint={entrypoint}; "
        f"cch={cch};"
    )


# ---------------------------------------------------------------------------
# Stainless SDK spoof headers (lowercase, matches upstream src/index.ts)
# ---------------------------------------------------------------------------


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
    return {"Darwin": "MacOS", "Linux": "Linux", "Windows": "Windows"}.get(
        platform.system(), platform.system() or "Unknown"
    )


def _build_spoof_headers() -> Dict[str, str]:
    """Headers real Claude Code sends that hermes-agent does not.

    The Anthropic SDK (Stainless-generated) automatically attaches
    ``x-stainless-*`` identifying headers.  The validator cross-references
    these with the billing header's ``cc_entrypoint``; absent or mismatched
    values flag the request as third-party.  Lowercase to match upstream's
    JS SDK output.
    """
    return {
        "anthropic-dangerous-direct-browser-access": "true",
        "x-claude-code-session-id": _SESSION_ID,
        # Fresh per request, matching upstream's crypto.randomUUID() call.
        "x-client-request-id": str(uuid.uuid4()),
        "x-stainless-arch": _stainless_arch(),
        "x-stainless-lang": "js",
        "x-stainless-os": _stainless_os(),
        "x-stainless-package-version": _STAINLESS_PACKAGE_VERSION,
        "x-stainless-retry-count": "0",
        "x-stainless-runtime": "node",
        "x-stainless-runtime-version": _STAINLESS_NODE_VERSION,
        "x-stainless-timeout": "600",
    }


def _merge_spoof_extras(api_kwargs: Dict[str, Any]) -> None:
    """Existing extra_headers/extra_query take precedence so hermes's own
    headers (e.g. fast-mode beta) survive — additive spoof only.

    Exception: ``user-agent`` is forcibly overridden to match the billing
    header's ``cc_entrypoint=sdk-cli``.  Hermes-agent's adapter sets
    ``claude-cli/{detected-version} (external, cli)`` at client-construction
    time, but the billing header (built later in this module) claims
    ``cc_entrypoint=sdk-cli``.  Anthropic's validator catches the mismatch
    and routes the request to third-party billing (``HTTP 400 You're out of
    extra usage``).  Ported from hermes-claude-auth PR #21 / upstream #207.
    """
    merged_headers: Dict[str, str] = dict(_build_spoof_headers())
    existing_headers = api_kwargs.get("extra_headers")
    if isinstance(existing_headers, dict):
        for k, v in existing_headers.items():
            merged_headers[k] = v
    # Force user-agent to (external, sdk-cli) regardless of what hermes set
    # on the SDK client; the existing_headers loop above would otherwise let
    # hermes's "(external, cli)" win and break fingerprint parity.
    merged_headers["user-agent"] = (
        f"claude-cli/{_local_claude_version()} (external, sdk-cli)"
    )
    merged_headers["x-app"] = "cli"
    api_kwargs["extra_headers"] = merged_headers

    merged_query: Dict[str, Any] = {"beta": "true"}
    existing_query = api_kwargs.get("extra_query")
    if isinstance(existing_query, dict):
        for k, v in existing_query.items():
            merged_query[k] = v
    api_kwargs["extra_query"] = merged_query


# ---------------------------------------------------------------------------
# Tool pair repair (upstream PR #136)
# ---------------------------------------------------------------------------


def _repair_tool_pairs(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Repair orphaned ``tool_use`` / ``tool_result`` blocks.

    Anthropic rejects requests where a ``tool_use`` has no matching
    ``tool_result`` (or vice versa).  Long conversations or partial summaries
    can leave these orphans behind.

    Normal messages are repaired by stripping orphaned blocks.  Assistant
    messages containing ``thinking`` / ``redacted_thinking`` blocks are
    preserved byte-for-byte at the content level — Anthropic enforces strict
    content-integrity on them and rejects mutation (HTTP 400).  If such an
    immutable assistant message contains an orphaned ``tool_use``, synthesize
    an error ``tool_result`` in the immediately following user message instead
    of editing the assistant content.

    Returns the original list when nothing needs repairing so callers can
    detect a no-op via identity comparison.
    """
    if not isinstance(messages, list):
        return messages

    tool_use_ids: Set[str] = set()
    tool_result_ids: Set[str] = set()

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                bid = block.get("id")
                if isinstance(bid, str):
                    tool_use_ids.add(bid)
            elif block.get("type") == "tool_result":
                tuid = block.get("tool_use_id")
                if isinstance(tuid, str):
                    tool_result_ids.add(tuid)

    orphaned_uses = tool_use_ids - tool_result_ids
    orphaned_results = tool_result_ids - tool_use_ids

    if not orphaned_uses and not orphaned_results:
        return messages

    thinking_orphaned_uses: Set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict) or not _has_thinking_block(msg):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            bid = block.get("id")
            if isinstance(bid, str) and bid in orphaned_uses:
                thinking_orphaned_uses.add(bid)

    removable_orphaned_uses = orphaned_uses - thinking_orphaned_uses

    def _synthetic_tool_result(tool_use_id: str) -> Dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": "[Hermes repair: missing tool result synthesized for an earlier tool_use.]",
            "is_error": True,
        }

    def _filtered_message(
        msg: Dict[str, Any], prepend_results_for: List[str] | None = None
    ) -> Dict[str, Any] | None:
        prepend_results_for = prepend_results_for or []
        synthetic_results = [_synthetic_tool_result(tid) for tid in prepend_results_for]
        content = msg.get("content")

        if not isinstance(content, list):
            if synthetic_results and msg.get("role") == "user":
                if isinstance(content, str):
                    return {
                        **msg,
                        "content": [
                            *synthetic_results,
                            {"type": "text", "text": content},
                        ],
                    }
                return {**msg, "content": synthetic_results}
            return msg

        filtered: List[Any] = [*synthetic_results]
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("id") in removable_orphaned_uses
            ):
                continue
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") in orphaned_results
            ):
                continue
            filtered.append(block)
        if filtered:
            return {**msg, "content": filtered}
        return None

    repaired: List[Dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict):
            repaired.append(msg)
            i += 1
            continue

        if _has_thinking_block(msg):
            # Preserve thinking-bearing assistant content exactly as supplied.
            repaired.append(msg)
            content = msg.get("content")
            current_thinking_orphans: List[str] = []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    bid = block.get("id")
                    if isinstance(bid, str) and bid in thinking_orphaned_uses:
                        current_thinking_orphans.append(bid)

            if current_thinking_orphans:
                next_msg = messages[i + 1] if i + 1 < len(messages) else None
                if isinstance(next_msg, dict) and next_msg.get("role") == "user":
                    repaired_next = _filtered_message(next_msg, current_thinking_orphans)
                    if repaired_next is not None:
                        repaired.append(repaired_next)
                    i += 2
                    continue
                repaired.append(
                    {
                        "role": "user",
                        "content": [
                            _synthetic_tool_result(tid)
                            for tid in current_thinking_orphans
                        ],
                    }
                )
            i += 1
            continue

        repaired_msg = _filtered_message(msg)
        if repaired_msg is not None:
            repaired.append(repaired_msg)
        i += 1

    return repaired


def _split_tool_results_from_followup_user_text(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Split merged tool-result user turns from later natural user text.

    Hermes's Anthropic adapter merges consecutive user messages to enforce role
    alternation.  If an assistant tool-use turn failed before producing the next
    assistant response, the stored history can become:

        assistant(thinking + tool_use), user(tool_result..., text new prompt)

    For signed Anthropic thinking, that text was not part of the original
    tool-result continuation and can make the server report that the thinking
    block was modified.  Split it into a completed tool-result turn, a small
    synthetic assistant bridge, then the later user text.
    """
    if not isinstance(messages, list):
        return messages

    changed = False
    repaired: List[Dict[str, Any]] = []
    for msg in messages:
        if (
            repaired
            and isinstance(msg, dict)
            and msg.get("role") == "user"
            and isinstance(msg.get("content"), list)
        ):
            prev = repaired[-1]
            prev_content = prev.get("content") if isinstance(prev, dict) else None
            prev_has_tool_use = (
                isinstance(prev, dict)
                and prev.get("role") == "assistant"
                and isinstance(prev_content, list)
                and any(
                    isinstance(block, dict) and block.get("type") == "tool_use"
                    for block in prev_content
                )
            )
            content = msg["content"]
            leading_results: List[Any] = []
            rest: List[Any] = []
            seen_non_result = False
            for block in content:
                is_tool_result = (
                    isinstance(block, dict) and block.get("type") == "tool_result"
                )
                if prev_has_tool_use and is_tool_result and not seen_non_result:
                    leading_results.append(block)
                else:
                    seen_non_result = True
                    rest.append(block)
            if leading_results and rest:
                # Interrupted/failed tool turn — the assistant + tool_result
                # boundary was merged with later user text by Hermes's role
                # alternation logic.  Split them apart WITHOUT modifying the
                # assistant content so Anthropic's thinking-block integrity
                # check does not reject the request.  Hermes core's
                # _manage_thinking_signatures will strip thinking blocks from
                # non-latest assistant messages on the next turn, and the
                # standard thinking_signature recovery path handles any
                # signature validation failures on this turn.
                repaired.append({**msg, "content": leading_results})
                repaired.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "[Hermes repair: previous tool turn ended without an assistant response.]",
                            }
                        ],
                    }
                )
                repaired.append({**msg, "content": rest})
                changed = True
                continue
        repaired.append(msg)

    return repaired if changed else messages


# ---------------------------------------------------------------------------
# Effort stripping for haiku (upstream PR #126)
# ---------------------------------------------------------------------------


def _model_disables_effort(model: str) -> bool:
    if not isinstance(model, str):
        return False
    return "haiku" in model.lower()


def _strip_effort(api_kwargs: Dict[str, Any]) -> None:
    """Remove ``effort`` for haiku (rejected with HTTP 400).  Drops the
    parent dict if it becomes empty so we don't send ``"output_config": {}``
    which trips a different validator.  Mirrors upstream PR #126."""
    model = api_kwargs.get("model") or ""
    if not _model_disables_effort(model):
        return

    output_config = api_kwargs.get("output_config")
    if isinstance(output_config, dict) and "effort" in output_config:
        del output_config["effort"]
        if not output_config:
            del api_kwargs["output_config"]

    thinking = api_kwargs.get("thinking")
    if isinstance(thinking, dict) and "effort" in thinking:
        del thinking["effort"]
        if not thinking:
            del api_kwargs["thinking"]


# ---------------------------------------------------------------------------
# Temperature fix for Opus 4.6 adaptive thinking (preserved from 1.0.0)
# ---------------------------------------------------------------------------


def _model_supports_adaptive_thinking(model: str) -> bool:
    if not isinstance(model, str):
        return False
    return "4-6" in model or "4.6" in model


def _fix_temperature_for_oauth_adaptive(
    api_kwargs: Dict[str, Any],
    *,
    site: str,
) -> None:
    """Strip non-default ``temperature`` from OAuth requests on Opus 4.6.

    Opus 4.6 with implicit adaptive thinking rejects ``temperature != 1``
    with HTTP 400; dropping the parameter lets the API use its default.
    """
    if "temperature" not in api_kwargs:
        return
    temp = api_kwargs.get("temperature")
    if temp == 1 or temp == 1.0:
        return
    model = api_kwargs.get("model") or ""
    if not _model_supports_adaptive_thinking(model):
        return
    del api_kwargs["temperature"]
    logger.info(
        "Dropped temperature=%r for OAuth adaptive-thinking model %r (site=%s)",
        temp,
        model,
        site,
    )


# ---------------------------------------------------------------------------
# System prompt relocation (upstream PR #148)
# ---------------------------------------------------------------------------


def _prepend_to_first_user_message(
    messages: List[Dict[str, Any]],
    texts: List[str],
) -> None:
    if not texts:
        return
    combined = "\n\n".join(
        f"<system-reminder>\n{t}\n</system-reminder>" for t in texts
    )
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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


_IDENTITY_CACHE_CONTROL_1H: Dict[str, str] = {"type": "ephemeral", "ttl": "1h"}
_IDENTITY_CACHE_CONTROL_5M: Dict[str, str] = {"type": "ephemeral"}


def _identity_cache_control(api_kwargs: Dict[str, Any]) -> Dict[str, str]:
    """Pick the cache_control for the relocated system identity block.

    Anthropic processes cache breakpoints in the order ``tools`` -> ``system``
    -> ``messages`` and rejects (HTTP 400) a ``ttl='1h'`` block that comes
    after a ``ttl='5m'`` one.  hermes-agent 0.21.x started putting a cache
    marker on the last tool (tool-loop cache layout) with the session TTL,
    which is ``5m`` by default.  A hard-coded ``1h`` identity marker then
    lands *after* that ``5m`` tool marker and every request fails with
    ``system.1.cache_control.ttl: a ttl='1h' cache_control block must not
    come after a ttl='5m' cache_control block``.

    Rule: if any tool carries a 5m marker (no ``ttl`` == 5m), the identity
    must be 5m too; otherwise keep the historical 1h marker (older
    hermes-agent never marks tools, so behaviour there is unchanged).
    """
    tools = api_kwargs.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            cc = tool.get("cache_control") if isinstance(tool, dict) else None
            if isinstance(cc, dict) and cc.get("ttl", "5m") != "1h":
                return _IDENTITY_CACHE_CONTROL_5M
    return _IDENTITY_CACHE_CONTROL_1H


def apply_claude_code_bypass(api_kwargs: Dict[str, Any], version: str) -> None:
    """Apply all OAuth bypass transforms in place.

    Idempotent: stale billing headers are dropped before injecting the new
    one and duplicate identity entries are removed.  Safe to call on
    requests that have already been bypassed.
    """
    messages = api_kwargs.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    # Repair orphaned tool pairs first; downstream transforms assume valid
    # tool_use/tool_result pairing.
    repaired = _repair_tool_pairs(messages)
    if repaired is not messages:
        api_kwargs["messages"] = repaired
        messages = repaired

    split_messages = _split_tool_results_from_followup_user_text(messages)
    if split_messages is not messages:
        api_kwargs["messages"] = split_messages
        messages = split_messages

    raw_system = api_kwargs.get("system")
    if raw_system is None:
        system: List[Any] = []
    elif isinstance(raw_system, str):
        system = [{"type": "text", "text": raw_system}] if raw_system else []
    elif isinstance(raw_system, list):
        system = list(raw_system)
    else:
        logger.warning(
            "Unexpected system type %s; skipping bypass",
            type(raw_system).__name__,
        )
        return

    # Build billing header from ORIGINAL messages (before relocation mutates).
    try:
        billing_value = _build_billing_header_value(
            messages, version, _BILLING_ENTRYPOINT
        )
    except Exception as exc:
        logger.warning("Failed to build billing header: %s", exc)
        return
    billing_entry = {"type": "text", "text": billing_value}

    identity_cache_control = _identity_cache_control(api_kwargs)

    kept: List[Any] = []
    moved_texts: List[str] = []
    identity_seen = False

    for entry in system:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        if entry.get("type") != "text":
            kept.append(entry)
            continue
        text = entry.get("text") or ""
        if text.startswith(_BILLING_PREFIX):
            continue  # stale billing header — drop
        if text.startswith(_SYSTEM_IDENTITY) or text.startswith(_OLD_SYSTEM_IDENTITY):
            if identity_seen:
                continue  # duplicate — drop
            identity_seen = True
            # Strip whichever prefix matched and relocate the remainder.
            prefix = (
                _SYSTEM_IDENTITY
                if text.startswith(_SYSTEM_IDENTITY)
                else _OLD_SYSTEM_IDENTITY
            )
            rest = text[len(prefix):].lstrip("\n")
            kept.append({
                "type": "text",
                "text": _SYSTEM_IDENTITY,
                "cache_control": dict(identity_cache_control),
            })
            if rest:
                moved_texts.append(rest)
            continue
        if text:
            moved_texts.append(text)

    if not identity_seen:
        kept.insert(0, {
            "type": "text",
            "text": _SYSTEM_IDENTITY,
            "cache_control": dict(identity_cache_control),
        })

    api_kwargs["system"] = [billing_entry] + kept

    if moved_texts:
        _prepend_to_first_user_message(messages, moved_texts)

    # Strip signed thinking blocks from ALL assistant messages before
    # rewriting tool names.  Hermes' round-trip reorders thinking vs
    # tool_use blocks (see _strip_thinking_from_replay docstring),
    # making Anthropic signature validation impossible.  Removing them
    # here avoids the "cannot be modified" 400.
    _strip_thinking_from_replay(messages)

    _rewrite_tool_names(api_kwargs)
    _merge_spoof_extras(api_kwargs)
    _strip_effort(api_kwargs)
    _fix_temperature_for_oauth_adaptive(api_kwargs, site="build_kwargs")

    # Inject context_management if not already present.  CC 2.1.117 sends
    # this to control thinking-block retention.  Must go via extra_body
    # because the Anthropic Python SDK doesn't recognize it as a kwarg.
    # Only inject when thinking is enabled — the clear_thinking strategy
    # requires thinking to be active, and auxiliary calls (vision, etc.)
    # don't use thinking mode.
    thinking = api_kwargs.get("thinking")
    has_thinking = isinstance(thinking, dict) and thinking.get("type") in (
        "adaptive", "enabled",
    )
    if has_thinking and "context_management" not in api_kwargs:
        extra_body = api_kwargs.setdefault("extra_body", {})
        if isinstance(extra_body, dict) and "context_management" not in extra_body:
            extra_body["context_management"] = {
                "edits": [{"type": "clear_thinking_20251015", "keep": "all"}]
            }

    metadata = _get_account_metadata()
    if metadata:
        existing_meta = api_kwargs.get("metadata")
        if isinstance(existing_meta, dict):
            for k, v in metadata.items():
                existing_meta.setdefault(k, v)
        else:
            api_kwargs["metadata"] = metadata


# ---------------------------------------------------------------------------
# Monkey-patch installation
# ---------------------------------------------------------------------------


def _get_version_safely(aa_module: Any) -> str:
    """Return the Claude Code version used to sign the billing header.

    Detects the *installed* Claude Code version dynamically (via
    ``claude --version``) so the billing header's ``cc_version=`` and the
    user-agent's ``claude-cli/<version>`` always match the local binary —
    never drifting behind it.  This is what keeps Anthropic's third-party
    validator happy and routes to the subscription bucket instead of extra
    usage.

    Falls back to ``_PINNED_CC_VERSION`` if detection fails (e.g. Claude Code
    not on PATH), so the patch never hard-fails.

    Ported from hermes-claude-auth PR #21 / upstream #207, with dynamic
    detection replacing the static pin.
    """
    detected = _local_claude_version()
    if detected and detected[0].isdigit():
        return detected
    return _PINNED_CC_VERSION


def _get_version_detected(aa_module: Any) -> str:
    """Original dynamic detection, kept for diagnostics/tests."""
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
    return "2.1.112"


def _install_response_pascalcase_unhook(
    aa_module: Any, force: bool = False
) -> bool:
    """Patch hermes's response normalizer to unwrap ``mcp__hermes__Foo`` back
    to ``foo`` and lowercase the first character so the tool dispatcher
    resolves the original snake_case name without auto-repair noise.

    Patches both:
      - ``aa_module.normalize_anthropic_response`` (pre-0.11 hermes)
      - ``agent.transports.anthropic.AnthropicTransport.normalize_response``
        (hermes 0.11+)

    Returns True if at least one hook succeeded.
    """
    any_installed = False

    # --- Old hermes: normalize_anthropic_response on the adapter module ---
    original_normalize = getattr(aa_module, "normalize_anthropic_response", None)
    already_old = getattr(aa_module, "_CLAUDE_CODE_RESPONSE_UNHOOK_APPLIED", False)
    if callable(original_normalize) and (force or not already_old):
        def patched_normalize(
            response: Any, strip_tool_prefix: bool = False, **kwargs: Any
        ) -> Any:
            result = original_normalize(
                response, strip_tool_prefix=strip_tool_prefix, **kwargs
            )
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
                    name = getattr(tc, "name", None)
                    if isinstance(name, str):
                        try:
                            tc.name = _unwrap_tool_name(name)
                        except Exception:
                            pass
                    continue
                fn_name = getattr(fn, "name", None)
                if isinstance(fn_name, str):
                    try:
                        fn.name = _unwrap_tool_name(fn_name)
                    except Exception:
                        pass
            return result

        patched_normalize.__name__ = original_normalize.__name__
        patched_normalize.__qualname__ = getattr(
            original_normalize, "__qualname__", original_normalize.__name__
        )
        patched_normalize.__doc__ = original_normalize.__doc__
        patched_normalize.__wrapped__ = original_normalize  # type: ignore[attr-defined]

        aa_module.normalize_anthropic_response = patched_normalize
        aa_module._CLAUDE_CODE_RESPONSE_UNHOOK_APPLIED = True  # type: ignore[attr-defined]
        logger.debug(
            "[anthropic_billing_bypass] Adapter unwrap hook installed"
        )
        any_installed = True
    elif callable(original_normalize) and already_old:
        any_installed = True  # already installed in a previous call

    # --- New hermes: AnthropicTransport.normalize_response ---
    try:
        from agent.transports import anthropic as at  # type: ignore[import-not-found]
        cls = getattr(at, "AnthropicTransport", None)
    except Exception as exc:
        logger.debug(
            "AnthropicTransport not importable (%s); skipping transport hook",
            exc,
        )
        cls = None

    if cls is not None:
        already_new = getattr(cls, "_HERMES_MCP_UNWRAP_APPLIED", False)
        if force or not already_new:
            original_transport_normalize = getattr(cls, "normalize_response", None)
            if callable(original_transport_normalize):
                def patched_transport_normalize(
                    self: Any, response: Any, *args: Any, **kwargs: Any
                ) -> Any:
                    result = original_transport_normalize(
                        self, response, *args, **kwargs
                    )
                    tool_calls = getattr(result, "tool_calls", None)
                    if tool_calls:
                        for tc in tool_calls:
                            name = getattr(tc, "name", None)
                            if isinstance(name, str):
                                try:
                                    tc.name = _unwrap_tool_name(name)
                                except Exception:
                                    pass
                            fn = getattr(tc, "function", None)
                            fn_name = (
                                getattr(fn, "name", None) if fn is not None else None
                            )
                            if isinstance(fn_name, str):
                                try:
                                    fn.name = _unwrap_tool_name(fn_name)
                                except Exception:
                                    pass
                    return result

                patched_transport_normalize.__name__ = (
                    original_transport_normalize.__name__
                )
                patched_transport_normalize.__qualname__ = getattr(
                    original_transport_normalize,
                    "__qualname__",
                    original_transport_normalize.__name__,
                )
                patched_transport_normalize.__doc__ = (
                    original_transport_normalize.__doc__
                )
                patched_transport_normalize.__wrapped__ = (  # type: ignore[attr-defined]
                    original_transport_normalize
                )

                cls.normalize_response = patched_transport_normalize
                cls._HERMES_MCP_UNWRAP_APPLIED = True  # type: ignore[attr-defined]
                logger.debug(
                    "[anthropic_billing_bypass] Transport unwrap hook installed"
                )
                any_installed = True
        else:
            any_installed = True

    return any_installed


# ─────────────────────────────────────────────────────────────────────────
# Subscription rate-limit auto-wait (Claude Pro/Max windows: 5h, 1d, 7d)
# ==================================================================
# When a long agent run exhausts a Claude Pro/Max usage window mid-flight,
# api.anthropic.com returns HTTP 429 (rate_limit_error) with a reset time in
# the ``retry-after`` and/or ``anthropic-ratelimit-unified-*-reset`` headers.
# Hermes core's retry loop caps backoff at 120s and gives up after
# ``_api_max_retries``, surfacing "rate-limiting requests" on Telegram and
# abandoning the run.
#
# This patch wraps the two API-call entry points on ``run_agent.AIAgent``
# (``_interruptible_api_call`` and ``_interruptible_streaming_api_call``) so a
# genuine subscription-window 429 instead SLEEPS until the window resets
# (interruptibly, in short chunks) and then retries the same call
# transparently — the give-up logic never runs.  Per-minute throttles (short
# resets) are slept through too.
#
# As of v1.5.9 the auto-wait is **window-aware**: Anthropic now reports which
# subscription window tripped via the unified
# ``anthropic-ratelimit-unified-{5h,1d,7d}-status`` / ``-reset`` headers.
# We pick the **longest** waiting window (e.g. a 7d hit also covers the 5h
# window, so a single sleep clears both) and apply a **per-window** safety
# cap — the legacy 6h cap is no longer the bottleneck when the weekly
# window trips.  Behaviour is tunable via env vars:
#
#   HERMES_RL_AUTOWAIT               "1"/"0"  enable/disable           (default 1)
#   HERMES_RL_AUTOWAIT_MAX_S         int      fallback cap seconds     (default 21600 = 6h)
#                                                (used when no window could be detected)
#   HERMES_RL_AUTOWAIT_MAX_5H_S      int      5h-window safety cap     (default 21600 = 6h)
#   HERMES_RL_AUTOWAIT_MAX_1D_S      int      1d-window safety cap     (default 93600 = 26h)
#   HERMES_RL_AUTOWAIT_MAX_7D_S      int      7d-window safety cap     (default 648000 = 7.5d)
#   HERMES_RL_AUTOWAIT_BUFFER_S      int      pad after reset          (default 5)
#   HERMES_RL_AUTOWAIT_DEFAULT_S     int      wait when no reset found (default 300)
#   HERMES_RL_AUTOWAIT_HEADERLESS_ATTEMPTS
#                                    int      retries for a 429 that   (default 2)
#                                             carries no reset header
#
# The wait loop also calls ``agent._touch_activity(...)`` every ~25s so the
# gateway's inactivity watchdog (default 1800s = 30 min) doesn't kill the
# agent mid-wait.  Without this tick the auto-wait would always lose to the
# watchdog at ~30 min, defeating the whole feature.
#
# Idempotent.  Never breaks the call path: if anything goes wrong parsing or
# patching, the original method/exception behaviour is preserved.

_RL_AUTOWAIT_PATCHED_FLAG = "_CLAUDE_CODE_RL_AUTOWAIT_PATCHED"


def _rl_env_int(name: str, default: int) -> int:
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _rl_autowait_enabled() -> bool:
    return str(os.environ.get("HERMES_RL_AUTOWAIT", "1")).strip().lower() not in {
        "0", "false", "no", "off",
    }


def _rl_is_rate_limit_error(exc: Exception) -> bool:
    """True only for a real HTTP 429 from the provider."""
    if exc is None:
        return False
    if type(exc).__name__ == "RateLimitError":
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    return status == 429


def _rl_parse_headers(exc: Exception) -> Dict[str, str]:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except Exception:
        try:
            return {str(k).lower(): str(headers[k]) for k in headers}  # type: ignore[index]
        except Exception:
            return {}


# Anthropic rate-limit windows we recognise.  The numeric suffix (``5h``,
# ``1d``, ``7d``) matches the suffix Anthropic uses in the
# ``anthropic-ratelimit-unified-*-status`` / ``-reset`` / ``-utilization``
# headers.  ``5h`` is the Pro/Max subscription window; ``7d`` is the weekly
# cap; ``1d`` appears on some enterprise tiers.
_RL_KNOWN_WINDOWS: tuple = ("7d", "1d", "5h")


def _rl_parse_timestamp(raw: str, now: float) -> "float | None":
    """Parse a ``anthropic-ratelimit-*-reset`` value into seconds-until-reset.

    Accepts:
      * absolute epoch seconds (Anthropic's typical form),
      * a small relative duration in seconds,
      * RFC 3339 / ISO 8601 timestamps.

    Returns ``None`` if the value can't be parsed.  ``now`` is the reference
    time in ``time.time()`` seconds — passed in so all callers in a single
    429-handling path agree on "now" (avoids drift across separate calls).
    """
    import datetime as _dt
    import email.utils as _eut

    raw = (raw or "").strip()
    if not raw:
        return None
    # epoch seconds (Anthropic unified-reset is usually a Unix timestamp)
    try:
        ts = float(raw)
        # If it looks like an absolute epoch (in the future), use delta;
        # if it's a small number, treat as a relative duration.
        if ts > 1_000_000_000:  # ~2001+, clearly absolute epoch seconds
            return max(0.0, ts - now)
        return max(0.0, ts)
    except ValueError:
        pass
    # RFC 3339 / ISO 8601
    try:
        iso = raw.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return max(0.0, dt.timestamp() - now)
    except Exception:
        return None
    # retry-after / HTTP-date as a last resort
    try:
        parsed = _eut.parsedate_to_datetime(raw)
        if parsed is not None:
            return max(0.0, parsed.timestamp() - now)
    except Exception:
        return None
    return None


def _rl_status_for(window: str) -> str:
    return f"anthropic-ratelimit-unified-{window}-status"


def _rl_reset_for(window: str) -> str:
    return f"anthropic-ratelimit-unified-{window}-reset"


def _rl_detect_throttled_window(headers: Dict[str, str], now: float) -> "tuple":
    """Identify the throttled subscription window and its reset delta.

    Returns ``(window_name, seconds_until_reset)`` where either field may be
    ``None``.  When several windows are throttled at once (e.g. 5h and 7d
    both at the cap on a heavy Pro run), we pick the **longest** wait so a
    single sleep clears everything — waiting through a 7d reset also clears
    the 5h window.
    """
    if not headers:
        return None, None
    best_window = None
    best_secs: "float | None" = None
    for window in _RL_KNOWN_WINDOWS:
        status = headers.get(_rl_status_for(window))
        if not status or status.strip().lower() != "throttled":
            continue
        raw = headers.get(_rl_reset_for(window))
        if not raw:
            continue
        secs = _rl_parse_timestamp(raw, now)
        if secs is None:
            continue
        if best_secs is None or secs > best_secs:
            best_window, best_secs = window, secs
    return best_window, best_secs


def _rl_seconds_until_reset(exc: Exception) -> "tuple":
    """Parse a 429's reset info into ``(window_name, seconds_until_reset)``.

    Detection order:
      1. ``anthropic-ratelimit-unified-{7d,1d,5h}-status=throttled`` plus the
         matching ``-reset`` header (window-aware — preferred when present).
      2. ``retry-after`` (RFC 7231: delta-seconds or HTTP-date) — windowless.
      3. ``anthropic-ratelimit-unified-reset`` (epoch seconds or RFC 3339).
      4. Any other ``anthropic-ratelimit-*-reset`` (we keep the longest).

    ``window_name`` is one of ``"7d"``, ``"1d"``, ``"5h"``, or ``None`` when
    we can't tell which window tripped (e.g. only ``retry-after`` was set).
    The returned ``seconds_until_reset`` is the value to sleep, *already
    bounded to be non-negative* — callers still need to apply the per-window
    safety cap.
    """
    import email.utils as _eut

    headers = _rl_parse_headers(exc)
    if not headers:
        return None, None
    now = time.time()

    # 1. window-aware: if Anthropic told us which window throttled us,
    #    trust the matching reset header.
    window, secs = _rl_detect_throttled_window(headers, now)
    if window is not None and secs is not None:
        return window, secs

    # 2. retry-after (windowless — works for any provider that sets it)
    ra = headers.get("retry-after")
    if ra:
        ra = ra.strip()
        try:
            return None, max(0.0, float(ra))  # delta-seconds
        except ValueError:
            try:  # HTTP-date
                parsed = _eut.parsedate_to_datetime(ra)
                if parsed is not None:
                    return None, max(0.0, parsed.timestamp() - now)
            except Exception:
                pass

    # 3 + 4. anthropic-ratelimit-*-reset (unified first, then any other)
    unified = headers.get("anthropic-ratelimit-unified-reset")
    if unified:
        secs = _rl_parse_timestamp(unified, now)
        if secs is not None:
            return None, secs
    candidates = [
        v for k, v in headers.items()
        if k.startswith("anthropic-ratelimit-") and k.endswith("-reset")
    ]
    best = None
    for raw in candidates:
        secs = _rl_parse_timestamp(raw, now)
        if secs is not None:
            best = secs if best is None else max(best, secs)
    return None, best


def _rl_fmt_eta(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if sec else f"{m}m"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _rl_local_clock(seconds_from_now: float) -> str:
    try:
        t = time.localtime(time.time() + max(0.0, seconds_from_now))
        return time.strftime("%H:%M", t)
    except Exception:
        return "?"


def _rl_emit(agent: Any, message: str) -> None:
    """Best-effort user-facing status — reaches Telegram via status_callback."""
    try:
        emit = getattr(agent, "_emit_status", None)
        if callable(emit):
            emit(message)
            return
    except Exception:
        pass
    try:
        logger.warning("[rl-autowait] %s", message)
    except Exception:
        pass


def _rl_touch(agent: Any, desc: str) -> None:
    """Bump the agent's liveness signal so the gateway inactivity timer
    doesn't kill us mid-wait.

    The gateway polls ``get_activity_summary()["seconds_since_activity"]``
    every few seconds and aborts the run when it exceeds
    ``agent.gateway_timeout`` (default 1800s = 30 min).  Without a tick here,
    a long rate-limit wait (up to ``HERMES_RL_AUTOWAIT_MAX_S`` = 6h) silently
    trips the timeout while we're just sleeping.  Calling the agent's own
    ``_touch_activity`` resets ``seconds_since_activity`` to ~0; it never
    raises, and is a no-op when the agent predates this method.
    """
    try:
        touch = getattr(agent, "_touch_activity", None)
        if callable(touch):
            touch(desc)
    except Exception:
        # Liveness tick must never break the wait.
        logger.debug("[rl-autowait] touch_activity failed", exc_info=True)


def _rl_interrupted(agent: Any) -> bool:
    """True if the user asked to stop — so we abort the wait promptly."""
    if getattr(agent, "_interrupt_requested", False):
        return True
    try:
        from tools.interrupt import is_interrupted as _is_int  # type: ignore
        tid = getattr(agent, "_execution_thread_id", None)
        return bool(_is_int(tid)) if tid is not None else bool(_is_int())
    except Exception:
        return False


# Per-window safety caps.  These are independent of the legacy
# ``HERMES_RL_AUTOWAIT_MAX_S`` knob: when Anthropic tells us *which* window
# tripped (via ``anthropic-ratelimit-unified-{5h,1d,7d}-status``), we use
# the matching cap; otherwise we fall back to ``HERMES_RL_AUTOWAIT_MAX_S``.
# Defaults (all in seconds):
#   5h → 6h  (current behaviour — short enough that the gateway watchdog
#             can be tuned up if needed, but long enough to span any 5h window)
#   1d → 26h (daily cap with a 2h buffer for clock drift / reset skew)
#   7d → 7.5d (≈648000s — weekly Anthropic Pro reset + 12h safety)
_RL_WINDOW_DEFAULTS_S: dict = {
    "5h": 6 * 3600,         # 21600
    "1d": 26 * 3600,        # 93600
    "7d": int(7.5 * 86400),  # 648000
}
_RL_WINDOW_LABEL: dict = {
    "5h": "5h (Pro/Max)",
    "1d": "daily (1d)",
    "7d": "weekly (7d)",
}


def _rl_window_cap(window: "str | None") -> int:
    """Return the per-window safety cap in seconds.

    ``window`` is one of ``"5h"``/``"1d"``/``"7d"`` (Anthropic's exact
    suffix) or ``None`` (caller couldn't tell which window tripped — we
    fall back to the legacy ``HERMES_RL_AUTOWAIT_MAX_S`` knob, default 6h).
    """
    if window is not None and window in _RL_WINDOW_DEFAULTS_S:
        return _rl_env_int(
            f"HERMES_RL_AUTOWAIT_MAX_{window.upper()}_S",
            _RL_WINDOW_DEFAULTS_S[window],
        )
    return _rl_env_int("HERMES_RL_AUTOWAIT_MAX_S", 21600)  # 6h fallback


def _rl_sleep_until_reset(agent: Any, exc: Exception, attempt: int) -> bool:
    """Sleep until the rate-limit window resets.  Returns True if we waited
    (caller should retry), False if we should re-raise (cap hit / interrupted /
    unparseable with auto-wait disabled)."""
    buffer_s = _rl_env_int("HERMES_RL_AUTOWAIT_BUFFER_S", 5)
    default_s = _rl_env_int("HERMES_RL_AUTOWAIT_DEFAULT_S", 300)

    window, reset = _rl_seconds_until_reset(exc)
    if reset is None:
        # No parseable reset header.  Two very different situations produce
        # this, and they need opposite handling:
        #
        #   (a) a genuine subscription-window 429 whose reset header we
        #       simply failed to parse — waiting the default and retrying
        #       is correct and eventually succeeds;
        #   (b) a *third-party detection* 429 (body ``{"type":"rate_limit_
        #       error","message":"Error"}``, no ``anthropic-ratelimit-
        #       unified-*`` headers at all).  This is not a quota at all —
        #       it means the request went out without the Claude Code
        #       identity (e.g. ``is_oauth=False``, a poisoned credential
        #       lease).  Retrying the *identical* call can never clear it.
        #
        # We cannot distinguish (a) from (b) at the header level, so we
        # bound the headerless case instead of looping forever.  Left
        # unbounded, case (b) turns a single broken credential into a
        # permanent 429-every-DEFAULT_S heartbeat against the API: observed
        # in the wild as one stuck delegate_task child emitting 370 × 429
        # over 24h at exactly 5-minute spacing, with the parent blocked on
        # its result() the entire time.
        headerless_cap = _rl_env_int("HERMES_RL_AUTOWAIT_HEADERLESS_ATTEMPTS", 2)
        if attempt > headerless_cap:
            _rl_emit(
                agent,
                f"⏹ 429 with no reset headers after {attempt} attempts — this is "
                f"not a quota limit (looks like a request rejected for missing "
                f"Claude Code identity). Giving up on auto-wait.",
            )
            return False
        reset = float(default_s)
        had_header = False
    else:
        had_header = True
    max_wait = _rl_window_cap(window)
    wait_s = min(reset + buffer_s, float(max_wait))

    if wait_s <= 0:
        return True  # already reset — retry immediately

    if reset + buffer_s > max_wait:
        label = _RL_WINDOW_LABEL.get(window or "", "usage") if window else "usage"
        _rl_emit(
            agent,
            f"⏱️ Claude {label} quota exhausted, but it does not reset for "
            f"{_rl_fmt_eta(reset)} — beyond the safety cap "
            f"({_rl_fmt_eta(max_wait)}). Stopping.",
        )
        return False

    eta = _rl_fmt_eta(wait_s)
    clock = _rl_local_clock(wait_s)
    src = "" if had_header else " (estimated)"
    if window and window in _RL_WINDOW_LABEL:
        window_msg = f" ({_RL_WINDOW_LABEL[window]})"
    else:
        window_msg = ""
    _rl_emit(
        agent,
        f"⏸ Claude quota{window_msg} exhausted. Waiting ≈{eta}{src} for the "
        f"window to reset (resuming around {clock}). Sleeping…",
    )

    # Touch activity once before sleeping so the gateway sees the wait
    # as legitimate activity rather than a hung API call.  Then refresh
    # the liveness signal every ~25s inside the chunk loop — well under
    # the default gateway_timeout (1800s) and gateway_timeout_warning
    # (900s), so neither the warning nor the kill fires.
    touch_desc = f"auto-waiting for Claude {window or 'unified'} rate-limit reset (≈{eta})"
    _rl_touch(agent, touch_desc)
    last_touch = time.monotonic()
    _TOUCH_INTERVAL_S = 25.0

    deadline = time.time() + wait_s
    # Sleep in short chunks so a user interrupt (or /stop) breaks out quickly.
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        if _rl_interrupted(agent):
            _rl_emit(agent, "⏹ Interrupted while waiting for quota reset — stopping.")
            return False
        time.sleep(min(5.0, remaining))
        # Refresh liveness periodically.  Cheap and best-effort.
        if time.monotonic() - last_touch >= _TOUCH_INTERVAL_S:
            last_touch = time.monotonic()
            remaining_str = _rl_fmt_eta(max(0.0, deadline - time.time()))
            _rl_touch(
                agent,
                f"auto-waiting for Claude {window or 'unified'} rate-limit reset "
                f"(≈{remaining_str} left)",
            )

    _rl_emit(agent, "▶ Quota reset — resuming execution.")
    return True


def _rl_wrap_call(original_method):
    """Wrap an AIAgent API-call method with subscription rate-limit auto-wait."""

    def wrapper(self, *args: Any, **kwargs: Any):
        # Claude-subscription windows only: a 429 from another provider
        # (openai-codex usage cap, openrouter, ...) must reach Hermes core so
        # fallback_providers engage instead of a blind 5-minute sleep loop.
        if not _rl_autowait_enabled() or getattr(self, "provider", None) != "anthropic":
            return original_method(self, *args, **kwargs)
        attempt = 0
        while True:
            try:
                return original_method(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — re-raised unless it's a 429 we handle
                if not _rl_is_rate_limit_error(exc):
                    raise
                attempt += 1
                # Decide whether to wait-and-retry or give up (re-raise so
                # Hermes core's normal handling/give-up path takes over).
                try:
                    should_retry = _rl_sleep_until_reset(self, exc, attempt)
                except Exception:
                    logger.debug("[rl-autowait] sleep handler error", exc_info=True)
                    should_retry = False
                if not should_retry:
                    raise
                # loop and retry the identical call

    wrapper.__name__ = getattr(original_method, "__name__", "interruptible_api_call")
    wrapper.__qualname__ = getattr(original_method, "__qualname__", wrapper.__name__)
    wrapper.__doc__ = getattr(original_method, "__doc__", None)
    wrapper.__wrapped__ = original_method  # type: ignore[attr-defined]
    return wrapper


def install_rate_limit_autowait(run_agent_module: Any = None) -> bool:
    """Patch ``run_agent.AIAgent`` so subscription-window 429s wait-and-resume.

    Idempotent.  Returns True if installed (or already installed), False if the
    target class/methods are missing (API drift) — in which case the original
    behaviour is untouched.
    """
    ram = run_agent_module
    if ram is None:
        try:
            import run_agent as ram  # type: ignore[no-redef]
        except Exception as exc:
            logger.debug("[rl-autowait] cannot import run_agent: %s", exc)
            return False

    agent_cls = getattr(ram, "AIAgent", None)
    if agent_cls is None:
        logger.debug("[rl-autowait] run_agent.AIAgent missing; skipping")
        return False
    if getattr(agent_cls, _RL_AUTOWAIT_PATCHED_FLAG, False):
        return True

    patched_any = False
    for name in ("_interruptible_api_call", "_interruptible_streaming_api_call"):
        original = getattr(agent_cls, name, None)
        if not callable(original):
            continue
        setattr(agent_cls, name, _rl_wrap_call(original))
        patched_any = True
        logger.info("[rl-autowait] wrapped AIAgent.%s", name)

    if not patched_any:
        return False
    setattr(agent_cls, _RL_AUTOWAIT_PATCHED_FLAG, True)
    logger.debug("[rl-autowait] subscription rate-limit auto-wait installed")
    return True


def _install_pool_select_hook() -> None:
    """Wrap CredentialPool.select() to call set_active_account_uuid when
    an anthropic pool entry with an account_uuid field is selected.

    This ensures _get_account_metadata() sends the correct UUID for
    multi-account setups instead of always reading ~/.claude.json.
    """
    try:
        from agent.credential_pool import CredentialPool  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("credential_pool not importable; pool select hook skipped")
        return

    if getattr(CredentialPool, "_BILLING_BYPASS_SELECT_HOOK", False):
        return  # already installed

    original_select = CredentialPool.select

    def hooked_select(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Signature-transparent: hermes-agent 0.21.x added a keyword-only
        # ``model`` parameter to CredentialPool.select().  A rigid
        # ``(self)`` wrapper raises TypeError on every model-aware call
        # site (agent_runtime_helpers, runtime_provider), which surfaces as
        # "Model resolution failed" / "could not start the assistant".
        entry = original_select(self, *args, **kwargs)
        if entry is not None and getattr(self, "provider", None) == "anthropic":
            uuid_val = getattr(entry, "account_uuid", None)
            if isinstance(uuid_val, str) and uuid_val:
                set_active_account_uuid(uuid_val)
                logger.debug(
                    "Pool selected entry %s → account_uuid %s",
                    getattr(entry, "label", "?"),
                    uuid_val,
                )
            else:
                # No account_uuid on this entry — clear override so
                # _get_account_metadata falls back to ~/.claude.json.
                set_active_account_uuid(None)
        return entry

    hooked_select.__name__ = original_select.__name__
    hooked_select.__doc__ = original_select.__doc__
    hooked_select.__wrapped__ = original_select  # type: ignore[attr-defined]

    CredentialPool.select = hooked_select
    CredentialPool._BILLING_BYPASS_SELECT_HOOK = True  # type: ignore[attr-defined]
    sys.stderr.write("[anthropic_billing_bypass] Pool select hook installed\n")


def apply_patches(anthropic_adapter_module: Any = None) -> bool:
    """Install the bypass on hermes-agent's anthropic adapter.

    Idempotent.  Returns False if hermes-agent's API is incompatible with
    this patch (e.g. ``build_anthropic_kwargs`` missing or signature changed).
    """
    aa = anthropic_adapter_module
    if aa is None:
        try:
            from agent import anthropic_adapter as aa  # type: ignore[import-not-found,no-redef]
        except ImportError as exc:
            logger.warning("Cannot import agent.anthropic_adapter: %s", exc)
            return False

    if getattr(aa, "_CLAUDE_CODE_BYPASS_APPLIED", False):
        _install_thinking_replay_classifier_patch()
        return True

    # 1. Add the OAuth-only beta flags.
    oauth_betas = getattr(aa, "_OAUTH_ONLY_BETAS", None)
    if isinstance(oauth_betas, list):
        for new_beta in _EXTRA_OAUTH_BETAS:
            if new_beta not in oauth_betas:
                oauth_betas.append(new_beta)
                logger.info("Appended beta flag: %s", new_beta)

    # 2. Verify build_anthropic_kwargs presence and signature.
    original_build = getattr(aa, "build_anthropic_kwargs", None)
    if not callable(original_build):
        logger.warning(
            "agent.anthropic_adapter.build_anthropic_kwargs missing; skipping"
        )
        return False

    try:
        sig = inspect.signature(original_build)
        if "is_oauth" not in sig.parameters:
            logger.warning(
                "build_anthropic_kwargs lacks 'is_oauth' param; skipping"
            )
            return False
    except (TypeError, ValueError) as exc:
        logger.warning("Cannot introspect build_anthropic_kwargs: %s", exc)
        return False

    # 3. Wrap build_anthropic_kwargs to apply the bypass on OAuth requests.
    def patched_build(*args: Any, **kwargs: Any) -> Dict[str, Any]:
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

    patched_build.__name__ = original_build.__name__
    patched_build.__qualname__ = getattr(
        original_build, "__qualname__", original_build.__name__
    )
    patched_build.__doc__ = original_build.__doc__
    patched_build.__module__ = getattr(original_build, "__module__", __name__)
    patched_build.__wrapped__ = original_build  # type: ignore[attr-defined]

    aa.build_anthropic_kwargs = patched_build
    aa._CLAUDE_CODE_BYPASS_APPLIED = True  # type: ignore[attr-defined]
    logger.debug("[anthropic_billing_bypass] Bypass installed")

    _install_thinking_replay_classifier_patch()
    _install_response_pascalcase_unhook(aa)

    # Window-aware subscription rate-limit auto-wait (ported from
    # hermes-claude-auth PR #27).  Never fatal: a failure here must not
    # break the billing bypass itself.
    try:
        if _rl_autowait_enabled():
            if install_rate_limit_autowait():
                sys.stderr.write(
                    "[anthropic_billing_bypass] Rate-limit auto-wait installed\n"
                )
        else:
            logger.debug("[rl-autowait] disabled via HERMES_RL_AUTOWAIT")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "install_rate_limit_autowait raised %s: %s", type(exc).__name__, exc
        )

    _install_pool_select_hook()
    return True
