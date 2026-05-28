#!/usr/bin/env python3
"""
demo_drive.py — Interactive demo driver for the Mein Geselle video recording.

The script walks Paul through the 8-step demo at HIS pace. He presses Enter,
the next user message is:

  1. Copied to the macOS clipboard (so he can Cmd+V into Telegram OR a terminal)
  2. Also dispatched through Hermes' CLI (`hermes chat -q ...`) so all five
     custom tools fire AND skill files evolve AND the dashboard updates live.

The simultaneous CLI dispatch means: even if Paul demos the conversation via
Telegram in the video, the dashboard's timeline + skill commits + tool-call
counter still tick up in real time because Hermes is processing in parallel.

Paul keeps full control of pacing — useful for recording multiple takes,
re-doing a clip, or pausing to check the dashboard between messages.

Usage:
    ./scripts/demo_drive.py                          # the canonical 8-message demo
    ./scripts/demo_drive.py --script ./my_demo.txt   # custom script (one msg per line)
    ./scripts/demo_drive.py --no-clipboard           # skip clipboard copy
    ./scripts/demo_drive.py --no-cli                 # skip CLI dispatch (manual mode)
    ./scripts/demo_drive.py --auto-pause 8           # auto-advance every 8 s

Prereqs:
    - Hermes Agent installed and configured (`hermes setup`, env vars set)
    - `hermes` on PATH (or set $HERMES env var)
    - For voice playback (optional): macOS `say` command
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

# Default 8-message demo — mirrors submission/demo_script.md and the storyboard.
DEFAULT_SCRIPT: List[str] = [
    "Hat Frau Müller schon mal angerufen? Ich brauche ihre Telefonnummer.",
    "Bitte draft mir ein Angebot für Frau Müller über Bad-Sanierung, 8 qm Fliesen neu, ungefähr 4500 Euro netto.",
    "Bei Frau Müller gib immer 5 Prozent Skonto bei sofort-Zahlung. Merk dir das für künftige Angebote.",
    "Notfall! Familie Yıldırım hat Wasserschaden in der Küche. Was machen wir?",
    "Ab jetzt: bei Wasserschäden immer zuerst den Notdienst-Klempner Frank Becker anrufen. Merk dir das.",
    "Welche Termine hat Herr Becker diese Woche?",
    "Bitte buch Frau Kowalski für Freitag 14 Uhr für Wohnzimmer streichen, 25 qm.",
    "Termine vor 8:30 will ich nie wieder, ich fahre dann gerade zur Baustelle. Merk dir das für die Zukunft.",
]

# Per-message hints shown to Paul before he advances.
HINTS = [
    "tool: customer_db                          → expect Frau Müller's record",
    "tools: customer_db + angebot_draft         → expect a draft + total ~5355 € gross",
    "tool: remember_rule (THE LEARNING LOOP)    → expect a new dot on angebot-style timeline",
    "tools: lead_classify + customer_db + cal   → expect urgency 5, emergency slot booked",
    "tool: remember_rule                        → expect a new dot on notfall-routing",
    "tool: customer_db                          → expect Becker, no appointments this week",
    "tools: customer_db + calendar              → expect a booked slot on Friday",
    "tool: remember_rule                        → expect a new dot on customer-intake",
]

GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _color(text: str, c: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{c}{text}{RESET}"


def _copy_to_clipboard(text: str) -> bool:
    """Copy *text* to the macOS clipboard via pbcopy. Returns True on success."""
    pbcopy = shutil.which("pbcopy")
    if not pbcopy:
        return False
    try:
        subprocess.run([pbcopy], input=text.encode("utf-8"), check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _find_hermes() -> Optional[Path]:
    """Resolve the hermes CLI binary."""
    env = os.environ.get("HERMES")
    if env and Path(env).exists():
        return Path(env)
    p = shutil.which("hermes")
    if p:
        return Path(p)
    # Fall back to the venv next to mein-geselle
    candidate = REPO_ROOT.parent / "hermes-agent" / ".venv" / "bin" / "hermes"
    if candidate.exists():
        return candidate
    return None


def _dispatch_via_cli(hermes: Path, message: str, timeout: int = 100) -> dict:
    """Run a single hermes chat -q call. Returns {rc, stdout, duration_s}."""
    started = time.time()
    try:
        proc = subprocess.run(
            [str(hermes), "chat", "-q", message, "-Q", "-t", "hermes-cli,mein_geselle"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "rc": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "duration_s": time.time() - started,
        }
    except subprocess.TimeoutExpired:
        return {
            "rc": 124,
            "stdout": "",
            "stderr": f"timed out after {timeout}s",
            "duration_s": time.time() - started,
        }


def _print_banner() -> None:
    print(_color("┌────────────────────────────────────────────────────────────────┐", CYAN))
    print(_color("│  Mein Geselle · Demo Driver                                    │", CYAN))
    print(_color("│  Each step: copy message → press Enter → CLI dispatch          │", CYAN))
    print(_color("│  Open the dashboard at http://localhost:7070 in another window │", CYAN))
    print(_color("└────────────────────────────────────────────────────────────────┘", CYAN))
    print()


def _load_script(path: Optional[Path]) -> List[str]:
    if path is None:
        return DEFAULT_SCRIPT
    if not path.exists():
        sys.exit(f"✗ script file not found: {path}")
    msgs = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not msgs:
        sys.exit(f"✗ no messages in {path}")
    return msgs


def _wait_user_or_timer(auto_pause: Optional[float], turn_index: int) -> str:
    """Block until Paul presses Enter, or auto-advance after *auto_pause* seconds."""
    if auto_pause is None:
        try:
            return input(_color("  ↪ press Enter to send (or 'q' to quit, 's' to skip): ", DIM)).strip()
        except KeyboardInterrupt:
            return "q"
        except EOFError:
            return "q"
    else:
        print(_color(f"  ⏳ auto-sending in {auto_pause}s — Ctrl+C to abort", DIM), end="", flush=True)
        for _ in range(int(auto_pause)):
            time.sleep(1)
            print(".", end="", flush=True)
        print()
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    parser.add_argument("--script", type=Path, default=None, help="Custom one-per-line script file")
    parser.add_argument("--no-clipboard", action="store_true", help="Don't copy to clipboard")
    parser.add_argument("--no-cli", action="store_true", help="Don't dispatch to hermes CLI")
    parser.add_argument("--auto-pause", type=float, default=None, metavar="SECS", help="Auto-advance every N seconds")
    parser.add_argument("--reset-first", action="store_true", help="Run reset_demo.sh before starting")
    args = parser.parse_args()

    msgs = _load_script(args.script)
    hermes = None if args.no_cli else _find_hermes()

    if args.reset_first:
        print(_color("→ Running reset_demo.sh first...", YELLOW))
        rc = subprocess.call([str(REPO_ROOT / "scripts" / "reset_demo.sh")])
        if rc != 0:
            sys.exit("✗ reset failed; aborting")
        print()

    _print_banner()

    if hermes:
        print(_color(f"  CLI dispatch:  {hermes}", DIM))
    else:
        print(_color("  CLI dispatch:  DISABLED (no hermes CLI found)", YELLOW))
    print(_color(f"  Clipboard:     {'pbcopy' if not args.no_clipboard else 'DISABLED'}", DIM))
    print(_color(f"  Auto-pause:    {args.auto_pause}s" if args.auto_pause else "  Auto-pause:    OFF (manual)", DIM))
    print(_color(f"  Messages:      {len(msgs)}", DIM))
    print()

    # Be polite to Ctrl+C on long waits
    signal.signal(signal.SIGINT, signal.default_int_handler)

    for i, msg in enumerate(msgs, start=1):
        hint = HINTS[i - 1] if i - 1 < len(HINTS) else ""
        print()
        print(_color(f"━━━ Step {i}/{len(msgs)} ━━━", BOLD))
        print(_color(f"  hint:  {hint}", DIM))
        print()
        print(_color("  user: " + textwrap.fill(msg, width=70, subsequent_indent="        "), GREEN))
        print()
        if not args.no_clipboard:
            ok = _copy_to_clipboard(msg)
            print(_color(f"  ✓ message copied to clipboard ({'pbcopy' if ok else 'failed'})", DIM if ok else RED))

        cmd = _wait_user_or_timer(args.auto_pause, i)
        if cmd.lower() in {"q", "quit", "exit"}:
            print(_color("  ✗ aborted by user", YELLOW))
            return 0
        if cmd.lower() in {"s", "skip"}:
            print(_color("  → skipped", YELLOW))
            continue

        if hermes:
            print(_color("  ⚙ dispatching to hermes CLI...", DIM), flush=True)
            res = _dispatch_via_cli(hermes, msg)
            if res["rc"] != 0:
                print(_color(f"  ✗ rc={res['rc']} ({res['duration_s']:.1f}s)  err={res['stderr'][:100]}", RED))
            else:
                reply = res["stdout"].split("-----")[-1].strip()
                snippet = textwrap.shorten(reply, width=160, placeholder="…")
                print(_color(f"  ⏱  {res['duration_s']:.1f}s", DIM))
                print(_color("  hermes:", CYAN), snippet)

    print()
    print(_color("━━━ Demo complete ━━━", BOLD))
    print(_color("  Check the dashboard at http://localhost:7070 — the timeline should now show", DIM))
    print(_color("  3 new commits (one per remember_rule call) and the growth chart should tick up.", DIM))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
