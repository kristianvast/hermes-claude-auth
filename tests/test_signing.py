# pyright: reportPrivateUsage=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

import hashlib

from anthropic_billing_bypass import (
    _build_billing_header_value,
    _compute_cch,
    _compute_version_suffix,
    _extract_first_user_message_text,
)


def test_extract_first_user_message_text_with_string_content(simple_messages):
    assert _extract_first_user_message_text(simple_messages) == "hello world"


def test_extract_first_user_message_text_with_text_block(complex_messages):
    assert _extract_first_user_message_text(complex_messages) == "hello world"


def test_extract_first_user_message_text_with_image_block_only_returns_empty_string():
    messages = [{"role": "user", "content": [{"type": "image", "source": {}}]}]
    assert _extract_first_user_message_text(messages) == ""


def test_extract_first_user_message_text_with_no_user_message_returns_empty_string():
    messages = [{"role": "assistant", "content": "hello"}]
    assert _extract_first_user_message_text(messages) == ""


def test_extract_first_user_message_text_uses_first_user_message_only():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ]
    assert _extract_first_user_message_text(messages) == "first"


def test_extract_first_user_message_text_with_empty_messages_returns_empty_string():
    assert _extract_first_user_message_text([]) == ""


def test_compute_cch_known_values():
    assert _compute_cch("hello world") == "b94d2"
    assert _compute_cch("") == "e3b0c"


def test_compute_version_suffix_pads_short_text():
    expected = hashlib.sha256(b"59cf53e54c78e002.1.90").hexdigest()[:3]
    assert _compute_version_suffix("abcde", "2.1.90") == expected


def test_compute_version_suffix_samples_long_enough_text():
    text = "abcdefghijklmnopqrstuvwxyz"
    sampled = f"{text[4]}{text[7]}{text[20]}"
    expected = hashlib.sha256(
        f"59cf53e54c78{sampled}2.1.90".encode("utf-8")
    ).hexdigest()[:3]
    assert _compute_version_suffix(text, "2.1.90") == expected


def test_compute_version_suffix_known_value_for_hello_world():
    expected = hashlib.sha256(b"59cf53e54c78oo02.1.90").hexdigest()[:3]
    assert _compute_version_suffix("hello world", "2.1.90") == expected


def test_build_billing_header_value_format(simple_messages):
    version = "2.1.90"
    entrypoint = "cli"
    suffix = _compute_version_suffix("hello world", version)
    cch = _compute_cch("hello world")

    assert _build_billing_header_value(simple_messages, version, entrypoint) == (
        f"x-anthropic-billing-header: cc_version={version}.{suffix}; "
        f"cc_entrypoint={entrypoint}; cch={cch};"
    )
