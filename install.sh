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
if [ ! -x "$VENV_PYTHON" ]; then
    printf "${RED}[✗] Python not found at %s${RESET}\n" "$VENV_PYTHON"
    exit 1
fi

SITE_PACKAGES="$("$VENV_PYTHON" -c "import site; print(site.getsitepackages()[0])")"
if [ ! -d "$SITE_PACKAGES" ]; then
    printf "${RED}[✗] site-packages directory does not exist: %s${RESET}\n" "$SITE_PACKAGES"
    exit 1
fi

mkdir -p "$PATCHES_DIR"
cp "$SCRIPT_DIR/anthropic_billing_bypass.py" "$PATCHES_DIR/anthropic_billing_bypass.py"
chmod 644 "$PATCHES_DIR/anthropic_billing_bypass.py"
printf "${GREEN}[✓] Copied patch to %s/${RESET}\n" "$PATCHES_DIR"

SITECUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"

if [ ! -f "$SITECUSTOMIZE" ]; then
    cp "$SCRIPT_DIR/sitecustomize_hook.py" "$SITECUSTOMIZE"
elif grep -q "$MARKER" "$SITECUSTOMIZE"; then
    cp "$SCRIPT_DIR/sitecustomize_hook.py" "$SITECUSTOMIZE"
else
    BACKUP="$SITECUSTOMIZE.pre-hermes-claude-auth"
    cp "$SITECUSTOMIZE" "$BACKUP"
    printf "${YELLOW}[!] Backed up existing sitecustomize.py to %s${RESET}\n" "$BACKUP"
    cp "$SCRIPT_DIR/sitecustomize_hook.py" "$SITECUSTOMIZE"
fi

chmod 644 "$SITECUSTOMIZE"
printf "${GREEN}[✓] Installed hook into %s${RESET}\n" "$SITECUSTOMIZE"

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
printf "  Patch:  %s/anthropic_billing_bypass.py\n" "$PATCHES_DIR"
printf "  Hook:   %s\n" "$SITECUSTOMIZE"
printf "  Venv:   %s\n" "$VENV_DIR"
