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
"""

from __future__ import annotations

__version__ = "1.1.1"

import hashlib
import inspect
import logging
import platform
import sys
import traceback
from typing import Any, Dict, List

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
_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# Tool-name prefix used by hermes-agent's existing OAuth path.  We rewrite
# hermes's lowercase ``mcp_foo`` to Claude Code's PascalCase ``mcp_Foo``.
_MCP_PREFIX = "mcp_"

# Stainless SDK version the Anthropic JS SDK reports.  Real Claude Code ships
# @anthropic-ai/sdk@0.81.0 as of 2.1.112 — we spoof the same value.
_STAINLESS_PACKAGE_VERSION = "0.81.0"

# Node runtime version Claude Code 2.1.112 runs under.  We send a recent LTS
# value rather than our actual Python version (which would give us away).
_STAINLESS_NODE_VERSION = "v22.11.0"

# Additional beta flags the OAuth path needs on top of hermes-agent's built-in
# ``claude-code-20250219`` and ``oauth-2025-04-20``.  These are appended to
# ``_OAUTH_ONLY_BETAS`` in ``apply_patches``.
_EXTRA_OAUTH_BETAS = [
    "prompt-caching-scope-2026-01-05",
    "advisor-tool-2026-03-01",
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


def _build_spoof_headers() -> Dict[str, str]:
    """Headers real Claude Code 2.1.112 sends that hermes-agent does not.

    The Anthropic SDK (Stainless-generated) automatically attaches
    ``x-stainless-*`` identifying headers.  The validator cross-references these
    with the billing header's ``cc_entrypoint``; absent or mismatched values
    flag the request as third-party.  ``anthropic-dangerous-direct-browser-
    access: true`` is a separate Claude Code CLI behavior.
    """
    return {
        "anthropic-dangerous-direct-browser-access": "true",
        "x-stainless-arch": _stainless_arch(),
        "x-stainless-lang": "js",
        "x-stainless-os": _stainless_os(),
        "x-stainless-package-version": _STAINLESS_PACKAGE_VERSION,
        "x-stainless-retry-count": "0",
        "x-stainless-runtime": "node",
        "x-stainless-runtime-version": _STAINLESS_NODE_VERSION,
        "x-stainless-timeout": "600",
    }


def _pascalcase_mcp_name(name: str) -> str:
    """Rewrite ``mcp_foo_bar`` → ``mcp_Foo_bar``.

    Matches opencode-claude-auth PR #191 exactly: only the character
    immediately following the ``mcp_`` prefix is uppercased.  Names already in
    PascalCase are returned unchanged.
    """
    if not isinstance(name, str) or not name.startswith(_MCP_PREFIX):
        return name
    rest = name[len(_MCP_PREFIX):]
    if not rest or not rest[0].islower():
        return name
    return _MCP_PREFIX + rest[0].upper() + rest[1:]


def _rewrite_tool_names_pascalcase(api_kwargs: Dict[str, Any]) -> None:
    """Convert hermes-agent's lowercase ``mcp_`` tool names to PascalCase.

    Hermes prefixes tools with ``mcp_`` at line 1365 of ``anthropic_adapter``
    using the literal ``_MCP_TOOL_PREFIX + tool["name"]``, which produces
    lowercase values like ``mcp_bash``.  Anthropic's billing validator
    blacklists specific lowercase names (e.g. ``background_output``), flagging
    the client as non-Claude-Code.  Real Claude Code uses PascalCase after the
    prefix; we rewrite the request in-place.
    """
    tools = api_kwargs.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and "name" in tool:
                tool["name"] = _pascalcase_mcp_name(tool.get("name") or "")

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
                    block["name"] = _pascalcase_mcp_name(block.get("name") or "")


def _merge_spoof_extras(api_kwargs: Dict[str, Any]) -> None:
    """Inject Claude Code 2.1.112 request fingerprint via extra_headers / extra_query.

    The Anthropic Python SDK forwards both to the underlying HTTP request:
    ``extra_headers`` becomes request headers (merged with client defaults),
    ``extra_query`` becomes URL query parameters.  We avoid overwriting values
    already set by hermes-agent (e.g. its fast-mode ``anthropic-beta`` header)
    so our spoof is additive.
    """
    existing_headers = api_kwargs.get("extra_headers")
    merged_headers: Dict[str, str] = dict(_build_spoof_headers())
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
    return any(v in model for v in ("4-6", "4.6"))


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
            kept.append(identity_entry)
            if rest:
                moved_texts.append(rest)
            continue
        if text:
            moved_texts.append(text)

    if not identity_seen:
        kept.insert(0, {"type": "text", "text": _SYSTEM_IDENTITY})

    # Billing header first (no cache_control — changes per request).
    api_kwargs["system"] = [billing_entry] + kept

    if moved_texts:
        _prepend_to_first_user_message(messages, moved_texts)

    _rewrite_tool_names_pascalcase(api_kwargs)
    _merge_spoof_extras(api_kwargs)
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
    """Post-process response normalization to restore lowercase tool names.

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
        # Try to find AnthropicTransport in agent.transports.anthropic
        try:
            from agent.transports import anthropic as at
            original_class = getattr(at, "AnthropicTransport", None)
            if original_class:
                original = getattr(original_class, "normalize_response", None)
                if callable(original):
                    def patched_normalize_transport(self, response: Any, **kwargs: Any) -> Any:
                        result = original(self, response, **kwargs)
                        tool_calls = getattr(result, "tool_calls", None)
                        if not tool_calls:
                            return result
                        for tc in tool_calls:
                            name = getattr(tc, "name", None)
                            if isinstance(name, str) and name and name[0].isupper():
                                try:
                                    tc.name = _lowercase_first(name)
                                except Exception:
                                    pass
                        return result
                    
                    original_class.normalize_response = patched_normalize_transport
                    aa_module._CLAUDE_CODE_RESPONSE_UNHOOK_APPLIED = True
                    logger.info("Response PascalCase unhook installed on AnthropicTransport.normalize_response")
                    sys.stderr.write("[anthropic_billing_bypass] Response PascalCase unhook installed (Transport)\n")
                    return True
        except ImportError:
            pass

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
    _install_aux_client_hook()

    return True
