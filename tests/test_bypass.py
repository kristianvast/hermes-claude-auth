# pyright: reportPrivateUsage=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportArgumentType=false

import copy
from types import SimpleNamespace

from anthropic_billing_bypass import (
    _AGENT_SDK_SYSTEM_IDENTITY,
    _BILLING_ENTRYPOINT,
    _MCP_HERMES_NAMESPACE,
    _SESSION_ID,
    _SYSTEM_IDENTITY,
    _fix_temperature_for_oauth_adaptive,
    _install_response_pascalcase_unhook,
    _model_disables_effort,
    _pascalcase_mcp_name,
    _repair_tool_pairs,
    _split_tool_results_from_followup_user_text,
    _strip_effort,
    _strip_thinking_from_replay,
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


def test_apply_claude_code_bypass_rewraps_tool_use_in_thinking_message():
    """v1.5.7: signed thinking blocks are PRESERVED (original block order
    maintained via _anthropic_raw_content fast path).  tool_use names are
    still re-wrapped to mcp__hermes__ form."""
    thinking_block = {"type": "thinking", "thinking": "private", "signature": "sig"}
    api_kwargs = {
        "system": "plain",
        "model": "claude-opus-4-8",
        "tools": [{"name": "terminal"}],
        "messages": [
            {"role": "user", "content": "please use a tool"},
            {
                "role": "assistant",
                "content": [
                    thinking_block,
                    {"type": "tool_use", "id": "tool_1", "name": "terminal", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "ok"},
                ],
            },
        ],
    }

    apply_claude_code_bypass(api_kwargs, "2.1.112")

    assistant_content = api_kwargs["messages"][1]["content"]
    # Signed thinking block should be PRESERVED (v1.5.7 — order-preserving)
    thinking_blocks = [
        b for b in assistant_content
        if isinstance(b, dict) and b.get("type") == "thinking"
    ]
    assert len(thinking_blocks) == 1
    assert thinking_blocks[0]["signature"] == "sig"
    # tool_use name should still be re-wrapped
    tool_use_blocks = [b for b in assistant_content if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert len(tool_use_blocks) == 1
    assert tool_use_blocks[0]["name"] == "mcp__hermes__Terminal"


def test_split_tool_results_from_followup_user_text_inserts_bridge():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "private", "signature": "sig"},
                {"type": "tool_use", "id": "tool_1", "name": "terminal", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "ok"},
                {"type": "text", "text": "new prompt after failed tool turn"},
            ],
        },
    ]

    repaired = _split_tool_results_from_followup_user_text(messages)

    assert repaired is not messages
    assert [msg["role"] for msg in repaired] == ["assistant", "user", "assistant", "user"]
    assert repaired[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tool_1", "content": "ok"}
    ]
    assert repaired[2]["content"][0]["type"] == "text"
    assert repaired[3]["content"] == [
        {"type": "text", "text": "new prompt after failed tool turn"}
    ]


def test_apply_claude_code_bypass_splits_merged_tool_result_and_followup_text():
    api_kwargs = {
        "system": "plain",
        "model": "claude-opus-4-8",
        "messages": [
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private", "signature": "sig"},
                    {"type": "tool_use", "id": "tool_1", "name": "terminal", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "ok"},
                    {"type": "text", "text": "new prompt"},
                ],
            },
        ],
    }

    apply_claude_code_bypass(api_kwargs, "2.1.112")

    roles = [msg["role"] for msg in api_kwargs["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert api_kwargs["messages"][2]["content"][0]["type"] == "tool_result"
    assert api_kwargs["messages"][3]["content"][0]["type"] == "text"
    assert api_kwargs["messages"][4]["content"][0]["text"] == "new prompt"


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


def test_repair_tool_pairs_counts_tool_use_inside_thinking_message():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "private", "signature": "sig"},
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    ]

    repaired = _repair_tool_pairs(messages)

    assert repaired is messages


def test_repair_tool_pairs_synthesizes_result_for_orphaned_thinking_tool_use():
    thinking_block = {"type": "thinking", "thinking": "private", "signature": "sig"}
    tool_use_block = {"type": "tool_use", "id": "t_missing", "name": "bash", "input": {}}
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [thinking_block, tool_use_block]},
        {"role": "user", "content": [{"type": "text", "text": "next request"}]},
    ]

    repaired = _repair_tool_pairs(messages)

    assert repaired[1]["content"] is messages[1]["content"]
    assert repaired[1]["content"] == [thinking_block, tool_use_block]
    next_user_blocks = repaired[2]["content"]
    assert next_user_blocks[0] == {
        "type": "tool_result",
        "tool_use_id": "t_missing",
        "content": "[Hermes repair: missing tool result synthesized for an earlier tool_use.]",
        "is_error": True,
    }
    assert next_user_blocks[1] == {"type": "text", "text": "next request"}


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


def test_many_paired_tool_calls_survive_bypass_and_are_namespaced():
    tool_count = 100
    assistant_blocks = [
        {
            "type": "tool_use",
            "id": f"tool_{idx}",
            "name": "mcp_bash" if idx % 2 == 0 else "terminal",
            "input": {"cmd": f"printf {idx}"},
        }
        for idx in range(tool_count)
    ]
    result_blocks = [
        {
            "type": "tool_result",
            "tool_use_id": f"tool_{idx}",
            "content": f"ok {idx}",
        }
        for idx in range(tool_count)
    ]
    api_kwargs = {
        "system": "plain",
        "model": "claude-opus-4-6-20260101",
        "tools": [{"name": "mcp_bash"}, {"name": "terminal"}],
        "messages": [
            {"role": "user", "content": "run many tools"},
            {"role": "assistant", "content": assistant_blocks},
            {"role": "user", "content": result_blocks},
        ],
    }

    apply_claude_code_bypass(api_kwargs, "2.1.112")

    assistant_after = api_kwargs["messages"][1]["content"]
    result_after = api_kwargs["messages"][2]["content"]
    assert len(assistant_after) == tool_count
    assert len(result_after) == tool_count
    assert {block["id"] for block in assistant_after} == {
        block["tool_use_id"] for block in result_after
    }
    assert all(
        block["name"].startswith(_MCP_HERMES_NAMESPACE)
        for block in assistant_after
    )


def test_large_orphaned_tool_history_is_repaired_without_leftover_orphans():
    messages = [{"role": "user", "content": [{"type": "text", "text": "start"}]}]
    expected_ids = set()
    for turn in range(20):
        valid_id = f"valid_{turn}"
        missing_id = f"missing_{turn}"
        expected_ids.add(valid_id)
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": valid_id, "name": "bash", "input": {}},
                    {"type": "tool_use", "id": missing_id, "name": "read", "input": {}},
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": valid_id, "content": "ok"},
                    {
                        "type": "tool_result",
                        "tool_use_id": f"stale_{turn}",
                        "content": "stale",
                    },
                ],
            }
        )

    repaired = _repair_tool_pairs(messages)
    seen_uses = {
        block["id"]
        for msg in repaired
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    seen_results = {
        block["tool_use_id"]
        for msg in repaired
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    }

    assert seen_uses == expected_ids
    assert seen_results == expected_ids


def test_many_thinking_orphaned_tool_uses_get_synthetic_results():
    messages = [{"role": "user", "content": [{"type": "text", "text": "start"}]}]
    original_assistant_contents = []
    for turn in range(25):
        content = [
            {
                "type": "thinking",
                "thinking": f"private {turn}",
                "signature": f"sig_{turn}",
            },
            {
                "type": "tool_use",
                "id": f"thinking_tool_{turn}",
                "name": "terminal",
                "input": {},
            },
        ]
        original_assistant_contents.append(content)
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"next {turn}"}],
            }
        )

    repaired = _repair_tool_pairs(messages)

    synthetic_results = []
    preserved_contents = []
    for msg in repaired:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if msg.get("role") == "assistant" and any(
            isinstance(block, dict) and block.get("type") == "thinking"
            for block in content
        ):
            preserved_contents.append(content)
        synthetic_results.extend(
            block
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("is_error") is True
        )

    assert preserved_contents == original_assistant_contents
    assert {block["tool_use_id"] for block in synthetic_results} == {
        f"thinking_tool_{turn}" for turn in range(25)
    }


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


def test_system_identity_tracks_upstream_claude_code_prefix():
    """The emitted identity must match upstream opencode-claude-auth.

    Upstream still ships this exact string at ccVersion 2.1.217, so PR #15's
    "Claude Agent SDK" identity is not what the validator expects. That variant
    is only recognised on input.
    """
    assert _SYSTEM_IDENTITY == "You are Claude Code, Anthropic's official CLI for Claude."
    assert _AGENT_SDK_SYSTEM_IDENTITY == (
        "You are a Claude agent, built on Anthropic's Claude Agent SDK."
    )


def test_bypass_normalizes_agent_sdk_identity_to_claude_code(new_identity_api_kwargs):
    """An Agent-SDK identity on input is rewritten to the canonical one."""
    assert new_identity_api_kwargs["system"][0]["text"].startswith(
        _AGENT_SDK_SYSTEM_IDENTITY
    )

    apply_claude_code_bypass(new_identity_api_kwargs, "2.1.217")

    identity_entry = new_identity_api_kwargs["system"][1]
    assert identity_entry["text"] == _SYSTEM_IDENTITY
    assert identity_entry.get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


def test_bypass_emits_exactly_one_identity_entry(new_identity_api_kwargs):
    """Normalising must not leave both identity variants in system[]."""
    apply_claude_code_bypass(new_identity_api_kwargs, "2.1.217")

    system = new_identity_api_kwargs["system"]
    identities = [
        e for e in system
        if isinstance(e, dict)
        and isinstance(e.get("text"), str)
        and (
            e["text"].startswith(_SYSTEM_IDENTITY)
            or e["text"].startswith(_AGENT_SDK_SYSTEM_IDENTITY)
        )
    ]
    assert len(identities) == 1
    assert identities[0]["text"] == _SYSTEM_IDENTITY


def test_bypass_injects_cache_control_on_identity(basic_api_kwargs):
    """Identity system entry should have ephemeral cache_control."""
    apply_claude_code_bypass(basic_api_kwargs, "2.1.117")

    identity_entry = basic_api_kwargs["system"][1]
    assert identity_entry["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def _cache_ttls_in_wire_order(api_kwargs):
    """TTL of every cache breakpoint in Anthropic's processing order:
    tools -> system -> messages (content blocks included)."""
    ttls = []

    def _take(block):
        cc = block.get("cache_control") if isinstance(block, dict) else None
        if isinstance(cc, dict):
            ttls.append(cc.get("ttl", "5m"))

    for tool in api_kwargs.get("tools") or []:
        _take(tool)
    system = api_kwargs.get("system")
    for block in system if isinstance(system, list) else []:
        _take(block)
    for msg in api_kwargs.get("messages") or []:
        _take(msg)
        content = msg.get("content") if isinstance(msg, dict) else None
        for block in content if isinstance(content, list) else []:
            _take(block)
    return ttls


def _assert_no_1h_after_5m(api_kwargs):
    ttls = _cache_ttls_in_wire_order(api_kwargs)
    if "5m" in ttls:
        assert "1h" not in ttls[ttls.index("5m"):], ttls


def _tool_loop_cache_kwargs(basic_api_kwargs, tool_marker):
    """hermes-agent 0.21.x tool-loop layout: last tool carries a marker."""
    kwargs = copy.deepcopy(basic_api_kwargs)
    kwargs["tools"] = [
        {"name": "terminal", "description": "t", "input_schema": {"type": "object"}},
        {
            "name": "read_file",
            "description": "r",
            "input_schema": {"type": "object"},
            "cache_control": dict(tool_marker),
        },
    ]
    return kwargs


def test_identity_follows_5m_tool_marker(basic_api_kwargs):
    """0.21.x + default cache_ttl=5m: a 1h identity after a 5m tool marker
    is a hard 400 ("a ttl='1h' cache_control block must not come after a
    ttl='5m' cache_control block").  Identity must drop to 5m."""
    kwargs = _tool_loop_cache_kwargs(basic_api_kwargs, {"type": "ephemeral"})

    apply_claude_code_bypass(kwargs, "2.1.217")

    assert kwargs["system"][1]["cache_control"] == {"type": "ephemeral"}
    _assert_no_1h_after_5m(kwargs)


def test_identity_follows_explicit_5m_tool_marker(basic_api_kwargs):
    kwargs = _tool_loop_cache_kwargs(
        basic_api_kwargs, {"type": "ephemeral", "ttl": "5m"}
    )

    apply_claude_code_bypass(kwargs, "2.1.217")

    assert kwargs["system"][1]["cache_control"] == {"type": "ephemeral"}
    _assert_no_1h_after_5m(kwargs)


def test_identity_keeps_1h_after_1h_tool_marker(basic_api_kwargs):
    kwargs = _tool_loop_cache_kwargs(
        basic_api_kwargs, {"type": "ephemeral", "ttl": "1h"}
    )

    apply_claude_code_bypass(kwargs, "2.1.217")

    assert kwargs["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    _assert_no_1h_after_5m(kwargs)


def test_identity_marker_is_not_shared_between_requests(basic_api_kwargs):
    """The marker dict must be a fresh copy; mutating one request's marker
    must not leak into the module default or the next request."""
    first = copy.deepcopy(basic_api_kwargs)
    apply_claude_code_bypass(first, "2.1.217")
    first["system"][1]["cache_control"]["ttl"] = "mutated"

    second = copy.deepcopy(basic_api_kwargs)
    apply_claude_code_bypass(second, "2.1.217")
    assert second["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


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
# ── _strip_thinking_from_replay tests (v1.5.6) ──────────────────────────────


def test_strip_thinking_from_replay_removes_unsigned_thinking_blocks():
    """Strip unsigned thinking blocks (legacy messages without raw content
    preservation) but preserve signed ones (from _anthropic_raw_content
    fast path with valid block order)."""
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                # Unsigned thinking — should be stripped (legacy slow path)
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "response"},
            ],
        },
        {"role": "user", "content": "next"},
        {
            "role": "assistant",
            "content": [
                # Signed thinking — should be preserved (fast path, valid order)
                {"type": "thinking", "thinking": "private", "signature": "sig1"},
                {"type": "text", "text": "second response"},
            ],
        },
    ]

    _strip_thinking_from_replay(messages)

    # First assistant (unsigned): thinking stripped
    assert not any(
        b.get("type") == "thinking" for b in messages[1]["content"]
    )
    # Second assistant (signed): thinking preserved
    assert any(
        b.get("type") == "thinking" and b.get("signature") == "sig1"
        for b in messages[3]["content"]
    )


def test_strip_thinking_from_replay_unsigned_thinking_only_message():
    """Edge case: when ALL content blocks are unsigned thinking, a placeholder
    text block must replace them to avoid empty content (Anthropic rejects it)."""
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                # No signature → unsigned (legacy), should be stripped
                {"type": "thinking", "thinking": "private"},
            ],
        },
    ]

    _strip_thinking_from_replay(messages)

    assistant = messages[1]
    assert len(assistant["content"]) == 1
    assert assistant["content"][0]["type"] == "text"
    assert assistant["content"][0]["text"] == "(thinking elided)"


def test_strip_thinking_from_replay_signed_thinking_preserved():
    """Signed thinking blocks should be preserved (order is correct from
    _anthropic_raw_content fast path)."""
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "private", "signature": "sig"},
                {"type": "tool_use", "id": "t1", "name": "terminal", "input": {}},
            ],
        },
    ]

    _strip_thinking_from_replay(messages)

    # Signed thinking preserved
    assistant = messages[1]
    assert len(assistant["content"]) == 2
    assert assistant["content"][0]["type"] == "thinking"
    assert assistant["content"][0]["signature"] == "sig"


def test_strip_thinking_from_replay_leaves_non_assistant():
    """User and tool messages should be untouched."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "tool", "content": "result"},
    ]
    original = copy.deepcopy(messages)

    _strip_thinking_from_replay(messages)

    assert messages == original
