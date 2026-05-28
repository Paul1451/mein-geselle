#!/usr/bin/env bash
# reset_demo.sh — restore Mein Geselle to a clean demo baseline.
#
# Use before recording a new video take, or before any clean E2E demo run.
#
# What it does:
#   1. Git-reverts the three SKILL.md files to their initial state (v0.1.0, no Learned Rules)
#   2. Drops all the `learning-loop` commits since the initial repo commit
#   3. (--hard) Optionally clears the customer DB and re-seeds it
#   4. (--hard) Optionally truncates today's tool-call log lines so the dashboard counter resets
#   5. (--hard) Optionally deletes the local iCal so the calendar is empty
#
# Usage:
#   ./scripts/reset_demo.sh           # soft: only skills + git
#   ./scripts/reset_demo.sh --hard    # also wipe DB + ical + tool-call log

set -euo pipefail

# Resolve repo root the same way tool_remember_rule does
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILLS_ROOT="$REPO_ROOT/skills/handwerk"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

HARD=0
for arg in "$@"; do
  case "$arg" in
    --hard) HARD=1 ;;
    -h|--help)
      grep -E "^#( |$)" "$0" | sed 's/^# \?//' | head -25
      exit 0 ;;
    *)
      echo "✗ unknown arg: $arg"; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

echo "→ Resetting Mein Geselle demo state"
echo "  repo:   $REPO_ROOT"
echo "  hermes: $HERMES_HOME"
echo "  mode:   $([ "$HARD" = 1 ] && echo HARD || echo soft)"
echo

# ---------------------------------------------------------------------------
# 1. Skills — git revert each file back to the initial commit's content
# ---------------------------------------------------------------------------
echo "[1/4] Reverting skill files to v0.1.0 baseline..."

INITIAL=$(git log --reverse --pretty=format:%H -- skills/handwerk/ | head -1)
if [[ -z "$INITIAL" ]]; then
  echo "  ✗ Could not find initial commit for skills. Aborting."
  exit 1
fi
echo "  initial commit: $(git log -1 --oneline "$INITIAL")"

for skill_dir in angebot_style customer_intake notfall_routing; do
  skill_file="skills/handwerk/$skill_dir/SKILL.md"
  if git show "$INITIAL:$skill_file" > "$skill_file.new" 2>/dev/null; then
    mv "$skill_file.new" "$skill_file"
    echo "  ✓ reset $skill_file → v0.1.0"
  else
    echo "  · $skill_file not present in initial commit, skipping"
    rm -f "$skill_file.new"
  fi
done

# Stage but don't commit — let Paul inspect with `git diff --cached` first.
git add skills/handwerk/

echo
echo "  Skills staged for reset. Inspect with:"
echo "    git diff --cached skills/handwerk/"
echo "  Commit the reset with:"
echo "    git -c user.email=demo@meingesselle.local -c user.name='Mein Geselle' commit -m 'chore: reset skills to v0.1.0 for demo recording'"
echo

# ---------------------------------------------------------------------------
# 2-4. Hard mode: wipe runtime state
# ---------------------------------------------------------------------------
if [[ "$HARD" = 1 ]]; then
  echo "[2/4] Wiping customer DB..."
  rm -f "$HERMES_HOME/data/handwerk.db"
  rm -f "$HERMES_HOME/data/handwerk.db-shm"
  rm -f "$HERMES_HOME/data/handwerk.db-wal"
  echo "  ✓ DB removed"
  echo "  Re-seeding 10 fictional customers..."
  "$REPO_ROOT/tools/seed.py" >/dev/null
  echo "  ✓ DB reseeded"

  echo
  echo "[3/4] Wiping iCal..."
  rm -f "$HERMES_HOME/data/handwerk.ics"
  echo "  ✓ iCal removed (calendar is now empty)"

  echo
  echo "[4/4] Truncating today's tool-call lines from agent.log..."
  today=$(date +%Y-%m-%d)
  if [[ -f "$HERMES_HOME/logs/agent.log" ]]; then
    grep -v "^$today" "$HERMES_HOME/logs/agent.log" > "$HERMES_HOME/logs/agent.log.tmp" || true
    mv "$HERMES_HOME/logs/agent.log.tmp" "$HERMES_HOME/logs/agent.log"
    echo "  ✓ today's entries removed (dashboard tool-call counter will be 0)"
  fi
fi

echo
echo "✓ Reset complete."
echo
echo "Next steps:"
echo "  1. Refresh the dashboard (Cmd+R in browser) → counters should be at baseline"
echo "  2. Start recording with ./scripts/demo_drive.py"
echo "  3. Commit the skill reset BEFORE the video session so the timeline is clean"
