"""Auto-wait is scoped to the Claude subscription (provider == "anthropic").

A 429 from another provider (openai-codex usage cap, openrouter, ...) must
propagate on the first attempt so Hermes core's fallback_providers engage,
instead of the wrapper sleeping on a window it cannot observe.
"""

import types

import anthropic_billing_bypass as bp


class _Fake429(Exception):
    def __init__(self):
        super().__init__("HTTP 429: The usage limit has been reached")
        self.status_code = 429
        self.response = types.SimpleNamespace(headers={}, status_code=429)


def _run(monkeypatch, provider):
    sleeps = []
    monkeypatch.setattr(bp, "_rl_sleep_until_reset", lambda a, e, n: sleeps.append(n) or n < 2)
    calls = []

    def original(self):
        calls.append(1)
        raise _Fake429()

    wrapped = bp._rl_wrap_call(original)
    agent = types.SimpleNamespace(provider=provider)
    try:
        wrapped(agent)
    except _Fake429:
        pass
    return calls, sleeps


def test_non_anthropic_429_propagates_without_waiting(monkeypatch):
    calls, sleeps = _run(monkeypatch, "openai-codex")
    assert len(calls) == 1 and sleeps == []


def test_anthropic_429_still_waits_and_retries(monkeypatch):
    calls, sleeps = _run(monkeypatch, "anthropic")
    assert sleeps == [1, 2] and len(calls) == 2
