"""
Bootstrap loader for hermes-claude-auth.
=======================================

This module is imported from a ``hermes_claude_auth.pth`` file installed into
the hermes-agent venv's site-packages by ``install.sh``. The ``.pth`` file adds
``~/.hermes/patches`` to ``sys.path`` and imports this module during Python's
site initialization, before any user code runs.

Using a dedicated ``.pth`` bootstrap avoids the fragility of a plain
``sitecustomize.py`` install: environments such as Homebrew Python may already
provide a global ``sitecustomize`` module that shadows the venv-local one and
prevents our hook from loading at all.

The bootstrap installs a MetaPathFinder that patches
``agent.anthropic_adapter`` immediately after import so the Anthropic OAuth
billing bypass is active without modifying hermes-agent source files.
"""

from __future__ import annotations

import os
import sys

_PATCHES_DIR = os.environ.get(
    "HERMES_PATCHES_DIR",
    os.path.expanduser("~/.hermes/patches"),
)
_TARGET_MODULE = "agent.anthropic_adapter"

if os.path.isdir(_PATCHES_DIR) and _PATCHES_DIR not in sys.path:
    sys.path.insert(0, _PATCHES_DIR)


def _install_hook() -> None:
    try:
        from importlib.abc import MetaPathFinder
        from importlib.util import find_spec
    except ImportError:
        return

    class _ClaudeCodeBypassFinder(MetaPathFinder):
        _patched = False

        def find_spec(self, fullname, path=None, target=None):  # type: ignore[override]
            if fullname != _TARGET_MODULE or self._patched:
                return None

            # Temporarily remove ourselves to avoid recursion during find_spec.
            if self in sys.meta_path:
                sys.meta_path.remove(self)
            try:
                spec = find_spec(fullname)
            finally:
                if self not in sys.meta_path:
                    sys.meta_path.insert(0, self)

            if spec is None or spec.loader is None:
                return None

            original_exec = getattr(spec.loader, "exec_module", None)
            if not callable(original_exec):
                return None

            finder = self

            def patched_exec(module):  # type: ignore[no-untyped-def]
                original_exec(module)
                finder._patched = True
                try:
                    import anthropic_billing_bypass

                    anthropic_billing_bypass.apply_patches(module)
                except Exception as exc:
                    import traceback

                    sys.stderr.write(
                        f"[hermes-claude-auth] bypass failed: "
                        f"{type(exc).__name__}: {exc}\n"
                    )
                    traceback.print_exc(file=sys.stderr)

            spec.loader.exec_module = patched_exec  # type: ignore[attr-defined]
            return spec

    sys.meta_path.insert(0, _ClaudeCodeBypassFinder())


try:
    _install_hook()
except Exception as _exc:
    sys.stderr.write(f"[hermes-claude-auth] hook install failed: {_exc}\n")
