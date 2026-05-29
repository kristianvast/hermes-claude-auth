#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAKE_HOME="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() {
    rm -rf "$FAKE_HOME"
}
trap cleanup EXIT

pass() {
    printf '[PASS] %s\n' "$1"
    PASS=$((PASS + 1))
}

fail() {
    printf '[FAIL] %s - %s\n' "$1" "$2"
    FAIL=$((FAIL + 1))
}

assert_file_exists() {
    local label="$1" path="$2"
    if [ -f "$path" ]; then
        return 0
    else
        fail "$label" "expected file not found: $path"
        return 1
    fi
}

assert_file_contains() {
    local label="$1" path="$2" needle="$3"
    if grep -qF "$needle" "$path" 2>/dev/null; then
        return 0
    else
        fail "$label" "file $path does not contain: $needle"
        return 1
    fi
}

assert_file_not_exists() {
    local label="$1" path="$2"
    if [ ! -e "$path" ]; then
        return 0
    else
        fail "$label" "expected absent but found: $path"
        return 1
    fi
}

assert_dir_not_exists() {
    local label="$1" path="$2"
    if [ ! -d "$path" ]; then
        return 0
    else
        fail "$label" "expected absent dir but found: $path"
        return 1
    fi
}

export HOME="$FAKE_HOME"

mkdir -p "$FAKE_HOME/.hermes/hermes-agent"
python3 -m venv "$FAKE_HOME/.hermes/hermes-agent/venv"

VENV_PYTHON="$FAKE_HOME/.hermes/hermes-agent/venv/bin/python"
SITE_PACKAGES="$("$VENV_PYTHON" -c 'import site; print(site.getsitepackages()[0])')"
PTH_FILE="$SITE_PACKAGES/hermes_claude_auth.pth"
SITECUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"
SITE_BACKUP="$SITECUSTOMIZE.pre-hermes-claude-auth"
PATCH_FILE="$FAKE_HOME/.hermes/patches/anthropic_billing_bypass.py"
BOOTSTRAP_FILE="$FAKE_HOME/.hermes/patches/hermes_claude_auth_bootstrap.py"

# Test 1: Fresh install lays down the .pth loader, patch, and bootstrap.
T1="Test 1: Fresh install"
if "$REPO_DIR/install.sh" >/dev/null 2>&1; then
    ok=1
    assert_file_exists "$T1" "$PATCH_FILE" || ok=0
    assert_file_exists "$T1" "$BOOTSTRAP_FILE" || ok=0
    assert_file_exists "$T1" "$PTH_FILE" || ok=0
    assert_file_contains "$T1" "$PTH_FILE" "# hermes-claude-auth managed" || ok=0
    assert_file_contains "$T1" "$PTH_FILE" "hermes_claude_auth_bootstrap" || ok=0
    [ "$ok" -eq 1 ] && pass "$T1"
else
    fail "$T1" "install.sh exited non-zero"
fi

# Test 1b: The .pth actually loads at interpreter startup (the whole point).
T1b="Test 1b: .pth installs the import hook at startup"
hook_present="$("$VENV_PYTHON" -c "import sys; print(any('_ClaudeCodeBypassFinder' in type(f).__name__ for f in sys.meta_path))" 2>/dev/null || echo error)"
if [ "$hook_present" = "True" ]; then
    pass "$T1b"
else
    fail "$T1b" "meta_path finder not installed (got: $hook_present)"
fi

# Test 2: Idempotent re-install — .pth marker appears exactly once.
T2="Test 2: Idempotent re-install"
if "$REPO_DIR/install.sh" >/dev/null 2>&1; then
    ok=1
    assert_file_exists "$T2" "$PTH_FILE" || ok=0
    count="$(grep -cF '# hermes-claude-auth managed' "$PTH_FILE" 2>/dev/null || true)"
    if [ "$count" -ne 1 ]; then
        fail "$T2" "marker count is $count, expected 1"
        ok=0
    fi
    [ "$ok" -eq 1 ] && pass "$T2"
else
    fail "$T2" "install.sh exited non-zero on re-run"
fi

# Test 3: A foreign (non-managed) sitecustomize.py is left untouched.
T3="Test 3: Foreign sitecustomize.py untouched"
printf 'import sys\n# some unrelated hook\n' > "$SITECUSTOMIZE"
if "$REPO_DIR/install.sh" >/dev/null 2>&1; then
    ok=1
    assert_file_exists "$T3" "$SITECUSTOMIZE" || ok=0
    assert_file_contains "$T3" "$SITECUSTOMIZE" "# some unrelated hook" || ok=0
    assert_file_not_exists "$T3" "$SITE_BACKUP" || ok=0
    assert_file_exists "$T3" "$PTH_FILE" || ok=0
    [ "$ok" -eq 1 ] && pass "$T3"
else
    fail "$T3" "install.sh exited non-zero"
fi
rm -f "$SITECUSTOMIZE"

# Test 3b: A legacy MANAGED sitecustomize.py is migrated away (removed).
T3b="Test 3b: Legacy managed sitecustomize.py migrated"
printf '# hermes-claude-auth managed\nimport sys\n' > "$SITECUSTOMIZE"
if "$REPO_DIR/install.sh" >/dev/null 2>&1; then
    ok=1
    assert_file_not_exists "$T3b" "$SITECUSTOMIZE" || ok=0
    assert_file_exists "$T3b" "$PTH_FILE" || ok=0
    [ "$ok" -eq 1 ] && pass "$T3b"
else
    fail "$T3b" "install.sh exited non-zero"
fi

# Test 4: Uninstall removes the .pth loader but keeps the patch files.
T4="Test 4: Uninstall (loader only)"
if "$REPO_DIR/uninstall.sh" >/dev/null 2>&1; then
    ok=1
    assert_file_not_exists "$T4" "$PTH_FILE" || ok=0
    assert_file_exists "$T4" "$PATCH_FILE" || ok=0
    assert_file_exists "$T4" "$BOOTSTRAP_FILE" || ok=0
    [ "$ok" -eq 1 ] && pass "$T4"
else
    fail "$T4" "uninstall.sh exited non-zero"
fi

# Test 5: Reinstall then uninstall --purge removes everything.
T5="Test 5: Reinstall then uninstall --purge"
if "$REPO_DIR/install.sh" >/dev/null 2>&1 && "$REPO_DIR/uninstall.sh" --purge >/dev/null 2>&1; then
    ok=1
    assert_file_not_exists "$T5" "$PTH_FILE" || ok=0
    assert_file_not_exists "$T5" "$PATCH_FILE" || ok=0
    assert_file_not_exists "$T5" "$BOOTSTRAP_FILE" || ok=0
    assert_dir_not_exists "$T5" "$FAKE_HOME/.hermes/patches" || ok=0
    [ "$ok" -eq 1 ] && pass "$T5"
else
    fail "$T5" "install.sh or uninstall.sh --purge exited non-zero"
fi

# macOS Keychain mirror tests — fake `uname -s` → Darwin and a fake
# `security find-generic-password` via PATH shims so install.sh takes the
# Darwin branch without an actual Mac.
FAKE_BIN="$FAKE_HOME/fakebin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/uname" <<'UNAME_EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-s" ]; then
    echo Darwin
    exit 0
fi
exec /usr/bin/uname "$@"
UNAME_EOF
chmod +x "$FAKE_BIN/uname"

FAKE_CRED='{"oauth":{"accessToken":"sk-ant-fake","refreshToken":"rt-fake","expiresAt":0}}'
cat > "$FAKE_BIN/security" <<SECURITY_EOF
#!/usr/bin/env bash
if [ "\${1:-}" = "find-generic-password" ] && [ "\${2:-}" = "-s" ] \\
    && [ "\${3:-}" = "Claude Code-credentials" ] && [ "\${4:-}" = "-w" ]; then
    printf '%s' '$FAKE_CRED'
    exit 0
fi
exit 1
SECURITY_EOF
chmod +x "$FAKE_BIN/security"

# Test 6: Fresh macOS install mirrors Keychain → ~/.claude/.credentials.json
T6="Test 6: macOS install mirrors Keychain credentials to credentials.json"
rm -rf "$FAKE_HOME/.claude"
if PATH="$FAKE_BIN:$PATH" "$REPO_DIR/install.sh" >/dev/null 2>&1; then
    ok=1
    CRED_FILE="$FAKE_HOME/.claude/.credentials.json"
    assert_file_exists "$T6" "$CRED_FILE" || ok=0
    if [ -f "$CRED_FILE" ]; then
        actual="$(cat "$CRED_FILE")"
        if [ "$actual" != "$FAKE_CRED" ]; then
            fail "$T6" "credentials content mismatch: got '$actual'"
            ok=0
        fi
        mode="$(python3 -c "import os, sys; print(oct(os.stat(sys.argv[1]).st_mode)[-3:])" "$CRED_FILE")"
        if [ "$mode" != "600" ]; then
            fail "$T6" "credentials file mode is $mode, expected 600"
            ok=0
        fi
    fi
    [ "$ok" -eq 1 ] && pass "$T6"
else
    fail "$T6" "install.sh exited non-zero under faked macOS"
fi

# Test 7: Idempotent macOS re-install does not rewrite file with identical content
T7="Test 7: macOS re-install does not rewrite identical credentials"
CRED_FILE="$FAKE_HOME/.claude/.credentials.json"
if [ -f "$CRED_FILE" ]; then
    mtime_before="$(python3 -c "import os, sys; print(os.stat(sys.argv[1]).st_mtime_ns)" "$CRED_FILE")"
    sleep 1
    if PATH="$FAKE_BIN:$PATH" "$REPO_DIR/install.sh" >/dev/null 2>&1; then
        mtime_after="$(python3 -c "import os, sys; print(os.stat(sys.argv[1]).st_mtime_ns)" "$CRED_FILE")"
        if [ "$mtime_before" != "$mtime_after" ]; then
            fail "$T7" "credentials file rewritten despite identical content"
        else
            pass "$T7"
        fi
    else
        fail "$T7" "install.sh exited non-zero on idempotent macOS re-run"
    fi
else
    fail "$T7" "Test 6 did not produce a credentials file; cannot run idempotency check"
fi

# Test 8: macOS install with no Keychain entry leaves credentials file absent
T8="Test 8: macOS install with missing Keychain entry leaves no file"
rm -rf "$FAKE_HOME/.claude"
cat > "$FAKE_BIN/security" <<'SECURITY_FAIL_EOF'
#!/usr/bin/env bash
exit 1
SECURITY_FAIL_EOF
chmod +x "$FAKE_BIN/security"

if PATH="$FAKE_BIN:$PATH" "$REPO_DIR/install.sh" >/dev/null 2>&1; then
    assert_file_not_exists "$T8" "$FAKE_HOME/.claude/.credentials.json" && pass "$T8"
else
    fail "$T8" "install.sh exited non-zero when Keychain entry absent"
fi

TOTAL=$((PASS + FAIL))
printf '\n%d/%d tests passed\n' "$PASS" "$TOTAL"
[ "$FAIL" -eq 0 ]
