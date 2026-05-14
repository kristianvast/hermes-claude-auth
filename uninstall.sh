#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

MARKER="# hermes-claude-auth managed"
BOOTSTRAP_NAME="_hermes_claude_auth_bootstrap.py"
PTH_NAME="hermes_claude_auth.pth"

PURGE=0

for arg in "$@"; do
  case "$arg" in
    --purge)
      PURGE=1
      ;;
    -h|--help)
      printf 'Usage: %s [--purge]\n' "$0"
      exit 0
      ;;
    *)
      printf '%b[!]%b Unknown argument: %s\n' "$RED" "$RESET" "$arg" >&2
      exit 1
      ;;
  esac
done

VENV_DIR=""
if [ -n "${HERMES_VENV:-}" ] && [ -d "${HERMES_VENV:-}" ]; then
  VENV_DIR="$HERMES_VENV"
elif [ -d "$HOME/.hermes/hermes-agent/venv" ]; then
  VENV_DIR="$HOME/.hermes/hermes-agent/venv"
elif [ -d "$HOME/.hermes/hermes-agent/.venv" ]; then
  VENV_DIR="$HOME/.hermes/hermes-agent/.venv"
fi

removed_pth=0
removed_bootstrap=0
removed_legacy_hook=0
restored_legacy_hook=0
removed_patch=0

if [ -z "$VENV_DIR" ]; then
  printf '%b[—]%b No hermes venv found, skipping hook removal\n' "$YELLOW" "$RESET"
else
  PYTHON_BIN="$VENV_DIR/bin/python"
  SITE_PACKAGES=""

  if [ -x "$PYTHON_BIN" ]; then
    SITE_PACKAGES="$($PYTHON_BIN -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || true)"
  fi

  if [ -z "$SITE_PACKAGES" ]; then
    printf '%b[—]%b Could not detect site-packages, skipping hook removal\n' "$YELLOW" "$RESET"
  else
    # Remove the .pth shim
    PTH_PATH="$SITE_PACKAGES/$PTH_NAME"
    if [ -e "$PTH_PATH" ]; then
      rm -f "$PTH_PATH"
      printf '%b[✓]%b Removed .pth shim from %s\n' "$GREEN" "$RESET" "$PTH_PATH"
      removed_pth=1
    fi

    # Remove the bootstrap module
    BOOTSTRAP_PATH="$SITE_PACKAGES/$BOOTSTRAP_NAME"
    if [ -e "$BOOTSTRAP_PATH" ]; then
      rm -f "$BOOTSTRAP_PATH"
      printf '%b[✓]%b Removed bootstrap module from %s\n' "$GREEN" "$RESET" "$BOOTSTRAP_PATH"
      removed_bootstrap=1
    fi

    # Clean up stale bytecode for both new and legacy files
    find "$SITE_PACKAGES" \
        -maxdepth 3 \
        \( -name '_hermes_claude_auth_bootstrap*.pyc' -o -name 'sitecustomize*.pyc' \) \
        -delete 2>/dev/null || true

    # Legacy: handle a sitecustomize.py left behind by an old install.
    SITE_CUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"
    BACKUP_FILE="$SITE_PACKAGES/sitecustomize.py.pre-hermes-claude-auth"

    if [ -e "$SITE_CUSTOMIZE" ] && grep -qF "$MARKER" "$SITE_CUSTOMIZE"; then
      if [ -e "$BACKUP_FILE" ]; then
        mv "$BACKUP_FILE" "$SITE_CUSTOMIZE"
        printf '%b[✓]%b Restored original sitecustomize.py from legacy backup\n' "$GREEN" "$RESET"
        restored_legacy_hook=1
      else
        rm -f "$SITE_CUSTOMIZE"
        printf '%b[✓]%b Removed legacy sitecustomize.py hook from %s\n' "$GREEN" "$RESET" "$SITE_PACKAGES"
        removed_legacy_hook=1
      fi
    fi
  fi
fi

if [ "$PURGE" -eq 1 ]; then
  PATCH_DIR="$HOME/.hermes/patches"
  PATCH_FILE="$PATCH_DIR/anthropic_billing_bypass.py"

  if [ -e "$PATCH_FILE" ]; then
    rm -f "$PATCH_FILE"
    printf '%b[✓]%b Removed patch from ~/.hermes/patches/\n' "$GREEN" "$RESET"
    removed_patch=1
  fi

  # Drop the pycache directory if it's still around
  if [ -d "$PATCH_DIR/__pycache__" ]; then
    rm -rf "$PATCH_DIR/__pycache__" 2>/dev/null || true
  fi

  if [ -d "$PATCH_DIR" ]; then
    empty=1
    for entry in "$PATCH_DIR"/* "$PATCH_DIR"/.[!.]* "$PATCH_DIR"/..?*; do
      [ -e "$entry" ] || continue
      empty=0
      break
    done
    if [ "$empty" -eq 1 ]; then
      rmdir "$PATCH_DIR" 2>/dev/null || true
    fi
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null; then
    systemctl --user restart hermes-gateway.service
  fi
fi

printf '%bSummary:%b\n' "$GREEN" "$RESET"
if [ "$removed_pth" -eq 1 ] || [ "$removed_bootstrap" -eq 1 ]; then
  printf '  - Removed .pth shim and bootstrap module\n'
fi
if [ "$restored_legacy_hook" -eq 1 ]; then
  printf '  - Restored sitecustomize.py from legacy backup\n'
elif [ "$removed_legacy_hook" -eq 1 ]; then
  printf '  - Removed legacy sitecustomize.py hook\n'
fi
if [ "$removed_pth" -eq 0 ] && [ "$removed_bootstrap" -eq 0 ] \
   && [ "$restored_legacy_hook" -eq 0 ] && [ "$removed_legacy_hook" -eq 0 ]; then
  printf '  - No hook changes needed\n'
fi
if [ "$removed_patch" -eq 1 ]; then
  printf '  - Removed patch file\n'
fi
