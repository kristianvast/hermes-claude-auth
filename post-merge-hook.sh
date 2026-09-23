#!/bin/sh
# hermes-claude-auth post-merge hook (Linux/macOS).
#
# Installed by install.sh into $HERMES_HOME/hermes-agent/.git/hooks/post-merge.
# `hermes update` pulls hermes-agent and may rebuild the venv, which deletes
# the .pth shim + bootstrap module from site-packages. After every merge this
# hook runs `install.sh --check`; only if something is missing or drifted does
# it re-run `install.sh --post-update`. When everything is intact it does
# nothing (no gateway restart).
#
# Never fatal: always exits 0 so it can't break the update.

set -u

for dir in "${HERMES_CLAUDE_AUTH_DIR:-}" "$HOME/hermes-claude-auth"; do
    if [ -n "$dir" ] && [ -x "$dir/install.sh" ]; then
        if ! "$dir/install.sh" --check >/dev/null 2>&1; then
            echo "[hermes-claude-auth] Recovering Claude Code bypass via $dir/install.sh --post-update" >&2
            "$dir/install.sh" --post-update >&2 || \
                echo "[hermes-claude-auth] recovery failed; run $dir/install.sh by hand" >&2
        fi
        exit 0
    fi
done

echo "[hermes-claude-auth] installer not found (set HERMES_CLAUDE_AUTH_DIR or clone to ~/hermes-claude-auth); skipping recovery" >&2
exit 0
