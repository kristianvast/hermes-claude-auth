#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

MARKER='# hermes-claude-auth managed'
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
removed_legacy=0
restored_legacy=0
removed_patch=0

if [ -z "$VENV_DIR" ]; then
  printf '%b[—]%b No hermes venv found, skipping loader removal\n' "$YELLOW" "$RESET"
else
  PYTHON_BIN="$VENV_DIR/bin/python"
  [ -x "$PYTHON_BIN" ] || PYTHON_BIN="$VENV_DIR/bin/python3"
  SITE_PACKAGES=""

  if [ -x "$PYTHON_BIN" ]; then
    SITE_PACKAGES="$($PYTHON_BIN -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || true)"
  fi

  if [ -z "$SITE_PACKAGES" ]; then
    printf '%b[—]%b Could not detect site-packages, skipping loader removal\n' "$YELLOW" "$RESET"
  else
    # Remove the .pth loader.
    PTH_FILE="$SITE_PACKAGES/hermes_claude_auth.pth"
    if [ -e "$PTH_FILE" ]; then
      rm -f "$PTH_FILE"
      printf '%b[✓]%b Removed .pth loader from %s\n' "$GREEN" "$RESET" "$SITE_PACKAGES"
      removed_pth=1
    else
      printf '%b[—]%b .pth loader not found (already removed)\n' "$YELLOW" "$RESET"
    fi

    # Clean up a legacy managed sitecustomize.py if present (pre-.pth installs).
    SITE_CUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"
    BACKUP_FILE="$SITE_CUSTOMIZE.pre-hermes-claude-auth"
    if [ -f "$SITE_CUSTOMIZE" ] && grep -qF "$MARKER" "$SITE_CUSTOMIZE"; then
      if [ -e "$BACKUP_FILE" ]; then
        mv "$BACKUP_FILE" "$SITE_CUSTOMIZE"
        printf '%b[✓]%b Restored original sitecustomize.py from backup (legacy)\n' "$GREEN" "$RESET"
        restored_legacy=1
      else
        rm -f "$SITE_CUSTOMIZE"
        printf '%b[✓]%b Removed legacy managed sitecustomize.py\n' "$GREEN" "$RESET"
        removed_legacy=1
      fi
    fi
  fi
fi

if [ "$PURGE" -eq 1 ]; then
  PATCH_DIR="$HOME/.hermes/patches"

  for f in anthropic_billing_bypass.py hermes_claude_auth_bootstrap.py; do
    if [ -e "$PATCH_DIR/$f" ]; then
      rm -f "$PATCH_DIR/$f"
      removed_patch=1
    fi
  done
  # Drop the bytecode cache our modules left behind (the dir is ours alone).
  if [ -d "$PATCH_DIR/__pycache__" ]; then
    rm -f "$PATCH_DIR"/__pycache__/anthropic_billing_bypass.*.pyc \
          "$PATCH_DIR"/__pycache__/hermes_claude_auth_bootstrap.*.pyc 2>/dev/null || true
    rmdir "$PATCH_DIR/__pycache__" 2>/dev/null || true
  fi
  [ "$removed_patch" -eq 1 ] && printf '%b[✓]%b Removed patch + bootstrap from ~/.hermes/patches/\n' "$GREEN" "$RESET"

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
if [ "$removed_pth" -eq 1 ]; then
  printf '  - Removed .pth loader\n'
else
  printf '  - No .pth loader changes needed\n'
fi
if [ "$restored_legacy" -eq 1 ]; then
  printf '  - Restored legacy sitecustomize.py from backup\n'
elif [ "$removed_legacy" -eq 1 ]; then
  printf '  - Removed legacy sitecustomize.py hook\n'
fi
if [ "$removed_patch" -eq 1 ]; then
  printf '  - Removed patch + bootstrap files\n'
fi
