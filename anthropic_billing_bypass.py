import hashlib
import inspect
import logging
import platform
import sys
import traceback
import json
import os
from typing import Any, Dict, List

__version__ = "1.4.0-pr10"
logger = logging.getLogger("anthropic_billing_bypass")

_BILLING_SALT = "59cf53e54c78"
_BILLING_ENTRYPOINT = "sdk-cli"
_BILLING_PREFIX = "x-anthropic-billing-header"
_SYSTEM_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_MCP_PREFIX = "mcp_"
_MCP_HERMES_NAMESPACE = "mcp__hermes__"
_STAINLESS_PACKAGE_VERSION = "0.81.0"
_STAINLESS_NODE_VERSION = "v22.11.0"
_EXTRA_OAUTH_BETAS = ["prompt-caching-scope-2026-01-05", "advisor-tool-2026-03-01"]

def _pascalcase_mcp_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        return name
    return name[0].upper() + name[1:]

def _wrap_tool_name_as_mcp_hermes(name: str) -> str:
    if not isinstance(name, str) or not name:
        return name
    return _MCP_HERMES_NAMESPACE + name

def _unwrap_mcp_hermes_name(name: Any) -> Any:
    if isinstance(name, str) and name.startswith(_MCP_HERMES_NAMESPACE):
        return name[len(_MCP_HERMES_NAMESPACE) :]
    return name

def _normalize_tool_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        return name
    if name.startswith(_MCP_PREFIX):
        name = name[len(_MCP_PREFIX):]
    return _wrap_tool_name_as_mcp_hermes(_pascalcase_mcp_name(name))

def _read_claude_config() -> Dict[str, Any]:
    path = os.path.expanduser("~/.claude.json")
    if not os.path.exists(path): return {}
    try:
        with open(path, "r") as f: return json.load(f)
    except Exception: return {}

def _get_account_metadata() -> Dict[str, Any]:
    config = _read_claude_config()
    oauth = config.get("oauthAccount", {})
    metadata = {}
    if "accountUuid" in oauth: metadata["account_uuid"] = oauth["accountUuid"]
    if "organizationUuid" in oauth: metadata["organization_uuid"] = oauth["organizationUuid"]
    return metadata

def _extract_first_user_message_text(messages: List[Dict[str, Any]]) -> str:
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user": continue
        content = msg.get("content")
        if isinstance(content, str): return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text"); 
                    if isinstance(text, str) and text: return text
    return ""

def _compute_cch(message_text: str) -> str:
    return hashlib.sha256(message_text.encode("utf-8")).hexdigest()[:5]

def _compute_version_suffix(message_text: str, version: str) -> str:
    sampled = "".join(message_text[i] if i < len(message_text) else "0" for i in (4, 7, 20))
    input_str = f"{_BILLING_SALT}{sampled}{version}"
    return hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:3]

def _build_billing_header_value(messages: List[Dict[str, Any]], version: str, entrypoint: str) -> str:
    text = _extract_first_user_message_text(messages)
    suffix = _compute_version_suffix(text, version)
    cch = _compute_cch(text)
    return f"x-anthropic-billing-header: cc_version={version}.{suffix}; cc_entrypoint={entrypoint}; cch={cch};"

def _stainless_arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("x86_64", "amd64"): return "x64"
    if machine in ("arm64", "aarch64"): return "arm64"
    return machine or "unknown"

def _stainless_os() -> str:
    mapping = {"Darwin": "MacOS", "Linux": "Linux", "Windows": "Windows"}
    return mapping.get(platform.system(), "Unknown")

def _build_spoof_headers() -> Dict[str, str]:
    return {
        "anthropic-dangerous-direct-browser-access": "true",
        "X-Stainless-Arch": _stainless_arch(),
        "X-Stainless-Lang": "js",
        "X-Stainless-OS": _stainless_os(),
        "X-Stainless-Package-Version": _STAINLESS_PACKAGE_VERSION,
        "X-Stainless-Retry-Count": "0",
        "X-Stainless-Runtime": "node",
        "X-Stainless-Runtime-Version": _STAINLESS_NODE_VERSION,
        "X-Stainless-Timeout": "600",
    }

def _rewrite_tool_names_pascalcase(api_kwargs: Dict[str, Any]) -> None:
    tools = api_kwargs.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and "name" in tool:
                tool["name"] = _normalize_tool_name(tool.get("name") or "")
    messages = api_kwargs.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict): continue
            content = msg.get("content")
            if not isinstance(content, list): continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    block["name"] = _normalize_tool_name(block.get("name") or "")

def _merge_spoof_extras(api_kwargs: Dict[str, Any]) -> None:
    merged_headers = dict(_build_spoof_headers())
    existing_headers = api_kwargs.get("extra_headers")
    if isinstance(existing_headers, dict): merged_headers.update(existing_headers)
    api_kwargs["extra_headers"] = merged_headers
    merged_query = {"beta": "true"}
    existing_query = api_kwargs.get("extra_query")
    if isinstance(existing_query, dict): merged_query.update(existing_query)
    api_kwargs["extra_query"] = merged_query

def apply_claude_code_bypass(api_kwargs: Dict[str, Any], version: str) -> None:
    messages = api_kwargs.get("messages")
    if not isinstance(messages, list) or not messages: return
    system = api_kwargs.get("system", [])
    if isinstance(system, str): system = [{"type": "text", "text": system}]
    billing_value = _build_billing_header_value(messages, version, _BILLING_ENTRYPOINT)
    billing_entry = {"type": "text", "text": billing_value}
    kept, moved_texts, identity_seen = [], [], False
    for entry in system:
        if not isinstance(entry, dict) or entry.get("type") != "text":
            kept.append(entry); continue
        text = entry.get("text", "")
        if text.startswith(_BILLING_PREFIX): continue
        if text.startswith(_SYSTEM_IDENTITY):
            if identity_seen: continue
            identity_seen = True
            rest = text[len(_SYSTEM_IDENTITY):].lstrip("\n")
            kept.append({"type": "text", "text": _SYSTEM_IDENTITY})
            if rest: moved_texts.append(rest)
        elif text: moved_texts.append(text)
    if not identity_seen: kept.insert(0, {"type": "text", "text": _SYSTEM_IDENTITY})
    api_kwargs["system"] = [billing_entry] + kept
    if moved_texts:
        combined = "\\n\\n".join(f"<system-reminder>\\n{t}\\n</system-reminder>" for t in moved_texts)
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str): messages[i]["content"] = f"{combined}\\n\\n{content}"
                elif isinstance(content, list): content.insert(0, {"type": "text", "text": combined})
                break
    _rewrite_tool_names_pascalcase(api_kwargs)
    _merge_spoof_extras(api_kwargs)
    metadata = _get_account_metadata()
    if metadata: api_kwargs["metadata"] = metadata

def _install_response_pascalcase_unhook(aa_module: Any) -> bool:
    try:
        from agent.transports import anthropic as at
        cls = getattr(at, "AnthropicTransport", None)
        if cls and not getattr(cls, "_HERMES_MCP_UNWRAP_APPLIED", False):
            orig = cls.normalize_response
            def patched(self, response: Any, **kwargs: Any) -> Any:
                res = orig(self, response, **kwargs)
                tcs = getattr(res, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        tc.name = _unwrap_mcp_hermes_name(tc.name)
                return res
            cls.normalize_response = patched
            cls._HERMES_MCP_UNWRAP_APPLIED = True
            sys.stderr.write("[anthropic_billing_bypass] Transport unwrap hook installed\\n")
            return True
    except Exception: pass
    return False

def apply_patches(aa: Any = None) -> bool:
    if aa is None:
        try: from agent import anthropic_adapter as aa
        except ImportError: return False
    if getattr(aa, "_CLAUDE_CODE_BYPASS_APPLIED", False): return True
    betas = getattr(aa, "_OAUTH_ONLY_BETAS", [])
    for b in _EXTRA_OAUTH_BETAS:
        if b not in betas: betas.append(b)
    orig_build = aa.build_anthropic_kwargs
    def patched_build(*args, **kwargs):
        res = orig_build(*args, **kwargs)
        is_oauth = kwargs.get("is_oauth", False)
        if not is_oauth and len(args) > 6: is_oauth = args[6]
        if is_oauth and isinstance(res, dict):
            version = "2.1.123"
            try: version = aa._get_claude_code_version()
            except: pass
            apply_claude_code_bypass(res, version)
        return res
    aa.build_anthropic_kwargs = patched_build
    aa._CLAUDE_CODE_BYPASS_APPLIED = True
    sys.stderr.write("[anthropic_billing_bypass] Bypass installed\\n")
    _install_response_pascalcase_unhook(aa)
    return True
