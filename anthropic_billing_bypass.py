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
- 1.6.0 (2026-05-08): Update wire format for CC 2.1.117 parity — new system
  identity prefix ("Claude agent" replacing "Claude Code"), structured
  metadata (JSON-encoded device_id + account_uuid + session_id),
  X-Claude-Code-Session-Id header, context_management body field,
  effort-2025-11-24 + context-management-2025-06-27 betas, Node v24.3.0
  stainless header, cache_control on system identity entry.
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

__version__ = "1.6.0"

import hashlib
import inspect
import json
import logging
import os
import platform
import sys
import traceback
import uuid
from typing import Any, Dict, List, Set

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
# CC 2.1.117 changed the identity prefix from the old "You are Claude Code..."
# to this new Agent SDK identity.  The server-side validator matches on the
# identity prefix to route requests to subscription billing vs extra-usage.
_SYSTEM_IDENTITY = "You are a Claude agent, built on Anthropic's Claude Agent SDK."
# Keep the old prefix for matching — hermes-agent may still inject it.
_OLD_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

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

# OAuth-only beta flags appended on top of hermes-agent's built-in
# ``claude-code-20250219`` and ``oauth-2025-04-20``.
_EXTRA_OAUTH_BETAS = [
    "prompt-caching-scope-2026-01-05",
    "context-management-2025-06-27",
    "advisor-tool-2026-03-01",
    "effort-2025-11-24",
]

# Stable per-process session ID matching CC's X-Claude-Code-Session-Id.
_SESSION_ID = str(uuid.uuid4())


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
                    block["name"] = _wrap_tool_name(block.get("name") or "")


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
    """
    config = _read_claude_config()
    oauth = config.get("oauthAccount") if isinstance(config, dict) else None
    metadata: Dict[str, Any] = {}
    if isinstance(oauth, dict) and isinstance(oauth.get("accountUuid"), str):
        # Build structured metadata matching CC 2.1.117 wire format.
        # device_id is a SHA-256 hex string in real CC; we derive one from
        # the account UUID so it's stable per-install.
        account_uuid = oauth["accountUuid"]
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
    """Headers real Claude Code 2.1.117 sends that hermes-agent does not.

    The Anthropic SDK (Stainless-generated) automatically attaches
    ``x-stainless-*`` identifying headers.  The validator cross-references
    these with the billing header's ``cc_entrypoint``; absent or mismatched
    values flag the request as third-party.  Lowercase to match upstream's
    JS SDK output.
    """
    return {
        "anthropic-dangerous-direct-browser-access": "true",
        "x-claude-code-session-id": _SESSION_ID,
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
    headers (e.g. fast-mode beta) survive — additive spoof only."""
    merged_headers: Dict[str, str] = dict(_build_spoof_headers())
    existing_headers = api_kwargs.get("extra_headers")
    if isinstance(existing_headers, dict):
        for k, v in existing_headers.items():
            merged_headers[k] = v
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
    """Strip orphaned ``tool_use`` / ``tool_result`` blocks.

    Anthropic rejects requests where a ``tool_use`` has no matching
    ``tool_result`` (or vice versa).  Long conversations or partial summaries
    can leave these orphans behind; this function removes them and drops
    messages whose content becomes empty as a result.

    Mirrors upstream ``src/transforms.ts::repairToolPairs``.  Returns the
    original list when nothing needs repairing so callers can detect a no-op
    via identity comparison.
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

    repaired: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            repaired.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            repaired.append(msg)
            continue
        filtered: List[Any] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("id") in orphaned_uses
            ):
                continue
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") in orphaned_results
            ):
                continue
            filtered.append(block)
        if filtered:
            repaired.append({**msg, "content": filtered})
    return repaired


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
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
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
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        })

    api_kwargs["system"] = [billing_entry] + kept

    if moved_texts:
        _prepend_to_first_user_message(messages, moved_texts)

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
        sys.stderr.write(
            "[anthropic_billing_bypass] Adapter unwrap hook installed\n"
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
                sys.stderr.write(
                    "[anthropic_billing_bypass] Transport unwrap hook installed\n"
                )
                any_installed = True
        else:
            any_installed = True

    return any_installed


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
    sys.stderr.write("[anthropic_billing_bypass] Bypass installed\n")

    _install_response_pascalcase_unhook(aa)
    return True
