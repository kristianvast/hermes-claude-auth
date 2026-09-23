#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_AGENT_DIR="$HERMES_HOME/hermes-agent"
PATCHES_DIR="$HERMES_HOME/patches"
MARKER="# hermes-claude-auth managed"
BOOTSTRAP_NAME="_hermes_claude_auth_bootstrap.py"
PTH_NAME="hermes_claude_auth.pth"

# ── Parse flags ─────────────────────────────────────────────────────
POST_UPDATE=false
CHECK_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --post-update) POST_UPDATE=true ;;
        --check)       CHECK_ONLY=true ;;
        *) ;;
    esac
done

# ── Pre-flight checks ───────────────────────────────────────────────
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

SITECUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"

# ── --check mode: verify patch integrity ────────────────────────────
if $CHECK_ONLY; then
    ALL_OK=true
    for f in "$PATCHES_DIR/anthropic_billing_bypass.py"; do
        if [[ -f "$f" ]]; then
            printf "${GREEN}[✓] %s${RESET}\n" "$f"
        else
            printf "${RED}[✗] MISSING: %s${RESET}\n" "$f"
            ALL_OK=false
        fi
    done

    # The hook is loaded through a .pth shim that imports the bootstrap module
    # (see install_hook_into below). A sitecustomize.py carrying the marker is
    # also accepted: the Antigravity coexistence loader embeds the Claude hook
    # that way.
    CHECK_PTH="$SITE_PACKAGES/$PTH_NAME"
    CHECK_BOOTSTRAP="$SITE_PACKAGES/$BOOTSTRAP_NAME"
    if [[ -f "$CHECK_PTH" ]] && grep -q "import ${BOOTSTRAP_NAME%.py}" "$CHECK_PTH" \
        && [[ -f "$CHECK_BOOTSTRAP" ]] && grep -q "$MARKER" "$CHECK_BOOTSTRAP"; then
        printf "${GREEN}[✓] .pth hook present (%s)${RESET}\n" "$CHECK_PTH"
        if ! cmp -s "$CHECK_BOOTSTRAP" "$SCRIPT_DIR/$BOOTSTRAP_NAME"; then
            printf "${YELLOW}[!] DRIFT: %s differs from repo${RESET}\n" "$CHECK_BOOTSTRAP"
            ALL_OK=false
        fi
    elif [[ -f "$SITECUSTOMIZE" ]] && grep -q "$MARKER" "$SITECUSTOMIZE"; then
        printf "${GREEN}[✓] sitecustomize hook present (%s)${RESET}\n" "$SITECUSTOMIZE"
    else
        printf "${RED}[✗] hook MISSING: neither %s nor a managed sitecustomize.py found${RESET}\n" "$CHECK_PTH"
        ALL_OK=false
    fi

    POST_MERGE_HOOK="$HERMES_AGENT_DIR/.git/hooks/post-merge"
    if [[ -f "$POST_MERGE_HOOK" && -x "$POST_MERGE_HOOK" ]] \
        && grep -q "Recovering Claude Code bypass" "$POST_MERGE_HOOK" 2>/dev/null; then
        printf "${GREEN}[✓] auto-recovery hook present${RESET}\n"
    elif [[ -d "$HERMES_AGENT_DIR/.git/hooks" ]]; then
        printf "${RED}[✗] auto-recovery hook MISSING, stale, or not executable${RESET}\n"
        ALL_OK=false
    else
        printf "${YELLOW}[!] hermes-agent git hooks directory not found; auto-recovery hook not checked${RESET}\n"
    fi

    # ── Content drift check: installed patch must match this repo ───────
    # File-existence alone does not catch the case where a runtime hotfix
    # was applied to the installed copy but never synced back to the repo
    # (or vice-versa). Compare byte-for-byte and warn on any drift.
    # NOTE: sitecustomize.py is intentionally NOT compared — it is shared
    # with other provider patches (e.g. Antigravity) and legitimately
    # diverges from this repo's single-provider hook.
    INSTALLED_PATCH="$PATCHES_DIR/anthropic_billing_bypass.py"
    REPO_PATCH="$SCRIPT_DIR/anthropic_billing_bypass.py"
    if [[ -f "$INSTALLED_PATCH" && -f "$REPO_PATCH" ]]; then
        if ! cmp -s "$INSTALLED_PATCH" "$REPO_PATCH"; then
            printf "${YELLOW}[!] DRIFT: anthropic_billing_bypass.py differs from repo (%s)${RESET}\n" "$REPO_PATCH"
            ALL_OK=false
        fi
    fi

    if $ALL_OK; then
        printf "\n${GREEN}Claude Code bypass patches intact.${RESET}\n"
        exit 0
    else
        printf "\n${YELLOW}Patches missing or drifted. To restore from repo: ./install.sh${RESET}\n"
        printf "${YELLOW}If the installed copy is the newer one, sync it back to the repo and commit instead.${RESET}\n"
        exit 1
    fi
fi

# ── Full install or post-update recovery ────────────────────────────
if $POST_UPDATE; then
    printf "${YELLOW}[post-update] Restoring Claude Code bypass after hermes update...${RESET}\n"
else
    printf "${YELLOW}[install] Installing Claude Code OAuth bypass...${RESET}\n"
fi

# ── Copy patch ──────────────────────────────────────────────────────
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

install_hook_into() {
    local site_packages="$1"
    local bootstrap_path="$site_packages/$BOOTSTRAP_NAME"
    local pth_path="$site_packages/$PTH_NAME"

    cp "$SCRIPT_DIR/$BOOTSTRAP_NAME" "$bootstrap_path" || return 1
    chmod 644 "$bootstrap_path"
    printf "${GREEN}[✓] Installed bootstrap module into %s${RESET}\n" "$bootstrap_path"

    cp "$SCRIPT_DIR/$PTH_NAME" "$pth_path" || return 1
    chmod 644 "$pth_path"
    printf "${GREEN}[✓] Installed .pth shim into %s${RESET}\n" "$pth_path"

    # Migrate an existing sitecustomize.py-style install, if any.
    #
    # - If we placed it there in a previous install (marker present), remove it and
    #   restore the original pre-existing sitecustomize.py from backup if one was
    #   saved.  Without the .pth shim, that file is dead weight on Debian/Ubuntu
    #   anyway, and on Fedora having both works but ours is now redundant.
    # - If a non-ours sitecustomize.py exists, leave it untouched.
    local sitecustomize="$site_packages/sitecustomize.py"
    local legacy_backup="$sitecustomize.pre-hermes-claude-auth"
    if [ -f "$sitecustomize" ] && grep -q "$MARKER" "$sitecustomize"; then
        if [ -f "$legacy_backup" ]; then
            mv "$legacy_backup" "$sitecustomize"
            printf "${YELLOW}[~] Migrated legacy sitecustomize.py install — restored your original from backup${RESET}\n"
        else
            # No prior sitecustomize.py existed; remove ours so future runs of /usr/bin/python
            # don't have a stray hook in this venv after this package is uninstalled.
            rm -f "$sitecustomize"
            printf "${YELLOW}[~] Migrated legacy sitecustomize.py install — removed superseded hook${RESET}\n"
        fi
    fi

    # Clear any stale bytecode for our installed files so the next interpreter
    # startup re-imports them.
    find "$site_packages" \
        -maxdepth 3 \
        \( -name '_hermes_claude_auth_bootstrap*.pyc' -o -name 'sitecustomize*.pyc' \) \
        -delete 2>/dev/null || true
}

site_packages_of() {
    "$1" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())" 2>/dev/null || true
}

BOOTSTRAP_PATH="$SITE_PACKAGES/$BOOTSTRAP_NAME"
PTH_PATH="$SITE_PACKAGES/$PTH_NAME"
install_hook_into "$SITE_PACKAGES"

# hermes-agent is frequently installed editable into a *different* interpreter
# than the gateway venv (e.g. the CLI runs from mise's python3.11 while the
# daemon runs from ~/.hermes/hermes-agent/venv).  Both must load the hook, so
# additionally install into any other interpreter that can import hermes-agent.
# Best-effort: a read-only or unwritable site-packages is skipped, never fatal.
# Ported from PR #21, adapted to the .pth mechanism.
INSTALLED_SITES="$SITE_PACKAGES"
for candidate in \
    "${HERMES_PYTHON:-}" \
    "$(command -v hermes >/dev/null 2>&1 && head -n 1 "$(command -v hermes)" 2>/dev/null | sed -n 's/^#!\([^ ]*\).*/\1/p')" \
    "$(command -v python 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"
do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    "$candidate" -c "import agent.anthropic_adapter" >/dev/null 2>&1 || continue
    candidate_site="$(site_packages_of "$candidate")"
    [ -n "$candidate_site" ] && [ -d "$candidate_site" ] || continue
    case ":$INSTALLED_SITES:" in
        *":$candidate_site:"*) continue ;;
    esac
    if [ ! -w "$candidate_site" ]; then
        printf "${YELLOW}[!] Skipping %s (not writable)${RESET}\n" "$candidate_site"
        continue
    fi
    if install_hook_into "$candidate_site"; then
        INSTALLED_SITES="$INSTALLED_SITES:$candidate_site"
    fi
done

# ── Verify patch ────────────────────────────────────────────────────
PATCH_CHECK=$("$VENV_PYTHON" -c "
import sys, os
sys.path.insert(0, os.path.expanduser('$PATCHES_DIR'))
try:
    import anthropic_billing_bypass
    v = getattr(anthropic_billing_bypass, '__version__', 'unknown')
    print(f'OK (v{v})')
except Exception as e:
    print(f'FAIL ({e})')
" 2>/dev/null || echo "FAIL (import error)")
printf "${GREEN}[✓] Patch integrity: %s${RESET}\n" "$PATCH_CHECK"

# ── Install auto-recovery git hook ──────────────────────────────────
GIT_HOOKS_DIR="$HERMES_AGENT_DIR/.git/hooks"
POST_MERGE_HOOK="$GIT_HOOKS_DIR/post-merge"
ANTIGRAVITY_HOOK="$SCRIPT_DIR/../hermes-google-antigravity-plugin/scripts/post-merge-hook.sh"
# Try sibling repo first, then look for standalone hook
if [ -f "$ANTIGRAVITY_HOOK" ]; then
    HOOK_SRC="$ANTIGRAVITY_HOOK"
elif [ -f "$SCRIPT_DIR/post-merge-hook.sh" ]; then
    HOOK_SRC="$SCRIPT_DIR/post-merge-hook.sh"
else
    HOOK_SRC=""
fi
if [ -d "$GIT_HOOKS_DIR" ] && [ -n "$HOOK_SRC" ] && [ -f "$HOOK_SRC" ]; then
    cp "$HOOK_SRC" "$POST_MERGE_HOOK"
    chmod +x "$POST_MERGE_HOOK"
    printf "${GREEN}[✓] Installed auto-recovery hook (post-merge)${RESET}\n"
fi

# ── macOS Keychain mirror ───────────────────────────────────────────
if [ "$(uname -s)" = "Darwin" ]; then
    CRED_FILE="$HOME/.claude/.credentials.json"
    if KEYCHAIN_CRED="$(security find-generic-password -s 'Claude Code-credentials' -w 2>/dev/null)"; then
        mkdir -p "$(dirname "$CRED_FILE")"
        if [ ! -f "$CRED_FILE" ] || [ "$(cat "$CRED_FILE" 2>/dev/null)" != "$KEYCHAIN_CRED" ]; then
            printf '%s' "$KEYCHAIN_CRED" >"$CRED_FILE"
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

# ── Restart gateway ─────────────────────────────────────────────────
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
