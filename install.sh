#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_AGENT_DIR="$HOME/.hermes/hermes-agent"
PATCHES_DIR="$HOME/.hermes/patches"
MARKER="# hermes-claude-auth managed"

if [ ! -d "$HERMES_AGENT_DIR" ]; then
    printf "${RED}[\xe2\x9c\x97] hermes-agent not found at %s${RESET}\n" "$HERMES_AGENT_DIR"
    printf "    Install hermes-agent first: https://github.com/nousresearch/hermes-agent\n"
    exit 1
fi

# ---------------------------------------------------------------------------
# Install the patch payload to ~/.hermes/patches/
# ---------------------------------------------------------------------------

mkdir -p "$PATCHES_DIR"
cp "$SCRIPT_DIR/anthropic_billing_bypass.py" "$PATCHES_DIR/anthropic_billing_bypass.py"
chmod 644 "$PATCHES_DIR/anthropic_billing_bypass.py"
printf "${GREEN}[\xe2\x9c\x93] Copied patch to %s/${RESET}\n" "$PATCHES_DIR"

# ---------------------------------------------------------------------------
# Install sitecustomize.py into every Python interpreter that can import
# hermes-agent.
#
# This is more than just the venv. hermes-agent is often installed
# editable into one Python (e.g. mise's python3.11) while the gateway
# daemon runs out of ~/.hermes/hermes-agent/venv. The CLI/TUI use the
# editable install; the daemon uses the venv. Both must load the hook.
#
# Strategy: collect candidate interpreters (venv + every python3* on
# PATH + $HERMES_VENV if set + $HERMES_PYTHON if set), ask each one if
# `import agent.anthropic_adapter` works, and install only into the
# winners' site-packages.
# ---------------------------------------------------------------------------

install_hook_into() {
    local py="$1"
    local site_packages
    if ! site_packages="$("$py" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())" 2>/dev/null)"; then
        return 1
    fi
    if [ -z "$site_packages" ] || [ ! -d "$site_packages" ]; then
        return 1
    fi

    local sitecustomize="$site_packages/sitecustomize.py"
    if [ -f "$sitecustomize" ] && ! grep -q "$MARKER" "$sitecustomize" 2>/dev/null; then
        local backup="$sitecustomize.pre-hermes-claude-auth"
        if [ ! -f "$backup" ]; then
            cp "$sitecustomize" "$backup"
            printf "${YELLOW}[!] Backed up existing sitecustomize.py to %s${RESET}\n" "$backup"
        fi
    fi

    cp "$SCRIPT_DIR/sitecustomize_hook.py" "$sitecustomize"
    chmod 644 "$sitecustomize"
    printf "${GREEN}[\xe2\x9c\x93] Installed hook into %s${RESET}\n" "$sitecustomize"
}

can_import_hermes_agent() {
    "$1" -c "import agent.anthropic_adapter" >/dev/null 2>&1
}

resolve_python() {
    # Given a directory, resolve $dir/bin/python -> $dir/bin/python3 fallback,
    # printing the first executable found. Empty output if neither exists.
    local dir="$1"
    if [ -x "$dir/bin/python" ]; then
        echo "$dir/bin/python"
    elif [ -x "$dir/bin/python3" ]; then
        echo "$dir/bin/python3"
    fi
}

declare -a CANDIDATES=()

# Explicit overrides first.
if [ -n "${HERMES_PYTHON:-}" ] && [ -x "$HERMES_PYTHON" ]; then
    CANDIDATES+=("$HERMES_PYTHON")
fi
if [ -n "${HERMES_VENV:-}" ] && [ -d "$HERMES_VENV" ]; then
    py="$(resolve_python "$HERMES_VENV")"
    [ -n "$py" ] && CANDIDATES+=("$py")
fi

# Standard hermes-agent venv locations.
for venv_subdir in venv .venv; do
    if [ -d "$HERMES_AGENT_DIR/$venv_subdir" ]; then
        py="$(resolve_python "$HERMES_AGENT_DIR/$venv_subdir")"
        [ -n "$py" ] && CANDIDATES+=("$py")
    fi
done

# Whatever python the `hermes` CLI shim is bound to.
if command -v hermes >/dev/null 2>&1; then
    shebang="$(head -n 1 "$(command -v hermes)" 2>/dev/null || true)"
    shebang_py="${shebang#\#!}"
    shebang_py="${shebang_py%% *}"
    if [ -x "$shebang_py" ]; then
        CANDIDATES+=("$shebang_py")
    fi
fi

# Every python3* on PATH.
for cmd in python python3 python3.11 python3.12 python3.13; do
    if py="$(command -v "$cmd" 2>/dev/null)"; then
        CANDIDATES+=("$py")
    fi
done

# Deduplicate by site-packages dir (NOT realpath of the binary) — a venv's
# python can be a symlink to a system/mise python, but the two have distinct
# site-packages and the hook must land in each. Filter to interpreters that
# can actually import hermes-agent.
declare -a INSTALL_TARGETS=()
declare -a SEEN=()
for py in "${CANDIDATES[@]}"; do
    site="$("$py" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())" 2>/dev/null || echo "")"
    [ -z "$site" ] && continue
    skip=0
    for s in "${SEEN[@]:-}"; do
        [ "$s" = "$site" ] && skip=1 && break
    done
    [ "$skip" -eq 1 ] && continue
    SEEN+=("$site")
    if can_import_hermes_agent "$py"; then
        INSTALL_TARGETS+=("$py")
    fi
done

if [ "${#INSTALL_TARGETS[@]}" -eq 0 ]; then
    printf "${RED}[\xe2\x9c\x97] No Python interpreter on PATH can import hermes-agent.${RESET}\n"
    printf "    Tried: %s\n" "${SEEN[*]:-(none)}"
    printf "    Set HERMES_PYTHON=/path/to/python or HERMES_VENV=/path/to/venv and re-run.\n"
    exit 1
fi

for py in "${INSTALL_TARGETS[@]}"; do
    install_hook_into "$py"
done

# ---------------------------------------------------------------------------
# macOS: mirror Claude Code-credentials Keychain entry into the credentials
# file hermes-agent reads from.
# ---------------------------------------------------------------------------

if [ "$(uname -s)" = "Darwin" ]; then
    CRED_FILE="$HOME/.claude/.credentials.json"
    if KEYCHAIN_CRED="$(security find-generic-password -s 'Claude Code-credentials' -w 2>/dev/null)"; then
        mkdir -p "$(dirname "$CRED_FILE")"
        if [ ! -f "$CRED_FILE" ] || [ "$(cat "$CRED_FILE" 2>/dev/null)" != "$KEYCHAIN_CRED" ]; then
            printf '%s' "$KEYCHAIN_CRED" > "$CRED_FILE"
            chmod 600 "$CRED_FILE"
            printf "${GREEN}[\xe2\x9c\x93] Mirrored Claude Code credentials from Keychain \xe2\x86\x92 %s${RESET}\n" "$CRED_FILE"
        else
            printf "${GREEN}[\xe2\x9c\x93] Claude Code credentials file already matches Keychain${RESET}\n"
        fi
    elif [ ! -f "$CRED_FILE" ]; then
        printf "${YELLOW}[!] macOS detected but no 'Claude Code-credentials' Keychain entry found${RESET}\n"
        printf "    Run: claude auth login --claudeai\n"
    fi
fi

# ---------------------------------------------------------------------------
# Restart hermes-gateway if it's managed by systemd.
# ---------------------------------------------------------------------------

if systemctl --user is-active hermes-gateway.service >/dev/null 2>&1; then
    systemctl --user restart hermes-gateway.service
    printf "${GREEN}[\xe2\x9c\x93] Restarted hermes-gateway.service${RESET}\n"
else
    printf "${YELLOW}[!] hermes-gateway not running under systemd \xe2\x80\x94 restart manually when ready${RESET}\n"
fi

printf "\n${GREEN}Installation complete.${RESET}\n"
printf "  Patch:           %s/anthropic_billing_bypass.py\n" "$PATCHES_DIR"
printf "  Hook installed into %d interpreter(s):\n" "${#INSTALL_TARGETS[@]}"
for py in "${INSTALL_TARGETS[@]}"; do
    printf "    %s\n" "$py"
done
