#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
RESET='\033[0m'

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

removed_hook=0
restored_hook=0
removed_loader=0
removed_bootstrap=0
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
    PTH_FILE="$SITE_PACKAGES/hermes_claude_auth.pth"
    if [ -e "$PTH_FILE" ] && grep -qF '# hermes-claude-auth managed' "$PTH_FILE"; then
      rm -f "$PTH_FILE"
      printf '%b[✓]%b Removed bootstrap loader %s\n' "$GREEN" "$RESET" "$PTH_FILE"
      removed_loader=1
    fi

    SITE_CUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"
    BACKUP_FILE="$SITE_PACKAGES/sitecustomize.py.pre-hermes-claude-auth"

    if [ ! -e "$SITE_CUSTOMIZE" ]; then
      printf '%b[—]%b sitecustomize.py not found (already removed)\n' "$YELLOW" "$RESET"
    elif grep -qF '# hermes-claude-auth managed' "$SITE_CUSTOMIZE"; then
      if [ -e "$BACKUP_FILE" ]; then
        mv "$BACKUP_FILE" "$SITE_CUSTOMIZE"
        printf '%b[✓]%b Restored original sitecustomize.py from backup\n' "$GREEN" "$RESET"
        restored_hook=1
      else
        rm -f "$SITE_CUSTOMIZE"
        printf '%b[✓]%b Removed hook from %s/sitecustomize.py\n' "$GREEN" "$RESET" "$SITE_PACKAGES"
        removed_hook=1
      fi
    else
      printf '%b[—]%b sitecustomize.py not ours\n' "$YELLOW" "$RESET"
    fi
  fi
fi

BOOTSTRAP_FILE="$HOME/.hermes/patches/hermes_claude_auth_bootstrap.py"
PATCH_PYCACHE_DIR="$HOME/.hermes/patches/__pycache__"
if [ -e "$BOOTSTRAP_FILE" ]; then
  rm -f "$BOOTSTRAP_FILE"
  printf '%b[✓]%b Removed bootstrap hook from ~/.hermes/patches/\n' "$GREEN" "$RESET"
  removed_bootstrap=1
fi
rm -f "$PATCH_PYCACHE_DIR"/hermes_claude_auth_bootstrap.*.pyc 2>/dev/null || true

if [ "$PURGE" -eq 1 ]; then
  PATCH_DIR="$HOME/.hermes/patches"
  PATCH_FILE="$PATCH_DIR/anthropic_billing_bypass.py"

  if [ -e "$PATCH_FILE" ]; then
    rm -f "$PATCH_FILE"
    printf '%b[✓]%b Removed patch from ~/.hermes/patches/\n' "$GREEN" "$RESET"
    removed_patch=1
  fi
  rm -f "$PATCH_PYCACHE_DIR"/anthropic_billing_bypass.*.pyc 2>/dev/null || true
  if [ -d "$PATCH_PYCACHE_DIR" ] && [ -z "$(ls -A "$PATCH_PYCACHE_DIR" 2>/dev/null)" ]; then
    rmdir "$PATCH_PYCACHE_DIR" 2>/dev/null || true
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
if [ "$removed_loader" -eq 1 ] || [ "$removed_bootstrap" -eq 1 ] || [ "$restored_hook" -eq 1 ] || [ "$removed_hook" -eq 1 ]; then
  if [ "$removed_loader" -eq 1 ]; then
    printf '  - Removed .pth bootstrap loader\n'
  fi
  if [ "$removed_bootstrap" -eq 1 ]; then
    printf '  - Removed bootstrap hook module\n'
  fi
  if [ "$restored_hook" -eq 1 ]; then
    printf '  - Restored sitecustomize.py from backup\n'
  elif [ "$removed_hook" -eq 1 ]; then
    printf '  - Removed sitecustomize.py hook\n'
  fi
else
  printf '  - No hook changes needed\n'
fi
if [ "$removed_patch" -eq 1 ]; then
  printf '  - Removed patch file\n'
fi
