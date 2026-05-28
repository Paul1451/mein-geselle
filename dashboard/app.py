"""Mein Geselle - Workshop Console.

A single-page dashboard that visually proves Hermes is learning over time.
Reads skill files + git history + Hermes runtime state from local sources.
Pure read-only; no auth. Serves on http://localhost:7070.

Tech: FastAPI + Jinja2 server-side render + HTMX for partial updates.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent  # mein-geselle
SKILLS_GLOB = REPO_ROOT / "skills" / "handwerk"
HERMES_HOME = Path.home() / ".hermes"
HANDWERK_DB = HERMES_HOME / "data" / "handwerk.db"
STATE_DB = HERMES_HOME / "state.db"
AGENT_LOG = HERMES_HOME / "logs" / "agent.log"
GATEWAY_STATE = HERMES_HOME / "gateway_state.json"

# ---------------------------------------------------------------------------
# FastAPI setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Mein Geselle — Workshop Console")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CommitEvent:
    sha: str
    iso_date: str
    subject: str

    @property
    def rel_age(self) -> str:
        return _humanize(self.iso_date)


@dataclass
class Skill:
    slug: str  # directory name, e.g. customer_intake
    path: Path
    name: str  # from frontmatter
    description: str
    version: str
    body_lines: int
    mtime: float
    commits: list[CommitEvent] = field(default_factory=list)

    @property
    def is_evolving(self) -> bool:
        return (datetime.now().timestamp() - self.mtime) < 24 * 3600

    @property
    def last_edit_human(self) -> str:
        return _humanize_ts(self.mtime)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _humanize(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return _humanize_ts(dt.timestamp())


def _humanize_ts(ts: float) -> str:
    delta = datetime.now().timestamp() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _git(args: list[str], cwd: Path = REPO_ROOT) -> str:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return res.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_skill_file(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, m.group(2)


def _skill_commits(rel_path: str) -> list[CommitEvent]:
    out = _git(
        [
            "log",
            "--follow",
            "--pretty=format:%h%x09%aI%x09%s",
            "--",
            rel_path,
        ]
    )
    events: list[CommitEvent] = []
    for line in out.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            events.append(CommitEvent(parts[0], parts[1], parts[2]))
    # Reverse so oldest is first (timeline reads left to right)
    return list(reversed(events))


def load_skills() -> list[Skill]:
    skills: list[Skill] = []
    if not SKILLS_GLOB.is_dir():
        return skills
    for skill_dir in sorted(SKILLS_GLOB.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        fm, body = _parse_skill_file(skill_md)
        rel = skill_md.relative_to(REPO_ROOT).as_posix()
        skills.append(
            Skill(
                slug=skill_dir.name,
                path=skill_md,
                name=str(fm.get("name") or skill_dir.name),
                description=str(fm.get("description") or "").strip(),
                version=str(fm.get("version") or "0.0.0"),
                body_lines=len(body.splitlines()),
                mtime=skill_md.stat().st_mtime,
                commits=_skill_commits(rel),
            )
        )
    return skills


# ---------------------------------------------------------------------------
# Database counters
# ---------------------------------------------------------------------------


def handwerk_counts() -> dict[str, int]:
    out = {"customers": 0, "appointments": 0, "angebote": 0}
    if not HANDWERK_DB.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{HANDWERK_DB}?mode=ro", uri=True)
        try:
            for t in out:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {t}")
                    out[t] = int(cur.fetchone()[0])
                except sqlite3.Error:
                    out[t] = 0
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return out


def tool_calls_today() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not AGENT_LOG.exists():
        return counts
    today = datetime.now().strftime("%Y-%m-%d")
    pattern = re.compile(r"tool\s+(\S+)\s+completed")
    try:
        with AGENT_LOG.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if today not in line:
                    continue
                m = pattern.search(line)
                if m:
                    counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    except OSError:
        pass
    return counts


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------


@dataclass
class ActivityEntry:
    when: str
    platform: str
    role: str
    text: str


def _activity_from_db() -> list[ActivityEntry] | None:
    if not STATE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        try:
            # Verify sessions table exists & has expected columns
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if not {"id", "source", "started_at"}.issubset(cols):
                return None
            # Pull last 10 messages joined to their session for platform
            rows = conn.execute(
                """
                SELECT m.timestamp, s.source, m.role, m.content
                FROM messages m
                LEFT JOIN sessions s ON s.id = m.session_id
                WHERE m.content IS NOT NULL AND m.content != ''
                  AND m.role IN ('user','assistant')
                ORDER BY m.timestamp DESC
                LIMIT 10
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    entries: list[ActivityEntry] = []
    for ts, source, role, content in rows:
        try:
            when = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
        except (TypeError, ValueError):
            when = "—"
        entries.append(
            ActivityEntry(
                when=when,
                platform=(source or "unknown").lower(),
                role=role or "?",
                text=_truncate(content or "", 160),
            )
        )
    return entries


def _activity_from_log() -> list[ActivityEntry]:
    entries: list[ActivityEntry] = []
    if not AGENT_LOG.exists():
        return entries
    try:
        with AGENT_LOG.open(encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-2000:]
    except OSError:
        return entries
    msg_re = re.compile(
        r"^(\S+ \S+).*inbound message:.*platform=(\S+).*msg='(.*)'"
    )
    rep_re = re.compile(
        r"^(\S+ \S+).*response ready:.*platform=(\S+).*response=(\d+) chars"
    )
    for line in tail:
        m = msg_re.search(line)
        if m:
            entries.append(
                ActivityEntry(
                    when=m.group(1).split(" ")[1][:8],
                    platform=m.group(2),
                    role="user",
                    text=_truncate(m.group(3), 160),
                )
            )
            continue
        m = rep_re.search(line)
        if m:
            entries.append(
                ActivityEntry(
                    when=m.group(1).split(" ")[1][:8],
                    platform=m.group(2),
                    role="assistant",
                    text=f"response sent ({m.group(3)} chars)",
                )
            )
    return list(reversed(entries))[:10]


def get_activity() -> tuple[list[ActivityEntry], str]:
    """Return (entries, source) where source is 'db' or 'log'."""
    db_entries = _activity_from_db()
    if db_entries:
        return db_entries, "db"
    return _activity_from_log(), "log"


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Gateway liveness
# ---------------------------------------------------------------------------


def gateway_status() -> dict[str, Any]:
    info = {"running": False, "pid": None, "label": "Gateway: down"}
    if not GATEWAY_STATE.exists():
        return info
    try:
        data = json.loads(GATEWAY_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return info
    pid = data.get("pid")
    state = data.get("gateway_state")
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            alive = False
    if state == "running" and alive:
        info.update({"running": True, "pid": pid, "label": "Gateway: running"})
    return info


# ---------------------------------------------------------------------------
# Skill-growth chart data
# ---------------------------------------------------------------------------


def _file_at_commit(rel_path: str, sha: str) -> str | None:
    res = subprocess.run(
        ["git", "show", f"{sha}:{rel_path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode != 0:
        return None
    return res.stdout


def build_growth_series(skills: list[Skill]) -> dict[str, Any]:
    """Return data describing total LOC across all skills over commit history."""
    # Collect (date, skill_slug, sha) tuples
    events: list[tuple[str, str, str]] = []
    for sk in skills:
        rel = sk.path.relative_to(REPO_ROOT).as_posix()
        for c in sk.commits:
            events.append((c.iso_date, rel, c.sha))
    events.sort(key=lambda e: e[0])

    # For each event, compute total LOC across all skills at that point in time.
    # Approx: cumulative — replace each skill's LOC with its size at the latest
    # commit seen so far for that skill.
    skill_loc: dict[str, int] = {}
    series: list[tuple[str, int]] = []
    for iso_date, rel, sha in events:
        content = _file_at_commit(rel, sha)
        if content is None:
            continue
        # Strip frontmatter to count body lines (consistent with body_lines).
        m = _FRONTMATTER_RE.match(content)
        body = m.group(2) if m else content
        skill_loc[rel] = len(body.splitlines())
        total = sum(skill_loc.values())
        series.append((iso_date, total))

    if not series:
        # Fall back to single point with current totals
        total_now = sum(sk.body_lines for sk in skills)
        return {
            "points": [(0, total_now)],
            "baseline": total_now,
            "today": total_now,
            "pct": 0,
            "path_d": "",
            "width": 360,
            "height": 240,
        }

    baseline = series[0][1]
    today = sum(sk.body_lines for sk in skills)  # uses current HEAD bodies
    pct = 0 if baseline == 0 else round((today - baseline) * 100 / baseline)

    # Build SVG path
    width, height = 360, 240
    pad_l, pad_r, pad_t, pad_b = 32, 12, 18, 28
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    values = [v for _, v in series] + [today]
    vmin = min(values)
    vmax = max(values)
    span = max(vmax - vmin, 1)
    n = len(values)
    coords: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        x = pad_l + (inner_w * (i / max(n - 1, 1)))
        y = pad_t + inner_h - (inner_h * (v - vmin) / span)
        coords.append((x, y))
    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)

    return {
        "points": coords,
        "baseline": baseline,
        "today": today,
        "pct": pct,
        "path_d": path_d,
        "width": width,
        "height": height,
        "vmin": vmin,
        "vmax": vmax,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    skills = load_skills()
    counts = handwerk_counts()
    tools = tool_calls_today()
    tool_total = sum(tools.values())
    activity, source = get_activity()
    growth = build_growth_series(skills)
    gw = gateway_status()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "skills": skills,
            "counts": counts,
            "tool_total": tool_total,
            "tools": tools,
            "activity": activity,
            "activity_source": source,
            "growth": growth,
            "gateway": gw,
            "now": datetime.now().strftime("%H:%M:%S"),
        },
    )


@app.get("/activity", response_class=HTMLResponse)
async def activity_partial(request: Request) -> HTMLResponse:
    activity, source = get_activity()
    counts = handwerk_counts()
    tools = tool_calls_today()
    tool_total = sum(tools.values())
    return templates.TemplateResponse(
        request,
        "_activity.html",
        {
            "activity": activity,
            "activity_source": source,
            "counts": counts,
            "tool_total": tool_total,
            "now": datetime.now().strftime("%H:%M:%S"),
        },
    )


@app.get("/skill/{slug}/diff/{sha}", response_class=HTMLResponse)
async def skill_diff(request: Request, slug: str, sha: str) -> HTMLResponse:
    # Find skill
    skill_md = SKILLS_GLOB / slug / "SKILL.md"
    if not skill_md.is_file():
        return HTMLResponse("<pre>skill not found</pre>", status_code=404)
    rel = skill_md.relative_to(REPO_ROOT).as_posix()
    old = _file_at_commit(rel, sha) or ""
    new = skill_md.read_text(encoding="utf-8")
    diff_lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=False),
            new.splitlines(keepends=False),
            fromfile=f"{rel}@{sha}",
            tofile=f"{rel}@HEAD",
            lineterm="",
            n=3,
        )
    )
    return templates.TemplateResponse(
        request,
        "_skill_diff.html",
        {"diff_lines": diff_lines, "sha": sha},
    )


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=7070,
        reload=False,
        log_level="info",
    )
