"""
hermes-claude-auth bootstrap — robust loader for the OAuth bypass.
==================================================================

WHY THIS EXISTS
---------------
The original installer dropped ``sitecustomize.py`` into the venv's
site-packages.  That fails on Homebrew Pythons: Homebrew ships its own
``sitecustomize.py`` inside the stdlib directory
(``.../python3.11/lib/python3.11/sitecustomize.py``) which appears *earlier*
on ``sys.path`` than the venv's site-packages.  Python imports only the
*first* ``sitecustomize`` module it finds, so the venv hook never ran and
every OAuth request went out unpatched (→ HTTP 400 "Third-party apps now
draw from extra usage, not plan limits").

A ``.pth`` file does not have this problem: ``site.py`` executes the
``import`` line of *every* ``.pth`` file in *every* site directory, so our
hook always loads regardless of any ``sitecustomize`` shadowing.  The
companion ``hermes_claude_auth.pth`` (installed into the venv site-packages)
adds this directory to ``sys.path`` and imports this module.

This module installs a lazy ``MetaPathFinder`` that applies the billing
bypass the moment ``agent.anthropic_adapter`` is imported (which happens
long after interpreter startup, when the gateway boots).

To disable: delete ``hermes_claude_auth.pth`` from the venv site-packages
(and optionally this file + ``anthropic_billing_bypass.py``), then restart
hermes-gateway.
"""
# hermes-claude-auth managed — do not remove this marker

from __future__ import annotations

import os
import sys

_PATCHES_DIR = os.environ.get(
    "HERMES_PATCHES_DIR",
    os.path.expanduser("~/.hermes/patches"),
)
_TARGET_MODULE = "agent.anthropic_adapter"


def install_hook() -> None:
    # Idempotent: skip if one of our finders is already registered (guards
    # against double-installation when both the .pth and a leftover
    # sitecustomize import this module in the same interpreter).
    if any(
        type(f).__name__ == "_ClaudeCodeBypassFinder" for f in sys.meta_path
    ):
        return

    if os.path.isdir(_PATCHES_DIR) and _PATCHES_DIR not in sys.path:
        sys.path.insert(0, _PATCHES_DIR)

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
    install_hook()
except Exception as _exc:  # never break interpreter startup
    sys.stderr.write(f"[hermes-claude-auth] hook install failed: {_exc}\n")
