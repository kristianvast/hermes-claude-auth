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
BOOTSTRAP_NAME="_hermes_claude_auth_bootstrap.py"
PTH_NAME="hermes_claude_auth.pth"

if [ ! -d "$HERMES_AGENT_DIR" ]; then
    printf "${RED}[✗] hermes-agent not found at %s${RESET}\n" "$HERMES_AGENT_DIR"
    printf "    Install hermes-agent first: https://github.com/nousresearch/hermes-agent\n"
    exit 1
fi

if [ -n "${HERMES_VENV:-}" ] && [ -d "$HERMES_VENV" ]; then
    VENV_DIR="$HERMES_VENV"
elif [ -d "$HERMES_AGENT_DIR/venv" ]; then
    VENV_DIR="$HERMES_AGENT_DIR/venv"
elif [ -d "$HERMES_AGENT_DIR/.venv" ]; then
    VENV_DIR="$HERMES_AGENT_DIR/.venv"
else
    printf "${RED}[✗] No virtualenv found in %s (checked venv/, .venv/, and \$HERMES_VENV)${RESET}\n" "$HERMES_AGENT_DIR"
    exit 1
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then VENV_PYTHON="$VENV_DIR/bin/python3"; fi
if [ ! -x "$VENV_PYTHON" ]; then
    printf "${RED}[✗] Python not found at %s${RESET}\n" "$VENV_PYTHON"
    exit 1
fi

SITE_PACKAGES="$("$VENV_PYTHON" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())")"
if [ ! -d "$SITE_PACKAGES" ]; then
    printf "${RED}[✗] site-packages directory does not exist: %s${RESET}\n" "$SITE_PACKAGES"
    exit 1
fi

mkdir -p "$PATCHES_DIR"
cp "$SCRIPT_DIR/anthropic_billing_bypass.py" "$PATCHES_DIR/anthropic_billing_bypass.py"
chmod 644 "$PATCHES_DIR/anthropic_billing_bypass.py"
printf "${GREEN}[✓] Copied patch to %s/${RESET}\n" "$PATCHES_DIR"

# Clear any stale bytecode in the patches dir so the new file is imported fresh
# next time the hook fires.  Harmless if the cache doesn't exist.
rm -rf "$PATCHES_DIR/__pycache__" 2>/dev/null || true

# --- Install hook via .pth shim ------------------------------------------------
#
# .pth files are processed by site.py *before* it imports sitecustomize, on every
# platform.  Earlier versions of this installer wrote a sitecustomize.py into
# site-packages directly, which fails silently on Debian/Ubuntu because those
# distros ship /usr/lib/pythonX.Y/sitecustomize.py for apport and it wins import
# priority over the venv-local one (the venv's never gets imported).  Routing
# through a .pth shim avoids the collision and works on every distro.

BOOTSTRAP_PATH="$SITE_PACKAGES/$BOOTSTRAP_NAME"
PTH_PATH="$SITE_PACKAGES/$PTH_NAME"

cp "$SCRIPT_DIR/$BOOTSTRAP_NAME" "$BOOTSTRAP_PATH"
chmod 644 "$BOOTSTRAP_PATH"
printf "${GREEN}[✓] Installed bootstrap module into %s${RESET}\n" "$BOOTSTRAP_PATH"

cp "$SCRIPT_DIR/$PTH_NAME" "$PTH_PATH"
chmod 644 "$PTH_PATH"
printf "${GREEN}[✓] Installed .pth shim into %s${RESET}\n" "$PTH_PATH"

# Migrate an existing sitecustomize.py-style install, if any.
#
# - If we placed it there in a previous install (marker present), remove it and
#   restore the original pre-existing sitecustomize.py from backup if one was
#   saved.  Without the .pth shim, that file is dead weight on Debian/Ubuntu
#   anyway, and on Fedora having both works but ours is now redundant.
# - If a non-ours sitecustomize.py exists, leave it untouched.
SITECUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"
LEGACY_BACKUP="$SITECUSTOMIZE.pre-hermes-claude-auth"
if [ -f "$SITECUSTOMIZE" ] && grep -q "$MARKER" "$SITECUSTOMIZE"; then
    if [ -f "$LEGACY_BACKUP" ]; then
        mv "$LEGACY_BACKUP" "$SITECUSTOMIZE"
        printf "${YELLOW}[~] Migrated legacy sitecustomize.py install — restored your original from backup${RESET}\n"
    else
        # No prior sitecustomize.py existed; remove ours so future runs of /usr/bin/python
        # don't have a stray hook in this venv after this package is uninstalled.
        rm -f "$SITECUSTOMIZE"
        printf "${YELLOW}[~] Migrated legacy sitecustomize.py install — removed superseded hook${RESET}\n"
    fi
fi

# Clear any stale bytecode for our installed files so the next interpreter
# startup re-imports them.
find "$SITE_PACKAGES" \
    -maxdepth 3 \
    \( -name '_hermes_claude_auth_bootstrap*.pyc' -o -name 'sitecustomize*.pyc' \) \
    -delete 2>/dev/null || true

# macOS: hermes-agent reads Claude subscription credentials from
# ~/.claude/.credentials.json, but Claude Code on macOS stores them in
# Keychain only.  Mirror the Keychain entry into the file so auth works
# out of the box.  No-op on Linux (Claude Code writes the file directly).
if [ "$(uname -s)" = "Darwin" ]; then
    CRED_FILE="$HOME/.claude/.credentials.json"
    if KEYCHAIN_CRED="$(security find-generic-password -s 'Claude Code-credentials' -w 2>/dev/null)"; then
        mkdir -p "$(dirname "$CRED_FILE")"
        if [ ! -f "$CRED_FILE" ] || [ "$(cat "$CRED_FILE" 2>/dev/null)" != "$KEYCHAIN_CRED" ]; then
            printf '%s' "$KEYCHAIN_CRED" > "$CRED_FILE"
            chmod 600 "$CRED_FILE"
            printf "${GREEN}[✓] Mirrored Claude Code credentials from Keychain → %s${RESET}\n" "$CRED_FILE"
        else
            printf "${GREEN}[✓] Claude Code credentials file already matches Keychain${RESET}\n"
        fi
    elif [ ! -f "$CRED_FILE" ]; then
        printf "${YELLOW}[!] macOS detected but no 'Claude Code-credentials' Keychain entry found${RESET}\n"
        printf "    Run: claude auth login --claudeai\n"
    fi
fi

if systemctl --user is-active hermes-gateway.service >/dev/null 2>&1; then
    systemctl --user restart hermes-gateway.service
    printf "${GREEN}[✓] Restarted hermes-gateway.service${RESET}\n"
else
    printf "${YELLOW}[!] hermes-gateway not running — restart manually when ready${RESET}\n"
fi

printf "\n${GREEN}Installation complete.${RESET}\n"
printf "  Patch:     %s/anthropic_billing_bypass.py\n" "$PATCHES_DIR"
printf "  Bootstrap: %s\n" "$BOOTSTRAP_PATH"
printf "  .pth shim: %s\n" "$PTH_PATH"
printf "  Venv:      %s\n" "$VENV_DIR"
