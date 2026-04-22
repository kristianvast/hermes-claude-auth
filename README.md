# hermes-claude-auth
Claude Code OAuth bypass for hermes-agent, use your Claude Code subscription (Max/Pro) with Hermes.

## What this does
Patches hermes-agent at runtime to pass Anthropic's server-side OAuth content validation. It does not modify hermes-agent source files. Installation happens through a Python import hook that monkey-patches `build_anthropic_kwargs` on startup.

## Why this exists
On 2026-04-04, Anthropic added server-side validation that rejects OAuth requests from third-party tools. This patch adds the billing header signature and system prompt structure the API expects.

## Prerequisites
- hermes-agent installed (`~/.hermes/hermes-agent/`)
- Claude Code CLI authenticated (valid credentials at `~/.claude/.credentials.json`)
- hermes-agent configured for OAuth (`credential_pool` has a `claude_code` entry in `~/.hermes/auth.json`)
- Python 3.11+

## Install
```bash
curl -fsSL https://raw.githubusercontent.com/kristianvast/hermes-claude-auth/main/install-remote.sh | bash
```

Or clone manually:
```bash
git clone https://github.com/kristianvast/hermes-claude-auth.git
cd hermes-claude-auth
./install.sh
```

What `install.sh` does:
- Copies `anthropic_billing_bypass.py` to `~/.hermes/patches/`
- Installs the import hook as `sitecustomize.py` in the hermes venv's site-packages
- Restarts `hermes-gateway.service` if running

## Uninstall
```bash
./uninstall.sh          # remove hook only
./uninstall.sh --purge  # remove hook + patch file
```

## How it works
1. **Billing header**: SHA-256 signed `x-anthropic-billing-header` injected as `system[0]`
2. **System prompt relocation**: Non-identity system entries moved to the first user message as `<system-reminder>` blocks
3. **Beta flag**: Adds `prompt-caching-scope-2026-01-05`
4. **Temperature fix**: Strips non-default temperature on Opus 4.6 adaptive thinking, which prevents HTTP 400

Installed through a `sitecustomize.py` MetaPathFinder hook, so it runs at interpreter startup with no source modifications.

## What gets modified
| File | Action |
|------|--------|
| `~/.hermes/patches/anthropic_billing_bypass.py` | Created |
| `<venv>/lib/pythonX.Y/site-packages/sitecustomize.py` | Created or replaced |
| hermes-agent source files | NOT modified |

## Compatibility
- Tested with hermes-agent on Python 3.11+
- Linux and macOS
- Depends on `build_anthropic_kwargs(is_oauth=...)` in `agent.anthropic_adapter`, so it may need updating if hermes-agent changes that interface

## Troubleshooting

### Install issues
- **"hermes-agent not found"**: Make sure Hermes is installed at `~/.hermes/hermes-agent/`
- **"No virtualenv found"**: Set `HERMES_VENV` to point to your venv
- **Patch not loading**: Check `journalctl --user -u hermes-gateway -n 50` for `[anthropic_billing_bypass]` or `[hermes-claude-auth]` messages

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
  3. Remove stale `ANTHROPIC_TOKEN` / `ANTHROPIC_API_KEY` values from `~/.hermes/.env` — they can override subscription auth.
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

### Billing / routing issues

- **HTTP 400: "Third-party apps now draw from your extra usage, not your plan limits"**: Anthropic's server-side validation has classified your requests as third-party and routed them to pay-per-token credits instead of your Max/Pro plan. Make sure you're on the latest version of this patch (it tracks the upstream [opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth) fingerprint changes). Reinstall with `./install.sh` and restart `hermes-gateway`. If the error persists after update, the bypass is currently broken upstream too — track [issue #6](https://github.com/kristianvast/hermes-claude-auth/issues/6) for status.
- **HTTP 400 persists after update**: The billing salt or signature format may have been rotated by Anthropic again. Check for newer commits to this repo.

## Credits
- [griffinmartin/opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth), the original TypeScript implementation for opencode (MIT)
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), the AI agent this patches (MIT)

## Disclaimer
This uses Claude Code subscription credentials outside the official Claude Code CLI. It works with Anthropic's current OAuth implementation but may break if Anthropic changes their validation. Use at your own risk.

## License
MIT, see [LICENSE](LICENSE).
