# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import pytest


@pytest.fixture
def simple_messages():
    return [{"role": "user", "content": "hello world"}]


@pytest.fixture
def complex_messages():
    return [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": "abc"}},
                {"type": "text", "text": "hello world"},
            ],
        }
    ]


@pytest.fixture
def basic_api_kwargs(simple_messages):
    return {
        "system": [
            {
                "type": "text",
                "text": "You are Claude Code, Anthropic's official CLI for Claude.\nStay helpful.",
            },
            {"type": "text", "text": "Extra system guidance"},
        ],
        "messages": [dict(message) for message in simple_messages],
        "model": "claude-opus-4-6-20260101",
    }
