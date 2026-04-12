# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedParameter=false, reportArgumentType=false, reportCallIssue=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false

import importlib
import importlib.machinery
import importlib.util
import sys
import types

import pytest


def _clear_bypass_finders():
    sys.meta_path[:] = [
        finder
        for finder in sys.meta_path
        if finder.__class__.__name__ != "_ClaudeCodeBypassFinder"
    ]


@pytest.fixture
def hook_module(monkeypatch):
    _clear_bypass_finders()
    module = importlib.import_module("sitecustomize_hook")
    module = importlib.reload(module)
    _clear_bypass_finders()
    yield module
    _clear_bypass_finders()


def test_install_hook_registers_finder(hook_module):
    hook_module._install_hook()

    assert any(
        finder.__class__.__name__ == "_ClaudeCodeBypassFinder"
        for finder in sys.meta_path
    )


def test_finder_only_targets_agent_anthropic_adapter(hook_module, monkeypatch):
    calls = []

    class Loader:
        def exec_module(self, module):
            module.loaded = True

    spec = importlib.machinery.ModuleSpec(hook_module._TARGET_MODULE, Loader())

    def fake_find_spec(fullname):
        calls.append(fullname)
        return spec if fullname == hook_module._TARGET_MODULE else None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    hook_module._install_hook()
    finder = next(
        finder
        for finder in sys.meta_path
        if finder.__class__.__name__ == "_ClaudeCodeBypassFinder"
    )

    assert finder.find_spec("some.other.module") is None
    found_spec = finder.find_spec(hook_module._TARGET_MODULE)

    assert found_spec is spec
    assert calls == [hook_module._TARGET_MODULE]


def test_finder_sets_patched_flag_and_stops_repatching(hook_module, monkeypatch):
    applied = []

    class Loader:
        def exec_module(self, module):
            module.loaded = True

    spec = importlib.machinery.ModuleSpec(hook_module._TARGET_MODULE, Loader())

    def fake_find_spec(fullname):
        return spec if fullname == hook_module._TARGET_MODULE else None

    bypass_module = types.SimpleNamespace(
        apply_patches=lambda module: applied.append(module.__name__)
    )

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setitem(sys.modules, "anthropic_billing_bypass", bypass_module)

    hook_module._install_hook()
    finder = next(
        finder
        for finder in sys.meta_path
        if finder.__class__.__name__ == "_ClaudeCodeBypassFinder"
    )

    found_spec = finder.find_spec(hook_module._TARGET_MODULE)
    assert found_spec is spec

    module = types.ModuleType(hook_module._TARGET_MODULE)
    found_spec.loader.exec_module(module)

    assert finder._patched is True
    assert applied == [hook_module._TARGET_MODULE]
    assert finder.find_spec(hook_module._TARGET_MODULE) is None
