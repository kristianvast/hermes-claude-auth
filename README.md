# hermes-claude-auth
Claude Code OAuth bypass for hermes-agent, use your Claude Code subscription (Max/Pro) with Hermes.

## What this does
Patches hermes-agent at runtime to pass Anthropic's server-side OAuth content validation. It does not modify hermes-agent source files. Installation happens through a Python import hook that monkey-patches `build_anthropic_kwargs` on startup.

## Why this exists
On 2026-04-04, Anthropic added server-side validation that rejects OAuth requests from third-party tools. This patch adds the billing header signature and system prompt structure the API expects.

## Prerequisites
- hermes-agent installed (`$HERMES_HOME/hermes-agent/`, defaults to `~/.hermes/hermes-agent/` on Linux/macOS and `%LOCALAPPDATA%\hermes\hermes-agent\` on Windows)
- Claude Code CLI authenticated (valid credentials at `~/.claude/.credentials.json`)
- hermes-agent configured for OAuth (`credential_pool` has a `claude_code` entry in `$HERMES_HOME/auth.json`)
- Python 3.11+
- **Windows**: PowerShell 5.1+ and Git in PATH

## Install

### Linux / macOS
```bash
# Remote install (one-liner)
curl -fsSL https://raw.githubusercontent.com/kristianvast/hermes-claude-auth/main/install-remote.sh | bash

# Or clone manually
git clone https://github.com/kristianvast/hermes-claude-auth.git
cd hermes-claude-auth
./install.sh
```

### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/kristianvast/hermes-claude-auth/main/install-remote.ps1 | iex
```

Or clone manually:
```powershell
git clone https://github.com/kristianvast/hermes-claude-auth.git
cd hermes-claude-auth
.\install.ps1
```

What the installer does:
- Auto-detects your hermes directory (`$HERMES_HOME` if set, else `%LOCALAPPDATA%\hermes\` on Windows and `~/.hermes/` on Linux/macOS)
- Copies `anthropic_billing_bypass.py` to `<hermes-dir>/patches/`
- Installs a `.pth` shim + bootstrap module into the hermes venv's site-packages (this loads the hook at interpreter startup; see "How it works" below for why a `.pth` and not `sitecustomize.py`)
- Mirrors Claude Code credentials from the OS credential store to `~/.claude/.credentials.json` (macOS Keychain / Windows Credential Manager)
- Restarts `hermes-gateway.service` if running (Linux only)

## Agent Handoff Prompt

Use this prompt when another Hermes instance needs to install, repair, or audit
Claude auth without losing the Antigravity provider:

```text
You are setting up hermes-claude-auth on this machine.

Work from the persistent clone at ~/hermes-claude-auth. If it exists and is
clean, run `git pull`; if it has local changes, do not overwrite them and
report that pull was skipped. If it does not exist, run:
`git clone https://github.com/kristianvast/hermes-claude-auth.git ~/hermes-claude-auth`

Then run:
`cd ~/hermes-claude-auth && ./install.sh`

After install, verify and report:
- `./install.sh --check` passes.
- `~/.hermes/patches/anthropic_billing_bypass.py` matches this repo.
- The Hermes venv `sitecustomize.py` contains `# hermes-claude-auth managed`.
- If `# hermes-antigravity managed` is also present, confirm the shared hook
  still contains Antigravity hooks for `hermes_cli.auth`,
  `hermes_cli.providers`, `hermes_cli.commands`, `cli`,
  `agent.auxiliary_client`, `hermes_cli.runtime_provider`,
  `hermes_cli.main`, `hermes_cli.model_switch`, and `api.config`.
- Importing `agent.error_classifier` in the Hermes venv does not print a
  `ModuleNotFoundError` traceback for `anthropic_billing_bypass`.
- `~/.hermes/hermes-agent/.git/hooks/post-merge` exists and is executable.

If Claude credentials are available, smoke-test:
`hermes chat --provider anthropic -m claude-sonnet-4-6 -q "OK" -Q`

If Antigravity is also installed and credentials are available, smoke-test:
`hermes chat --provider google-antigravity -m gemini-3.5-flash-high -q "OK" -Q`

If any smoke test cannot be run, say exactly what blocked it. Finish with the
current git commit, whether automatic repair is installed, and the check/smoke
results.
```

## Uninstall

### Linux / macOS
```bash
./uninstall.sh          # remove hook only
./uninstall.sh --purge  # remove hook + patch file
```

### Windows (PowerShell)
```powershell
.\uninstall.ps1          # remove hook only
.\uninstall.ps1 -Purge   # remove hook + patch file
```

## Surviving `hermes update` (auto-recovery)

`hermes update` can wipe the loader from the venv's `site-packages/` in two
ways:

1. **`git merge` / `git checkout`** of the hermes-agent repo — triggers git
   hooks, but the default `.git/hooks/` dir is *inside* the repo and gets
   **stashed** by the update's `git stash` step, so hooks there don't fire at
   the right time.
2. **Venv rebuild** — the update provisions a fresh Python runtime, which
   deletes `sitecustomize.py` outright. No git hook covers this.

### Two-layer defense

**Layer 1 — git hooks via `core.hooksPath` (outside the repo, Windows):**

On Linux/macOS `install.sh` installs `post-merge-hook.sh` into
`.git/hooks/post-merge` instead; it runs `install.sh --check` after each merge
and `install.sh --post-update` only when the hook is missing or drifted.

```bash
# From inside your hermes-agent checkout:
mkdir -p "$LOCALAPPDATA/hermes/git-hooks"
cp post-merge.hook.sh "$LOCALAPPDATA/hermes/git-hooks/post-merge"
cp post-checkout.hook.sh "$LOCALAPPDATA/hermes/git-hooks/post-checkout"
chmod +x "$LOCALAPPDATA/hermes/git-hooks/post-"*
git config core.hooksPath "$LOCALAPPDATA/hermes/git-hooks"
```

Because the hooks live *outside* `.git/hooks/`, the update's `git stash`
can't touch them, and `core.hooksPath` makes git use them for every
merge/checkout.

**Layer 2 — watchdog cron (covers venv rebuild):**

```bash
# Hermes cron runs restore_loader.sh every 15m; it re-copies the loader
# from $LOCALAPPDATA/hermes/patches/sitecustomize.py if it's missing.
hermes cron create "every 15m" --name restore-claude-auth-loader \
  --no-agent --script restore_loader.sh --deliver local
```

The cron job only fires while the Hermes gateway is running — after an update,
the loader is restored within ≤15 minutes of the gateway coming back up.

Both layers are idempotent and never break the update. The canonical loader
lives at `$LOCALAPPDATA/hermes/patches/sitecustomize.py` (outside any repo).

> **Windows note:** hooks are POSIX shell scripts run under Git Bash (ships
> with Git for Windows). The cron script uses `uname` to resolve paths and
> works under the Hermes gateway's environment.

## How it works
1. **Billing header**: SHA-256 signed `x-anthropic-billing-header` injected as `system[0]`
2. **System prompt relocation**: Non-identity system entries moved to the first user message as `<system-reminder>` blocks
3. **Beta flags**: Adds `prompt-caching-scope-2026-01-05` and `advisor-tool-2026-03-01`
4. **Stainless SDK spoof**: Lowercase `x-stainless-*` headers + `anthropic-dangerous-direct-browser-access` + `?beta=true` query param matching real Claude Code 2.1.112
5. **Tool name namespacing**: Hermes's `mcp_bash` is rewritten to `mcp__hermes__Bash` outbound; the response normalizer unwraps it back to `bash` so hermes's tool dispatcher resolves the registered name without auto-repair noise
6. **Tool pair repair**: Orphaned `tool_use` / `tool_result` blocks (left by long conversations or partial summaries) are stripped before signing — prevents HTTP 400 (upstream PR #136)
7. **Haiku effort stripping**: `effort` parameter is removed for haiku models that reject it with HTTP 400 (upstream PR #126)
8. **Temperature fix**: Strips non-default `temperature` on Opus 4.6 adaptive thinking, which otherwise rejects with HTTP 400
9. **Account metadata**: Maps `~/.claude.json::oauthAccount.accountUuid` to `metadata.user_id` (Anthropic rejected the older `account_uuid` key with HTTP 400 on 2026-04-29)
10. **Window-aware rate-limit auto-wait** (v1.5.9+): when Anthropic returns HTTP 429 mid-agent, reads the `anthropic-ratelimit-unified-{5h,1d,7d}-status` / `-reset` headers to identify which subscription window tripped, picks the **longest** waiting window (sleeping through a 7d reset also clears 5h), and applies a **per-window** safety cap (`HERMES_RL_AUTOWAIT_MAX_5H_S`=6h, `_1D_S`=26h, `_7D_S`=7.5d). The legacy 6h cap is kept as the fallback when no window-specific status is present. Per-window caps can be tuned via env vars.

Installed through a `.pth` file in the venv's site-packages that imports a small bootstrap module at interpreter startup, which in turn registers a `MetaPathFinder` hook for `agent.anthropic_adapter`. No source modifications.

The `.pth` shim runs *before* `site.py` imports `sitecustomize`, on every platform. An earlier version of this installer wrote a `sitecustomize.py` into site-packages directly, which failed silently on Debian/Ubuntu — those distros ship `/usr/lib/pythonX.Y/sitecustomize.py` for apport and it wins import priority over the venv-local one, so the bypass hook never ran. The current installer auto-migrates legacy installs.

## Subscription rate-limit auto-wait (Claude Pro/Max windows: 5h, 1d, 7d)

When a long agent run exhausts a Claude Pro/Max usage window mid-flight,
api.anthropic.com returns HTTP 429 with a reset time in the
`anthropic-ratelimit-unified-*-reset` headers.  Hermes core's retry loop
caps backoff at 120s and abandons the run, surfacing "rate-limiting
requests" on Telegram and killing the session.

This patch wraps the two API-call entry points on `run_agent.AIAgent` so a
genuine subscription-window 429 instead **sleeps until the window resets**
(interruptibly, in short chunks) and then retries the same call transparently.
It picks the **longest** throttled window (e.g. a 7d hit also clears the 5h
window, so a single sleep covers both) and applies a **per-window** safety
cap (5h=6h, 1d=26h, 7d=7.5d). Behaviour is tunable via env vars:

| Env var | Default | Meaning |
|---------|---------|---------|
| `HERMES_RL_AUTOWAIT` | `1` | `0` disables auto-wait entirely |
| `HERMES_RL_AUTOWAIT_MAX_S` | `21600` | fallback cap (s) when no window detected |
| `HERMES_RL_AUTOWAIT_MAX_5H_S` | `21600` | 5h-window safety cap (6h) |
| `HERMES_RL_AUTOWAIT_MAX_1D_S` | `93600` | 1d-window safety cap (26h) |
| `HERMES_RL_AUTOWAIT_MAX_7D_S` | `648000` | 7d-window safety cap (7.5d) |
| `HERMES_RL_AUTOWAIT_BUFFER_S` | `5` | pad added after reset |
| `HERMES_RL_AUTOWAIT_DEFAULT_S` | `300` | wait when no reset header found |

The wait loop also calls `agent._touch_activity(...)` every ~25s so the
gateway's inactivity watchdog doesn't kill the agent mid-wait.  Idempotent
and never breaks the billing path: if anything goes wrong, the original
call/exception behaviour is preserved.

Ported from kristianvast/hermes-claude-auth PR #27 (window-aware auto-wait);
the fingerprint parity fix below from PR #21.

## Fingerprint parity (avoids "extra usage" billing)

Anthropic's validator cross-references the `user-agent` header and the
`cc_version=` field in the billing header.  If the SDK client sends
`claude-cli/<ver> (external, cli)` while the billing header claims
`cc_entrypoint=sdk-cli`, the request is flagged as third-party and routed to
pay-per-token **extra usage** instead of your Max/Pro plan
(`HTTP 400 You're out of extra usage`).  This patch pins the Claude Code
version to `2.1.112` for **both** the user-agent and the signed billing
header, and forces `x-app: cli`, eliminating the drift that triggered
upstream issue #6.

## What gets modified
| File | Action |
|------|--------|
| `$HERMES_HOME/patches/anthropic_billing_bypass.py` | Created |
| `<venv>/lib/pythonX.Y/site-packages/hermes_claude_auth.pth` (Linux/macOS)<br>`<venv>\Lib\site-packages\hermes_claude_auth.pth` (Windows) | Created |
| `<venv>/lib/pythonX.Y/site-packages/_hermes_claude_auth_bootstrap.py` (Linux/macOS)<br>`<venv>\Lib\site-packages\_hermes_claude_auth_bootstrap.py` (Windows) | Created |
| `<venv>/lib/pythonX.Y/site-packages/sitecustomize.py` | Removed if left behind by a legacy install (original restored from `.pre-hermes-claude-auth` backup when present) |
| hermes-agent source files | NOT modified |

## After Hermes Update

When you run `hermes update` (which does `git pull` + `pip install`), the
`sitecustomize.py` inside the venv may be overwritten. The patch file survives.

**Automatic recovery (default).** `install.sh` installs a git `post-merge` hook
into `~/.hermes/hermes-agent/.git/hooks/`, so the moment `hermes update` runs its
`git pull`, the hook detects the missing hook and re-runs recovery automatically.
If the Google Antigravity plugin is also installed, its coexistence
`sitecustomize.py` (which already contains the Claude hook) is restored first and
this installer leaves it untouched — the two patches never clobber each other.

> **Keep the clone in a persistent path** (e.g. `~/hermes-claude-auth`), **not
> `/tmp`**. The post-merge hook looks for the installer at
> `$HOME/hermes-claude-auth/install.sh` first; a `/tmp` clone is wiped on reboot
> and auto-recovery silently can't run.

**Check what's broken** (verifies files exist AND match the repo byte-for-byte):
```bash
cd ~/hermes-claude-auth
./install.sh --check
```
`--check` flags **content drift** too — if the installed
`anthropic_billing_bypass.py` differs from the repo (e.g. a hot-fix was applied
to one but not synced to the other), it reports `[!] DRIFT` so you can sync the
newer copy back before a clean install silently reverts it. The shared
`sitecustomize.py` is intentionally not compared (it legitimately differs when
the Antigravity plugin's multi-hook version is installed).

**Recover (only restores sitecustomize.py + patch):**
```bash
cd ~/hermes-claude-auth
git pull && ./install.sh --post-update
```

**Full recovery:**
```bash
cd ~/hermes-claude-auth
git pull && ./install.sh
```

### What survives `hermes update`

| File | Location | Survives? |
|------|----------|:---:|
| `anthropic_billing_bypass.py` | `~/.hermes/patches/` | ✅ Outside repo |
| **`sitecustomize.py`** | venv `site-packages/` | ❌ Overwritten |
| Claude credentials | `~/.claude/` | ✅ Managed by Claude CLI |
| Auth token | `~/.hermes/auth.json` | ✅ Outside repo |

Only `sitecustomize.py` needs recovery. `--post-update` does exactly that.

## Compatibility
- Tested with hermes-agent on Python 3.11+
- Linux, macOS, and **Windows** (native: `%LOCALAPPDATA%\hermes`; the bypass
  patch lives at `%LOCALAPPDATA%\hermes\patches\anthropic_billing_bypass.py`)
- **Multiple profiles**: Hermes supports `profiles/<name>/` under the data
  root. The patch is installed once at the data root and shared by every
  profile; `HERMES_HOME` may point at a profile dir and the loader still
  resolves the patch correctly.
- Depends on `build_anthropic_kwargs(is_oauth=...)` in `agent.anthropic_adapter`, so it may need updating if hermes-agent changes that interface

## Verifying the bypass is active (not falling back to extra usage)

After install, confirm Hermes is using the subscription path and not silently
billing pay-per-token:

1. **Startup log** — look for these lines in the Hermes gateway log
   (`%LOCALAPPDATA%\hermes\logs\` on Windows):
   ```
   [anthropic_billing_bypass] Bypass installed
   [anthropic_billing_bypass] Transport unwrap hook installed
   [anthropic_billing_bypass] Rate-limit auto-wait installed
   ```
2. **No `extra usage` errors** — if you see `HTTP 400 You're out of extra
   usage` or `HTTP 429 ... extra usage required`, the fingerprint drifted and
   the request was routed to the pay-per-token bucket. Re-run `install.sh`.
3. **Token flow** — calls should succeed with `provider=anthropic` in
   `agent.log` and normal in/out token counts (not 429s).

> **Dynamic version detection:** This build detects the installed Claude Code
> version automatically (via `claude --version`) and uses it for both the
> user-agent and the signed billing header. No manual pinning — when you
> update Claude Code, the bypass follows on the next Hermes start. Falls back
> to `_PINNED_CC_VERSION` (`2.1.112`) only if Claude Code isn't on PATH.
> This replaces the static pin from upstream PR #21 and removes the drift that
> triggered issue #6.

## Integrated community PRs

This build merges the open community PRs into one coherent tree. Where two PRs
solved the same problem differently, the newer/better-reasoned design won and
the other was folded in or dropped:

- **PR #16** — loader moved from `sitecustomize.py` to a `.pth` shim +
  bootstrap module, fixing installs that were silently dead on Debian/Ubuntu
  (the system apport `sitecustomize.py` wins import priority).
- **PR #28** — patch resolution across Hermes profiles, `%LOCALAPPDATA%`, and
  `$HERMES_HOME`, plus dynamic Claude Code version detection and the two-layer
  auto-recovery (`core.hooksPath` hooks + `restore_loader.sh` cron).
- **PR #26** — `$HERMES_HOME` honoured throughout the shell installers.
- **PR #24** — Windows PowerShell installers + Credential Manager mirroring,
  retargeted onto the `.pth` mechanism. Supersedes PR #17.
- **PR #21** — fingerprint parity (forced `user-agent`, `x-app`). Its
  multi-interpreter install was ported onto the `.pth` mechanism so the hook
  also lands in an editable install used by the CLI.
- **PR #23** — thinking-block replay integrity, tool-pair repair hardening, and
  the `agent.error_classifier` hook. Its installer rewrite (`--check`,
  `.git/hooks/post-merge`) was dropped in favour of PR #28's recovery model,
  which deliberately avoids `.git/hooks` because `hermes update` stashes it.
- **PR #20** — per-pool-entry `account_uuid` billing routing for multi-account
  credential pools.
- **PR #1** — clone URL fix.

Not merged:

- **PR #17** — strict subset of PR #24.
- **PR #7** — MD5 tool-name obfuscation. Upstream never adopted it and still
  ships PascalCase `mcp_Bash`; this tree keeps the namespaced PascalCase scheme.
- **PR #10** — stale fingerprint base (branched before v1.5.0) and conflicting.

## Troubleshooting

### Install issues
- **"hermes-agent not found"**: Make sure Hermes is installed at `$HERMES_HOME/hermes-agent/` (defaults to `~/.hermes/hermes-agent/`) or `%LOCALAPPDATA%\hermes\hermes-agent\` (Windows)
- **"No virtualenv found"**: Set `HERMES_VENV` to point to your venv
- **"ModuleNotFoundError: No module named 'anthropic_billing_bypass'"** on every startup: the hook couldn't find the patch. This happens when `HERMES_HOME` points at a **profile** directory (`.../hermes/profiles/<name>`) but the patch lives at the data root. The bootstrap resolves that case automatically — re-run `./install.sh` to pick it up.
- **Patch not loading**: Check `journalctl --user -u hermes-gateway -n 50` (Linux) or the Hermes log under `%LOCALAPPDATA%\hermes\logs\` (Windows) for `[anthropic_billing_bypass]` or `[hermes-claude-auth]` messages
- **Bypass silently inactive on Debian/Ubuntu** (legacy `sitecustomize.py` installs only): If you installed before the `.pth` migration, the bypass may never have actually run on your host. Debian/Ubuntu ship `/usr/lib/pythonX.Y/sitecustomize.py` for apport, and that one wins import priority over the venv-local `sitecustomize.py`, so the hook never installs. Quick diagnostic:

  ```bash
  cd ~/.hermes/hermes-agent && ./venv/bin/python -c "import anthropic_billing_bypass"
  ```

  `ModuleNotFoundError` means the hook isn't running. Fix is to re-run `./install.sh` — the current installer uses a `.pth` shim that sidesteps the apport collision and auto-migrates legacy installs.
- **PowerShell execution policy**: The remote installer (`irm | iex`) handles this automatically. For manual runs, use `powershell -ExecutionPolicy Bypass -File .\install.ps1`

### Auth issues

- **`Anthropic 401 authentication failed`** or **`No Anthropic credentials found`**: Hermes reads Claude subscription credentials from `~/.claude/.credentials.json`. If Claude Code is authenticated (e.g. in macOS Keychain) but that file is missing or stale, Hermes fails even when Claude Code itself works.

  On macOS, `install.sh` v1.1.1+ auto-mirrors the `Claude Code-credentials` Keychain entry into `~/.claude/.credentials.json` on every run, so re-running the installer is usually enough. Full fix:

  1. Refresh Claude subscription login:
     ```bash
     claude auth login --claudeai
     ```
  2. Re-run the installer to re-mirror credentials (macOS) and reload the patch:
     ```bash
     ./install.sh
     ```
  3. Remove stale `ANTHROPIC_TOKEN` / `ANTHROPIC_API_KEY` values from `$HERMES_HOME/.env` — they can override subscription auth.
  4. Reset cached credentials:
     ```bash
     hermes auth reset anthropic
     ```
  5. Retry with a smoke test:
     ```bash
     hermes chat -q 'Reply with exactly: AUTH TEST OK' --provider anthropic -m claude-sonnet-4-6 -Q
     ```

  If the auto-mirror doesn't work (e.g. your Keychain entry is under a different service name), mirror it manually:
  ```bash
  python3 - <<'PY'
  import subprocess
  from pathlib import Path

  secret = subprocess.check_output(
      ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
      text=True,
  ).strip()

  cred_path = Path.home() / '.claude' / '.credentials.json'
  cred_path.parent.mkdir(parents=True, exist_ok=True)
  cred_path.write_text(secret)
  cred_path.chmod(0o600)
  print(f'wrote {cred_path}')
  PY
  ```

  Credit: the macOS Keychain mirror approach was written up by [@DrQbz](https://github.com/DrQbz) in [issue #5](https://github.com/kristianvast/hermes-claude-auth/issues/5) and is now automated in `install.sh`.

  **On Windows**, `install.ps1` auto-mirrors the `Claude Code-credentials` entry from Windows Credential Manager into `~/.claude/.credentials.json` using the Win32 Credential API (analogous to the macOS Keychain mirror). Re-running the installer is usually sufficient:
  ```powershell
  .\install.ps1
  ```
  If auto-mirroring fails (e.g. PowerShell constrained language mode), re-authenticate and retry:
  ```powershell
  claude auth login --claudeai
  .\install.ps1
  ```
  If credentials still aren't found, check Windows Credential Manager manually:
  ```powershell
  # List all Claude-related credentials
  cmdkey /list:*Claude*
  # If no entry exists, re-authenticate:
  claude auth login --claudeai
  ```

### Billing / routing issues

- **HTTP 400: "Third-party apps now draw from your extra usage, not your plan limits"**: Anthropic's server-side validation has classified your requests as third-party and routed them to pay-per-token credits instead of your Max/Pro plan. Make sure you're on the latest version of this patch (it tracks the upstream [opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth) fingerprint changes). Reinstall with `./install.sh` and restart `hermes-gateway`. If the error persists after update, the bypass is currently broken upstream too — track [issue #6](https://github.com/kristianvast/hermes-claude-auth/issues/6) for status.
- **HTTP 400 persists after update**: The billing salt or signature format may have been rotated by Anthropic again. Check for newer commits to this repo.

### Rate-limit / 429 behaviour

- **Auto-wait won't kick in / 429 reaches the user immediately**: the patch
  is disabled. Re-enable with `HERMES_RL_AUTOWAIT=1` (the default). It
  requires the `sitecustomize.py` hook to be installed (run `./install.sh`
  and check with `./install.sh --check`).
- **Auto-wait bails with "this is bigger than safety-cap" on a weekly limit**:
  you're on an older build (< v1.5.9). v1.5.9 added window-aware
  detection — it now reads `anthropic-ratelimit-unified-7d-status` /
  `-reset` and waits up to `HERMES_RL_AUTOWAIT_MAX_7D_S` (default 7.5d).
  Upgrade: `cd ~/hermes-claude-auth && git pull && ./install.sh`.
- **Want a shorter / longer wait for a specific window**: tune the
  per-window cap via env vars (values in seconds, units in
  `~/.hermes/.env` or your systemd unit):
  - `HERMES_RL_AUTOWAIT_MAX_5H_S` (default `21600` = 6h)
  - `HERMES_RL_AUTOWAIT_MAX_1D_S` (default `93600` = 26h)
  - `HERMES_RL_AUTOWAIT_MAX_7D_S` (default `648000` = 7.5d)
  - `HERMES_RL_AUTOWAIT_MAX_S` — fallback when the 429 has no
    window-specific status header (default `21600` = 6h).
  Setting any of the `MAX_*_S` vars to a value `<= 0` falls back to the
  default; to *disable* auto-wait entirely, use `HERMES_RL_AUTOWAIT=0`.

## Credits
- [griffinmartin/opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth), the original TypeScript implementation for opencode (MIT)
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), the AI agent this patches (MIT)

## Disclaimer
This uses Claude Code subscription credentials outside the official Claude Code CLI. It works with Anthropic's current OAuth implementation but may break if Anthropic changes their validation. Use at your own risk.

## License
MIT, see [LICENSE](LICENSE).
