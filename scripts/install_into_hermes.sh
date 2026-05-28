#!/usr/bin/env bash
# install_into_hermes.sh — wire Mein Geselle into an existing Hermes Agent install.
#
# Usage:
#   ./scripts/install_into_hermes.sh /path/to/hermes-agent
#
# Idempotent. Safe to re-run.

set -euo pipefail

HERMES_ROOT="${1:-$(realpath "$(dirname "$0")/../../hermes-agent" 2>/dev/null || true)}"
GESELLE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ ! -d "$HERMES_ROOT" ]] || [[ ! -f "$HERMES_ROOT/run_agent.py" ]]; then
  echo "✗ hermes-agent root not found at: $HERMES_ROOT"
  echo "  Pass the path explicitly:  $0 /path/to/hermes-agent"
  exit 2
fi

echo "Mein Geselle root: $GESELLE_ROOT"
echo "Hermes-Agent root: $HERMES_ROOT"
echo

# ---------------------------------------------------------------------------
# 1. Symlink the five custom tools into hermes-agent/tools/ as mg_tool_*.py
# ---------------------------------------------------------------------------
echo "[1/4] Symlinking custom tools..."
for tool_path in "$GESELLE_ROOT"/tools/tool_*.py; do
  fname=$(basename "$tool_path")
  link="$HERMES_ROOT/tools/mg_${fname}"
  ln -sfn "$tool_path" "$link"
  echo "  ✓ $(basename "$link") -> $tool_path"
done

# ---------------------------------------------------------------------------
# 2. Symlink the handwerk skills into ~/.hermes/skills/
# ---------------------------------------------------------------------------
echo
echo "[2/4] Symlinking skills into ~/.hermes/skills/handwerk ..."
mkdir -p "$HOME/.hermes/skills"
ln -sfn "$GESELLE_ROOT/skills/handwerk" "$HOME/.hermes/skills/handwerk"
echo "  ✓ ~/.hermes/skills/handwerk -> $GESELLE_ROOT/skills/handwerk"

# ---------------------------------------------------------------------------
# 3. Patch ~/.hermes/config.yaml to enable mein_geselle for cli + telegram
# ---------------------------------------------------------------------------
echo
echo "[3/4] Enabling mein_geselle toolset in ~/.hermes/config.yaml ..."
"$HERMES_ROOT/.venv/bin/python3" - <<'PY'
import yaml
from pathlib import Path

cfg_path = Path.home() / ".hermes" / "config.yaml"
if not cfg_path.exists():
    print("  ✗ ~/.hermes/config.yaml missing — run `hermes setup` first.")
    raise SystemExit(2)

with cfg_path.open() as f:
    cfg = yaml.safe_load(f) or {}

# Top-level toolsets list
ts = cfg.get("toolsets") or []
if "mein_geselle" not in ts:
    ts.append("mein_geselle")
    cfg["toolsets"] = ts
    print("  ✓ added mein_geselle to top-level toolsets")
else:
    print("  · already in top-level toolsets")

# Per-platform overrides
pt = cfg.get("platform_toolsets")
if not isinstance(pt, dict):
    pt = {}
    cfg["platform_toolsets"] = pt
for platform in ("cli", "telegram"):
    lst = pt.get(platform)
    if not isinstance(lst, list):
        lst = []
        pt[platform] = lst
    if "mein_geselle" not in lst:
        lst.append("mein_geselle")
        print(f"  ✓ added mein_geselle to platform_toolsets.{platform}")
    else:
        print(f"  · already in platform_toolsets.{platform}")

with cfg_path.open("w") as f:
    yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
PY

# ---------------------------------------------------------------------------
# 4. Seed the customer DB (idempotent)
# ---------------------------------------------------------------------------
echo
echo "[4/4] Seeding customer DB ..."
"$HERMES_ROOT/.venv/bin/python3" "$GESELLE_ROOT/tools/seed.py"

echo
echo "✓ Mein Geselle installed."
echo
echo "Next steps:"
echo "  1. Make sure ~/.hermes/.env contains your OPENROUTER_API_KEY (or other provider key)"
echo "     and (optional) TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS."
echo "  2. Start the gateway:  hermes gateway run"
echo "  3. Open the dashboard: python $GESELLE_ROOT/dashboard/app.py  →  http://localhost:7070"
echo "  4. Chat with the agent:  hermes chat -t mein_geselle,hermes-cli"
