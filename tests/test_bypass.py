# pyright: reportPrivateUsage=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportArgumentType=false

import copy
from types import SimpleNamespace

from anthropic_billing_bypass import (
    _BILLING_ENTRYPOINT,
    _MCP_HERMES_NAMESPACE,
    _OLD_SYSTEM_IDENTITY,
    _SESSION_ID,
    _SYSTEM_IDENTITY,
    _fix_temperature_for_oauth_adaptive,
    _install_response_pascalcase_unhook,
    _model_disables_effort,
    _pascalcase_mcp_name,
    _repair_tool_pairs,
    _strip_effort,
    _unwrap_tool_name,
    _wrap_tool_name,
    apply_claude_code_bypass,
)


def test_apply_claude_code_bypass_injects_billing_header_and_preserves_identity(
    basic_api_kwargs,
):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.90")

    system = basic_api_kwargs["system"]
    assert system[0]["text"].startswith("x-anthropic-billing-header: ")
    assert system[1]["text"] == _SYSTEM_IDENTITY


def test_apply_claude_code_bypass_uses_sdk_cli_entrypoint(basic_api_kwargs):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.112")

    billing_text = basic_api_kwargs["system"][0]["text"]
    assert "cc_entrypoint=sdk-cli;" in billing_text
    assert _BILLING_ENTRYPOINT == "sdk-cli"


def test_apply_claude_code_bypass_relocates_non_identity_system_text_to_first_user_message(
    basic_api_kwargs,
):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.90")

    user_content = basic_api_kwargs["messages"][0]["content"]
    assert isinstance(user_content, list)
    text = user_content[0]["text"]
    assert "<system-reminder>\nStay helpful.\n</system-reminder>" in text
    assert "<system-reminder>\nExtra system guidance\n</system-reminder>" in text
    assert text.endswith("hello world")
    # Regression: PR #11 introduced literal \n escapes (issue #?).  Real
    # newlines must be present so Anthropic's content validator passes.
    assert "\\n" not in text


def test_apply_claude_code_bypass_is_idempotent(basic_api_kwargs):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.90")
    once = copy.deepcopy(basic_api_kwargs)

    apply_claude_code_bypass(basic_api_kwargs, "2.1.90")

    assert len(basic_api_kwargs["system"]) == 2
    assert basic_api_kwargs["system"][0]["text"].startswith(
        "x-anthropic-billing-header: "
    )
    assert basic_api_kwargs["system"][1]["text"] == _SYSTEM_IDENTITY
    assert basic_api_kwargs["messages"] == once["messages"]


def test_apply_claude_code_bypass_normalizes_string_system(simple_messages):
    api_kwargs = {
        "system": "plain system",
        "messages": [dict(message) for message in simple_messages],
        "model": "claude-opus-4-6-20260101",
    }

    apply_claude_code_bypass(api_kwargs, "2.1.90")

    assert isinstance(api_kwargs["system"], list)
    assert api_kwargs["system"][1]["text"] == _SYSTEM_IDENTITY
    assert (
        "<system-reminder>\nplain system\n</system-reminder>"
        in api_kwargs["messages"][0]["content"][0]["text"]
    )


def test_apply_claude_code_bypass_without_messages_is_noop():
    api_kwargs = {"system": "plain system", "model": "claude-opus-4-6-20260101"}

    apply_claude_code_bypass(api_kwargs, "2.1.90")

    assert api_kwargs == {"system": "plain system", "model": "claude-opus-4-6-20260101"}


def test_fix_temperature_for_oauth_adaptive_removes_non_default_temperature():
    api_kwargs = {"model": "claude-opus-4-6-20260101", "temperature": 0.2}
    _fix_temperature_for_oauth_adaptive(api_kwargs, site="test")
    assert "temperature" not in api_kwargs


def test_fix_temperature_for_oauth_adaptive_keeps_temperature_one():
    api_kwargs = {"model": "claude-opus-4-6-20260101", "temperature": 1}
    _fix_temperature_for_oauth_adaptive(api_kwargs, site="test")
    assert api_kwargs["temperature"] == 1


def test_fix_temperature_for_oauth_adaptive_keeps_temperature_for_other_models():
    api_kwargs = {"model": "claude-3-7-sonnet", "temperature": 0.2}
    _fix_temperature_for_oauth_adaptive(api_kwargs, site="test")
    assert api_kwargs["temperature"] == 0.2


def test_fix_temperature_for_oauth_adaptive_without_temperature_is_noop():
    api_kwargs = {"model": "claude-opus-4-6-20260101"}
    _fix_temperature_for_oauth_adaptive(api_kwargs, site="test")
    assert api_kwargs == {"model": "claude-opus-4-6-20260101"}


def test_apply_claude_code_bypass_strips_temperature_for_opus46(simple_messages):
    api_kwargs = {
        "system": "plain",
        "messages": [dict(message) for message in simple_messages],
        "model": "claude-opus-4-6-20260101",
        "temperature": 0.2,
    }
    apply_claude_code_bypass(api_kwargs, "2.1.112")
    assert "temperature" not in api_kwargs


def test_pascalcase_mcp_name_uppercases_first_char_after_prefix():
    assert _pascalcase_mcp_name("mcp_bash") == "mcp_Bash"
    assert _pascalcase_mcp_name("mcp_read") == "mcp_Read"
    assert _pascalcase_mcp_name("mcp_background_output") == "mcp_Background_output"


def test_pascalcase_mcp_name_leaves_already_pascalcase_unchanged():
    assert _pascalcase_mcp_name("mcp_Bash") == "mcp_Bash"
    assert _pascalcase_mcp_name("mcp_Background_output") == "mcp_Background_output"


def test_pascalcase_mcp_name_ignores_unprefixed_names():
    assert _pascalcase_mcp_name("bash") == "bash"
    assert _pascalcase_mcp_name("not_mcp_bash") == "not_mcp_bash"
    assert _pascalcase_mcp_name("") == ""


def test_wrap_tool_name_namespaces_to_mcp_hermes():
    assert _wrap_tool_name("mcp_bash") == "mcp__hermes__Bash"
    assert _wrap_tool_name("mcp_background_output") == "mcp__hermes__Background_output"
    assert _wrap_tool_name("bash") == "mcp__hermes__Bash"


def test_wrap_tool_name_is_idempotent_when_already_namespaced():
    assert _wrap_tool_name("mcp__hermes__Bash") == "mcp__hermes__Bash"


def test_unwrap_tool_name_lowercases_first_char():
    assert _unwrap_tool_name("mcp__hermes__Bash") == "bash"
    assert _unwrap_tool_name("mcp__hermes__Background_output") == "background_output"


def test_unwrap_tool_name_handles_already_stripped_prefix():
    # When hermes's transport strips ``mcp_`` before our hook runs, the name
    # arrives as ``_hermes__Bash``.
    assert _unwrap_tool_name("_hermes__Bash") == "bash"


def test_unwrap_tool_name_returns_unknown_names_unchanged():
    assert _unwrap_tool_name("plain_bash") == "plain_bash"
    assert _unwrap_tool_name("") == ""


def test_apply_claude_code_bypass_rewrites_tool_names_to_hermes_namespace(
    basic_api_kwargs,
):
    basic_api_kwargs["tools"] = [
        {"name": "mcp_bash"},
        {"name": "mcp_background_output"},
        {"name": "mcp__hermes__Already_done"},
    ]
    # Provide a paired tool_use / tool_result so _repair_tool_pairs doesn't
    # eat the assistant message before tool-name rewriting runs.
    basic_api_kwargs["messages"] = [
        {"role": "user", "content": "hello world"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "mcp_bash", "id": "tool_1", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "ok"},
            ],
        },
    ]

    apply_claude_code_bypass(basic_api_kwargs, "2.1.112")

    tool_names = [tool["name"] for tool in basic_api_kwargs["tools"]]
    assert tool_names == [
        "mcp__hermes__Bash",
        "mcp__hermes__Background_output",
        "mcp__hermes__Already_done",
    ]
    assert all(name.startswith(_MCP_HERMES_NAMESPACE) for name in tool_names)

    assistant_msg = basic_api_kwargs["messages"][1]
    tool_use_block = assistant_msg["content"][0]
    assert tool_use_block["name"] == "mcp__hermes__Bash"


def test_apply_claude_code_bypass_injects_stainless_and_direct_browser_headers(
    basic_api_kwargs,
):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.112")

    extra_headers = basic_api_kwargs["extra_headers"]
    assert extra_headers["anthropic-dangerous-direct-browser-access"] == "true"
    assert extra_headers["x-stainless-lang"] == "js"
    assert extra_headers["x-stainless-runtime"] == "node"
    assert extra_headers["x-stainless-package-version"] == "0.81.0"
    assert extra_headers["x-stainless-retry-count"] == "0"
    assert extra_headers["x-stainless-timeout"] == "600"
    assert extra_headers["x-stainless-os"] in ("MacOS", "Linux", "Windows")
    assert extra_headers["x-stainless-arch"] in ("x64", "arm64", "ia32", "unknown")
    # Regression: PR #11 capitalized these to X-Stainless-*; lowercase keys
    # match upstream JS SDK output exactly.
    assert "X-Stainless-Lang" not in extra_headers


def test_apply_claude_code_bypass_sets_beta_true_query_param(basic_api_kwargs):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.112")

    assert basic_api_kwargs["extra_query"] == {"beta": "true"}


def test_apply_claude_code_bypass_preserves_existing_extra_headers(basic_api_kwargs):
    basic_api_kwargs["extra_headers"] = {"anthropic-beta": "fast-mode-2026-02-01"}

    apply_claude_code_bypass(basic_api_kwargs, "2.1.112")

    assert basic_api_kwargs["extra_headers"]["anthropic-beta"] == "fast-mode-2026-02-01"
    assert (
        basic_api_kwargs["extra_headers"]["anthropic-dangerous-direct-browser-access"]
        == "true"
    )


def test_repair_tool_pairs_drops_orphaned_tool_use_and_result():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "read", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "tX", "content": "stale"},
            ],
        },
    ]

    repaired = _repair_tool_pairs(messages)

    assert len(repaired) == 3
    assistant_blocks = repaired[1]["content"]
    assert [b["id"] for b in assistant_blocks] == ["t1"]
    user_results = repaired[2]["content"]
    assert [b["tool_use_id"] for b in user_results] == ["t1"]


def test_repair_tool_pairs_drops_messages_with_only_orphans():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tX", "name": "bash", "input": {}}
            ],
        },
    ]

    repaired = _repair_tool_pairs(messages)

    assert len(repaired) == 1
    assert repaired[0]["role"] == "user"


def test_repair_tool_pairs_returns_input_when_nothing_to_repair():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    ]

    repaired = _repair_tool_pairs(messages)
    assert repaired is messages  # identity-preserving no-op


def test_apply_claude_code_bypass_repairs_tool_pairs_before_signing(simple_messages):
    api_kwargs = {
        "system": "p",
        "messages": [
            {"role": "user", "content": "hello world"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tX", "name": "mcp_read", "input": {}}
                ],
            },
        ],
        "model": "claude-opus-4-6-20260101",
    }

    apply_claude_code_bypass(api_kwargs, "2.1.112")

    # Orphan removed; assistant message dropped (now empty after filter).
    assert len(api_kwargs["messages"]) == 1
    assert api_kwargs["messages"][0]["role"] == "user"


def test_model_disables_effort_for_haiku():
    assert _model_disables_effort("claude-haiku-3-5")
    assert _model_disables_effort("claude-3-5-haiku-20241022")
    assert not _model_disables_effort("claude-opus-4-6-20260101")
    assert not _model_disables_effort("claude-sonnet-4-7")


def test_strip_effort_removes_from_output_config_for_haiku():
    api = {
        "model": "claude-haiku-3-5",
        "output_config": {"effort": "low", "other": True},
    }
    _strip_effort(api)
    assert api["output_config"] == {"other": True}


def test_strip_effort_drops_empty_parent_dict():
    api = {
        "model": "claude-haiku-3-5",
        "output_config": {"effort": "low"},
        "thinking": {"effort": "medium"},
    }
    _strip_effort(api)
    assert "output_config" not in api
    assert "thinking" not in api


def test_strip_effort_preserves_other_thinking_keys():
    api = {
        "model": "claude-haiku-3-5",
        "thinking": {"effort": "low", "budget_tokens": 1024},
    }
    _strip_effort(api)
    assert api["thinking"] == {"budget_tokens": 1024}


def test_strip_effort_is_noop_for_non_haiku():
    api = {
        "model": "claude-opus-4-6-20260101",
        "output_config": {"effort": "low"},
    }
    _strip_effort(api)
    assert api["output_config"] == {"effort": "low"}


# ---------------------------------------------------------------------------
# Response unwrap hook tests
# ---------------------------------------------------------------------------


def _make_fake_adapter_module(tool_names, *, namespace=True):
    """Build a SimpleNamespace mimicking ``agent.anthropic_adapter``.

    Tool names returned by ``original_normalize`` arrive in
    ``mcp__hermes__Foo`` form because that's what we sent on the way out.
    """
    wrapped_names = [
        (_MCP_HERMES_NAMESPACE + n[len("mcp_"):][0].upper() + n[len("mcp_") + 1:])
        if namespace and n.startswith("mcp_")
        else n
        for n in tool_names
    ]

    def original_normalize(response, strip_tool_prefix=False):
        tool_calls = []
        for name in wrapped_names:
            tool_calls.append(
                SimpleNamespace(
                    id="tool_1",
                    type="function",
                    function=SimpleNamespace(name=name, arguments="{}"),
                )
            )
        msg = SimpleNamespace(content=None, tool_calls=tool_calls or None, reasoning=None)
        return msg, "tool_calls"

    return SimpleNamespace(normalize_anthropic_response=original_normalize)


def test_response_unhook_lowercases_first_char_of_tool_names():
    adapter = _make_fake_adapter_module(["mcp_bash", "mcp_background_output"])

    assert _install_response_pascalcase_unhook(adapter, force=True) is True

    msg, _reason = adapter.normalize_anthropic_response(
        response=object(), strip_tool_prefix=True
    )

    names = [tc.function.name for tc in msg.tool_calls]
    assert names == ["bash", "background_output"]


def test_response_unhook_strips_hermes_namespace_when_present():
    adapter = _make_fake_adapter_module(
        ["mcp__hermes__Bash"], namespace=False
    )

    _install_response_pascalcase_unhook(adapter, force=True)

    msg, _reason = adapter.normalize_anthropic_response(response=object())
    assert msg.tool_calls[0].function.name == "bash"


def test_response_unhook_leaves_unknown_names_alone():
    adapter = _make_fake_adapter_module(["plain_tool"], namespace=False)

    _install_response_pascalcase_unhook(adapter, force=True)

    msg, _reason = adapter.normalize_anthropic_response(response=object())
    assert msg.tool_calls[0].function.name == "plain_tool"


def test_response_unhook_is_idempotent():
    adapter = _make_fake_adapter_module(["mcp_bash"])

    assert _install_response_pascalcase_unhook(adapter, force=True) is True
    assert _install_response_pascalcase_unhook(adapter) is True

    msg, _reason = adapter.normalize_anthropic_response(response=object())
    assert msg.tool_calls[0].function.name == "bash"


# ---------------------------------------------------------------------------
# CC 2.1.117 wire-format parity tests (v1.6.0)
# ---------------------------------------------------------------------------


def test_system_identity_is_new_agent_sdk_prefix():
    """Verify _SYSTEM_IDENTITY uses the CC 2.1.117 identity string."""
    assert _SYSTEM_IDENTITY == "You are a Claude agent, built on Anthropic's Claude Agent SDK."
    assert _OLD_SYSTEM_IDENTITY == "You are Claude Code, Anthropic's official CLI for Claude."


def test_bypass_replaces_old_identity_with_new(basic_api_kwargs):
    """When hermes-agent sends the old identity, bypass should replace it."""
    assert basic_api_kwargs["system"][0]["text"].startswith(_OLD_SYSTEM_IDENTITY)

    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    system = basic_api_kwargs["system"]
    identity_entry = system[1]
    assert identity_entry["text"] == _SYSTEM_IDENTITY
    assert identity_entry.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


def test_bypass_preserves_new_identity(new_identity_api_kwargs):
    """When system already has the new identity, bypass should keep it."""
    apply_claude_code_bypass(new_identity_api_kwargs, "2.1.117")

    system = new_identity_api_kwargs["system"]
    identity_entry = system[1]
    assert identity_entry["text"] == _SYSTEM_IDENTITY


def test_bypass_injects_cache_control_on_identity(basic_api_kwargs):
    """Identity system entry should have ephemeral cache_control."""
    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    identity_entry = basic_api_kwargs["system"][1]
    assert identity_entry["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_bypass_injects_context_management_via_extra_body(basic_api_kwargs):
    """context_management should be injected via extra_body when thinking is active."""
    basic_api_kwargs["thinking"] = {"type": "adaptive"}
    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    extra_body = basic_api_kwargs.get("extra_body", {})
    cm = extra_body.get("context_management")
    assert cm is not None
    assert cm == {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}


def test_bypass_skips_context_management_when_top_level_present(basic_api_kwargs):
    """If context_management is already set at top level, bypass should not inject."""
    basic_api_kwargs["context_management"] = {"edits": [{"type": "custom"}]}
    basic_api_kwargs["thinking"] = {"type": "adaptive"}

    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    assert basic_api_kwargs["context_management"] == {"edits": [{"type": "custom"}]}


def test_bypass_injects_session_id_header(basic_api_kwargs):
    """X-Claude-Code-Session-Id should be present in extra_headers."""
    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    headers = basic_api_kwargs["extra_headers"]
    assert "x-claude-code-session-id" in headers
    assert headers["x-claude-code-session-id"] == _SESSION_ID
    import uuid
    uuid.UUID(headers["x-claude-code-session-id"], version=4)


def test_bypass_uses_node_v24(basic_api_kwargs):
    """Stainless runtime version should match CC 2.1.117 (node v24.3.0)."""
    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    headers = basic_api_kwargs["extra_headers"]
    assert headers["x-stainless-runtime-version"] == "v24.3.0"



def test_bypass_skips_context_management_without_thinking(basic_api_kwargs):
    """context_management should NOT be injected when thinking is not enabled."""
    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    extra_body = basic_api_kwargs.get("extra_body", {})
    assert "context_management" not in extra_body
