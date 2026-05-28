#!/usr/bin/env python3
"""
Remember-Rule Tool — Mein Geselle (Hermes plug-in tool)

Centerpiece of the "Mein Geselle" learning-loop story. Wraps Hermes'
native ``skill_manage`` semantics with a single-purpose, low-friction
entry point so that even weaker LLMs (DeepSeek, smaller Llamas, …) can
reliably persist a rule when the user says "merk dir das" / "always do
X" / "never Y".

What this tool does on every call
---------------------------------
1. Resolves the right SKILL.md from the requested ``category``.
2. Follows the ``~/.hermes/skills/...`` symlink down to the real file
   inside the ``mein-geselle`` git repo so the commit lands in the
   right working tree.
3. Acquires an ``fcntl.flock`` so two concurrent calls cannot corrupt
   the markdown file.
4. Parses the YAML frontmatter, bumps the semver patch version
   (X.Y.Z → X.Y.(Z+1)) by hand — no PyYAML dependency required.
5. Appends a bullet under a ``## 📒 Learned Rules`` section, creating
   the section if missing (always at the end of the body, before any
   trailing whitespace).
6. ``git -C <repo> add <skill_file> && git commit ...`` with an inline
   author so the commit does not depend on the user's global git
   config. Falls back gracefully if git is unavailable.
7. Returns a JSON string the agent can read back to the user.

Registration mirrors ``tool_customer_db.py`` — top-level import of the
registry, top-level ``registry.register(...)``, no try/except wrap.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from tools.registry import registry  # type: ignore


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

# Real on-disk repo (the ~/.hermes/skills/handwerk path is a symlink to this).
REPO_ROOT = Path("/Users/paul/Desktop/hermes-challenge/mein-geselle")
SKILLS_ROOT = REPO_ROOT / "skills" / "handwerk"

# Category → (skill_dir_name, public_skill_name).
# The directory name on disk uses underscores; the canonical skill name
# uses dashes (matches the `name:` field in the YAML frontmatter).
CATEGORY_MAP: Dict[str, Tuple[str, str]] = {
    "quoting":    ("angebot_style",    "angebot-style"),
    "scheduling": ("customer_intake",  "customer-intake"),  # reuse intake
    "intake":     ("customer_intake",  "customer-intake"),
    "emergency":  ("notfall_routing",  "notfall-routing"),
    "other":      ("angebot_style",    "angebot-style"),    # quoting default
}

LEARNED_HEADING = "## 📒 Learned Rules"

# Git author used for every learned-rule commit. We pass these via
# ``-c user.name=... -c user.email=...`` so we do not have to touch the
# user's global git config.
GIT_AUTHOR_NAME = "Mein Geselle"
GIT_AUTHOR_EMAIL = "hermes@meingesselle.local"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso_minutes() -> str:
    """Local timestamp with minute precision — short enough for a bullet."""
    return datetime.now().replace(microsecond=0, second=0).isoformat(timespec="minutes")


def _resolve_skill_file(category: str) -> Tuple[Path, str]:
    """Map a category to the real SKILL.md path and the public skill name.

    Raises ``KeyError`` if the category is not recognised.
    """
    dir_name, skill_name = CATEGORY_MAP[category]
    skill_file = SKILLS_ROOT / dir_name / "SKILL.md"
    # Follow any symlinks down to the real on-disk file. If the caller
    # passed a path that lives under ``~/.hermes/skills/handwerk`` we still
    # land inside the repo because that whole subtree is a symlink.
    skill_file = skill_file.resolve()
    return skill_file, skill_name


def _split_frontmatter(text: str) -> Tuple[str, str]:
    """Split a markdown file into ``(frontmatter_block, body)``.

    The frontmatter block keeps its surrounding ``---`` fences and the
    trailing newline. If the file has no frontmatter we return
    ``("", text)`` and the caller appends without touching the version.
    """
    if not text.startswith("---"):
        return "", text
    # Find the closing fence on its own line.
    m = re.search(r"^---\s*$", text[3:], flags=re.MULTILINE)
    if m is None:
        return "", text
    end = 3 + m.end()
    # Consume trailing newline after the closing fence if present.
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:end], text[end:]


def _bump_patch(frontmatter: str) -> Tuple[str, str]:
    """Bump the ``version: X.Y.Z`` line in a frontmatter block by one patch.

    Returns ``(new_frontmatter, new_version)``. If no version line is found
    we inject ``version: 0.1.1`` so subsequent calls keep bumping.
    """
    pattern = re.compile(r"^(version:\s*)(\d+)\.(\d+)\.(\d+)\s*$", flags=re.MULTILINE)
    match = pattern.search(frontmatter)
    if match is None:
        # Inject a fresh version field before the closing fence.
        new_version = "0.1.1"
        injected = re.sub(
            r"(^---\s*$)",
            lambda m, c=[0]: (f"version: {new_version}\n{m.group(1)}" if c.append(1) or len(c) == 2 else m.group(1)),
            frontmatter,
            flags=re.MULTILINE,
        )
        return injected, new_version

    major, minor, patch = int(match.group(2)), int(match.group(3)), int(match.group(4))
    new_version = f"{major}.{minor}.{patch + 1}"
    new_frontmatter = pattern.sub(f"{match.group(1)}{new_version}", frontmatter, count=1)
    return new_frontmatter, new_version


def _append_rule_to_body(body: str, bullet: str) -> str:
    """Append ``bullet`` under the ``## 📒 Learned Rules`` section.

    Behaviour:
      - If the heading already exists, the bullet is appended at the END
        of that section (just before the next ``## `` heading or EOF).
      - If not, we append a fresh heading + bullet at the end of the body
        (after stripping trailing whitespace, preserving exactly one
        trailing newline).
    """
    # Normalise body to end with exactly one newline so our insertions
    # produce predictable diffs.
    body_stripped = body.rstrip() + "\n"

    heading_re = re.compile(
        r"^" + re.escape(LEARNED_HEADING) + r"\s*$",
        flags=re.MULTILINE,
    )
    heading_match = heading_re.search(body_stripped)
    if heading_match is None:
        # Fresh section. Blank line + heading + blank line + bullet.
        return body_stripped + "\n" + LEARNED_HEADING + "\n\n" + bullet + "\n"

    # Find the end of the Learned Rules section: next top-level "## "
    # heading or end of file.
    section_start = heading_match.end()
    next_heading = re.search(r"^##\s", body_stripped[section_start:], flags=re.MULTILINE)
    if next_heading is None:
        section_end = len(body_stripped)
        tail = ""
    else:
        section_end = section_start + next_heading.start()
        tail = body_stripped[section_end:]

    section_body = body_stripped[section_start:section_end].rstrip()
    # Append the new bullet on its own line.
    new_section = section_body + "\n" + bullet + "\n"
    # Re-attach with a blank line between the section and the next heading.
    if tail:
        new_section += "\n"
    return body_stripped[:section_start] + new_section + tail


def _git_available(repo: Path) -> bool:
    """Return True iff ``git`` is on PATH and ``repo`` is inside a worktree."""
    if shutil.which("git") is None:
        return False
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_commit_skill(
    repo: Path,
    skill_file: Path,
    skill_name: str,
    rule_short: str,
) -> Optional[str]:
    """Stage + commit the skill file. Returns the short commit SHA or None.

    All git operations run with ``-c user.name`` / ``-c user.email`` so
    the commit never depends on the user's global git config.
    """
    if not _git_available(repo):
        return None

    rel = skill_file.relative_to(repo)
    base_cmd = [
        "git",
        "-C", str(repo),
        "-c", f"user.name={GIT_AUTHOR_NAME}",
        "-c", f"user.email={GIT_AUTHOR_EMAIL}",
    ]

    add = subprocess.run(base_cmd + ["add", str(rel)], capture_output=True, text=True)
    if add.returncode != 0:
        return None

    # If nothing is staged (e.g. identical write) git commit would fail —
    # detect and bail out cleanly.
    status = subprocess.run(
        base_cmd + ["diff", "--cached", "--quiet", "--", str(rel)],
        capture_output=True,
        text=True,
    )
    if status.returncode == 0:
        return None  # nothing to commit

    msg = f"skill({skill_name}): learned rule — {rule_short}"
    commit = subprocess.run(
        base_cmd + ["commit", "-m", msg, "--", str(rel)],
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        return None

    rev = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    if rev.returncode != 0:
        return None
    return rev.stdout.strip() or None


# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------

def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def remember_rule_tool(args: Dict[str, Any], **_kwargs: Any) -> str:
    """Append a learned rule to the appropriate SKILL.md and commit it.

    Returns a JSON string. See the module docstring + schema below.
    """
    args = args or {}
    rule = (args.get("rule") or "").strip()
    if not rule:
        return _err("rule is required and must be non-empty")
    if len(rule) > 200:
        # Truncate hard rather than reject — the agent gets one chance
        # and we'd rather store *something* than lose the signal.
        rule = rule[:197].rstrip() + "..."

    category = (args.get("category") or "").strip()
    if category not in CATEGORY_MAP:
        return _err(
            f"invalid category {category!r}; expected one of "
            f"{sorted(CATEGORY_MAP)}"
        )

    scope = (args.get("scope") or "global").strip() or "global"
    source_msg = (args.get("source_msg") or "").strip()
    if len(source_msg) > 120:
        source_msg = source_msg[:117].rstrip() + "..."

    try:
        skill_file, skill_name = _resolve_skill_file(category)
    except KeyError:
        return _err(f"no skill file mapped for category {category!r}")

    # Make sure the parent directory exists — the skill file should always
    # exist already in this project, but we defensively create the dir so
    # this tool never fails on a fresh checkout.
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    if not skill_file.exists():
        # Bootstrap a minimal stub so the rest of the pipeline keeps working.
        stub = (
            "---\n"
            f"name: {skill_name}\n"
            f"description: \"Auto-created stub for {skill_name}.\"\n"
            "version: 0.1.0\n"
            "---\n\n"
            f"# {skill_name}\n\n"
        )
        skill_file.write_text(stub, encoding="utf-8")

    bullet = f"- {_now_iso_minutes()} [{scope}] ({category}): {rule}"
    if source_msg:
        bullet += f"  _(src: {source_msg})_"

    # Lock + read + transform + write atomically with respect to other
    # callers on the same machine. fcntl.flock is Unix-only — fine on
    # macOS / Linux (and we explicitly target macOS here).
    with open(skill_file, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            original = fh.read()
            frontmatter, body = _split_frontmatter(original)
            new_frontmatter, new_version = _bump_patch(frontmatter) if frontmatter else ("", "")
            new_body = _append_rule_to_body(body, bullet)
            new_text = (new_frontmatter + new_body) if frontmatter else new_body
            fh.seek(0)
            fh.truncate()
            fh.write(new_text)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # Commit the change. The commit subject must stay under 72 chars-ish,
    # so we truncate the rule to 60.
    rule_short = rule if len(rule) <= 60 else rule[:57].rstrip() + "..."
    commit_sha = _git_commit_skill(REPO_ROOT, skill_file, skill_name, rule_short)

    response = {
        "ok": True,
        "skill": skill_name,
        "skill_file": str(skill_file),
        "new_version": new_version or None,
        "rule_appended": bullet,
        "git_commit": commit_sha,
    }
    return json.dumps(response, ensure_ascii=False)


def check_remember_rule_requirements() -> bool:
    """Toolset availability check. Always True — no external creds needed."""
    return True


# ---------------------------------------------------------------------------
# OpenAI function-calling schema
# ---------------------------------------------------------------------------

REMEMBER_RULE_SCHEMA = {
    "name": "remember_rule",
    "description": (
        "Persist a rule, preference, or correction the user just gave you "
        "(e.g. 'merk dir das', 'always do X', 'never Y', customer-specific "
        "behavior, quoting style, scheduling habit). Appends the rule to the "
        "appropriate skill markdown file and creates a git commit so the "
        "learning is versioned and visible. Call this whenever the user gives "
        "feedback that should outlive the current conversation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": (
                    "The rule text in the user's own words (German is fine). "
                    "Keep it ≤ 200 chars. Be concrete."
                ),
            },
            "scope": {
                "type": "string",
                "description": (
                    "Where the rule applies. Use one of: "
                    "'global' for shop-wide rules, "
                    "'customer:<name>' for a specific customer "
                    "(e.g. 'customer:Müller'), "
                    "or a skill name like 'angebot-style', 'customer-intake', "
                    "'notfall-routing'."
                ),
                "default": "global",
            },
            "category": {
                "type": "string",
                "enum": ["quoting", "scheduling", "intake", "emergency", "other"],
                "description": (
                    "What this rule is about. Determines which skill file "
                    "gets updated."
                ),
            },
            "source_msg": {
                "type": "string",
                "description": (
                    "(Optional) The user message that prompted this rule, for "
                    "audit trail. ≤ 120 chars."
                ),
            },
        },
        "required": ["rule", "category"],
    },
}


# ---------------------------------------------------------------------------
# Hermes registry hook
# ---------------------------------------------------------------------------
# Top-level call so the hermes-agent registry AST scanner discovers this
# module. NO try/except wrap — that would hide the registration from the
# discovery pass.

registry.register(
    name="remember_rule",
    toolset="mein_geselle",
    schema=REMEMBER_RULE_SCHEMA,
    handler=remember_rule_tool,
    check_fn=check_remember_rule_requirements,
    emoji="🧠",
    description=(
        "Persist a user-given rule into the right Handwerk SKILL.md, bump "
        "its semver, and git-commit the change."
    ),
)


__all__ = [
    "CATEGORY_MAP",
    "REMEMBER_RULE_SCHEMA",
    "remember_rule_tool",
    "check_remember_rule_requirements",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Three representative calls, one per category mapping target.
    demos = [
        {
            "rule": "Always offer 5% Skonto for immediate payment.",
            "scope": "customer:Müller",
            "category": "quoting",
            "source_msg": "Frau Müller zahlt gern sofort — merk dir das.",
        },
        {
            "rule": "Notfälle (Wasserschaden, Strom aus) sofort an Maler Schulz mobil eskalieren.",
            "scope": "global",
            "category": "emergency",
            "source_msg": "Bei akutem Wasserschaden direkt anrufen, nicht warten.",
        },
        {
            "rule": "Termine nie vor 08:30 Uhr vereinbaren — Schulz fährt vorher Material.",
            "scope": "global",
            "category": "scheduling",
        },
    ]

    print("=" * 72)
    print("remember_rule smoke test")
    print("=" * 72)

    for i, payload in enumerate(demos, 1):
        print(f"\n--- call #{i}: category={payload['category']} ---")
        out = remember_rule_tool(payload)
        print("raw response:", out)
        parsed = json.loads(out)
        print(f"  skill_file : {parsed.get('skill_file')}")
        print(f"  new_version: {parsed.get('new_version')}")
        print(f"  git_commit : {parsed.get('git_commit')}")

        skill_path = Path(parsed["skill_file"])
        tail_lines = skill_path.read_text(encoding="utf-8").splitlines()[-5:]
        print("  last 5 lines of SKILL.md:")
        for line in tail_lines:
            print(f"    | {line}")

        # Show recent commits touching this skill file.
        log = subprocess.run(
            [
                "git", "-C", str(REPO_ROOT),
                "log", "--oneline", "-3", "--", str(skill_path.relative_to(REPO_ROOT)),
            ],
            capture_output=True, text=True,
        )
        print("  git log --oneline -3:")
        for line in (log.stdout or "").splitlines():
            print(f"    | {line}")

    print("\nDone.")
