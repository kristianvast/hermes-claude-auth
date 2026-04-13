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
- **"hermes-agent not found"**: Make sure Hermes is installed at `~/.hermes/hermes-agent/`
- **"No virtualenv found"**: Set `HERMES_VENV` to point to your venv
- **Patch not loading**: Check `journalctl --user -u hermes-gateway -n 50` for `[anthropic_billing_bypass]` or `[hermes-claude-auth]` messages
- **HTTP 400 persists**: The billing salt may have been rotated by Anthropic. Check for updates to this repo.

## Credits
- [griffinmartin/opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth), the original TypeScript implementation for opencode (MIT)
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), the AI agent this patches (MIT)

## Disclaimer
This uses Claude Code subscription credentials outside the official Claude Code CLI. It works with Anthropic's current OAuth implementation but may break if Anthropic changes their validation. Use at your own risk.

## License
MIT, see [LICENSE](LICENSE).
