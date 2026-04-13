#!/usr/bin/env bash
set -euo pipefail

# One-line installer for hermes-claude-auth.
# Usage: curl -fsSL https://raw.githubusercontent.com/kristianvast/hermes-claude-auth/main/install-remote.sh | bash

REPO="https://github.com/kristianvast/hermes-claude-auth.git"
TMPDIR="$(mktemp -d)"

cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

git clone --depth 1 "$REPO" "$TMPDIR" 2>/dev/null
bash "$TMPDIR/install.sh"
