# pyright: reportPrivateUsage=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportArgumentType=false

import copy

from anthropic_billing_bypass import (
    _SYSTEM_IDENTITY,
    _fix_temperature_for_oauth_adaptive,
    apply_claude_code_bypass,
)


def test_apply_claude_code_bypass_injects_billing_header_and_preserves_identity(
    basic_api_kwargs,
):
    apply_claude_code_bypass(basic_api_kwargs, "2.1.90")

    system = basic_api_kwargs["system"]
    assert system[0]["text"].startswith("x-anthropic-billing-header: ")
    assert system[1]["text"] == _SYSTEM_IDENTITY


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
