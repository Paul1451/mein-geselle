# Troubleshooting

Things that bit me while building Mein Geselle. Listed in the order you're likely to hit them on a fresh install.

---

## "Unknown toolsets: mein_geselle" warning when running `hermes chat`

The toolset is registered but not enabled in your config.

```bash
# Check:
grep -A1 "^toolsets:" ~/.hermes/config.yaml

# Fix:
hermes config set toolsets '[hermes-cli, mein_geselle]'
# Or run the helper:
./scripts/install_into_hermes.sh /path/to/hermes-agent
```

The warning is non-fatal — Hermes still runs — but your custom tools won't be exposed to the planner.

---

## Hermes ignores my custom tool — no warning, no error

The registry uses AST discovery (see `hermes-agent/tools/registry.py:42-54`). It only finds `registry.register(...)` calls that are **top-level bare expressions** in the module. If you wrap the call:

```python
try:                                # ❌ silently skipped
    from tools.registry import registry
    registry.register(...)
except ImportError:
    pass
```

…the AST scanner sees an `ast.Try` node, not the `ast.Expr` it's looking for. The module never gets imported, the tool never registers, and there's no warning.

Fix: hoist the import and the register call to module top level:

```python
from tools.registry import registry # ✅ discovered

registry.register(
    name="...",
    toolset="mein_geselle",
    ...
)
```

If the module needs to be importable in environments where `tools.registry` doesn't exist (like running `seed.py` standalone), add the hermes-agent root to `sys.path` before the import instead of wrapping it.

---

## Custom tools register but Telegram still doesn't see them

Telegram uses a separate toolset (`hermes-telegram`) by default — not `hermes-cli`. Even if `mein_geselle` is in your top-level `toolsets:`, it isn't exposed to Telegram sessions unless you add it to `platform_toolsets`:

```yaml
# ~/.hermes/config.yaml
platform_toolsets:
  telegram: [mein_geselle]
  cli: [mein_geselle]
```

The install helper does this for you.

---

## WeasyPrint: `OSError: cannot load library 'libgobject-2.0-0'`

You're on macOS Apple Silicon with an x86_64 Python (Rosetta), but `pango` from Homebrew is arm64. The libs can't load across architectures.

```bash
# Check Python arch:
python3 -c "import platform; print(platform.machine())"
# If it prints x86_64 on an M-series Mac, you're on Rosetta.

# Fix: use a native aarch64 Python:
uv python install 3.13 --arch=aarch64
rm -rf .venv && uv venv --python 3.13 .venv && source .venv/bin/activate
uv pip install -e . && uv pip install -r mein-geselle/requirements.txt
```

The `angebot_draft` tool catches this specifically and returns a clear `pdf_error` in the response, so the markdown Angebot draft still works without the PDF.

---

## DeepSeek (or any tier-2 planner) doesn't call `skill_manage` even after a clear correction

Lighter LLMs don't always reach for verbose tools on casual phrases like "merk dir das". Use the `remember_rule` wrapper instead — its schema description is shaped to match natural-language corrections directly.

If you need `skill_manage` specifically, either prompt the agent more explicitly ("Patche das angebot-style Skill so dass …") or switch the planner to a tier-1 model (Claude Sonnet, GPT-4-class).

---

## `hermes skills list` doesn't show `handwerk/*` skills

Hermes reads skills from `~/.hermes/skills/`, not from the repo. The install helper symlinks `~/.hermes/skills/handwerk → mein-geselle/skills/handwerk`. If you ran the install manually:

```bash
ln -sfn $(pwd)/mein-geselle/skills/handwerk ~/.hermes/skills/handwerk
```

Verify with `ls -la ~/.hermes/skills/handwerk`.

---

## `hermes gateway list` says "default — not running" but my gateway is up

`gateway list` checks for installed launchd/systemd services. If you started the gateway with `hermes gateway run` in the foreground, it's a regular process, not a service — so `list` reports "not running" but `gateway_state.json` correctly reports `gateway_state: running`.

This is fine. If you want it as a real background service:

```bash
hermes gateway install   # creates a launchd plist on macOS
hermes gateway start
```

---

## Dashboard activity feed is empty

The dashboard reads from `~/.hermes/state.db` (Hermes' session store). On first install it's empty until the gateway logs its first conversation. Either:

- Send any message to your Telegram bot, then refresh the dashboard, or
- Run a quick CLI prompt: `hermes chat -q "ping" -Q`

The feed will populate within 3 seconds (HTMX auto-refresh).

If the table doesn't exist at all (older Hermes versions), the dashboard falls back to tailing `~/.hermes/logs/agent.log`. Check the footer of the dashboard — it shows `activity source: db` or `activity source: log`.

---

## Tool registers but `hermes doctor` says "not configured"

`hermes doctor` only checks built-in tools against their declared `check_fn` (usually API-key presence). Custom tools aren't audited there. Use the verification one-liner from the README instead:

```bash
python3 -c "
import sys; sys.path.insert(0, '/path/to/hermes-agent')
from tools import registry as r
r.discover_builtin_tools()
from tools.registry import registry
for name in ['customer_db', 'calendar', 'lead_classify', 'angebot_draft', 'remember_rule']:
    print(f'{name} -> {registry.get_toolset_for_tool(name)}')
"
```

All five should print `-> mein_geselle`.

---

If you hit something not on this list, open an issue at <https://github.com/Paul1451/mein-geselle/issues> — happy to extend the doc.
