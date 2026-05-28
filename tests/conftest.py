"""Shared pytest fixtures for the Mein Geselle tool test suite.

Sets up sys.path so that ``from tools.registry import registry`` resolves
(every tool module performs this import at module load time), then exposes
the per-tool monkeypatching fixtures the individual test files rely on.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
# Mirror the pattern from mein-geselle/tools/seed.py: put both the
# mein-geselle/tools dir AND the hermes-agent repo root on sys.path BEFORE
# the tool modules are imported, so the top-level `from tools.registry
# import registry` line resolves to hermes-agent/tools/registry.py.

_TESTS_DIR = Path(__file__).resolve().parent
_MEIN_GESELLE_ROOT = _TESTS_DIR.parent
_TOOLS_DIR = _MEIN_GESELLE_ROOT / "tools"
_HERMES_ROOT_ENV = os.environ.get("_HERMES_ROOT")
if _HERMES_ROOT_ENV:
    _HERMES_ROOT = Path(_HERMES_ROOT_ENV).expanduser().resolve()
else:
    _HERMES_ROOT = _MEIN_GESELLE_ROOT.parent / "hermes-agent"

if not _HERMES_ROOT.exists():
    raise RuntimeError(
        f"hermes-agent root not found at {_HERMES_ROOT}. "
        "Set _HERMES_ROOT env var or symlink the repo as a sibling of "
        "mein-geselle."
    )

for path in (str(_HERMES_ROOT), str(_TOOLS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Provide a temporary SQLite DB path, monkeypatched into tool_customer_db."""
    import tool_customer_db
    import tool_angebot_draft

    db_path = tmp_path / "handwerk.db"
    monkeypatch.setattr(tool_customer_db, "DB_PATH", db_path)
    monkeypatch.setattr(tool_angebot_draft, "DB_PATH", db_path)
    # Bootstrap an empty DB with the full schema.
    conn = tool_customer_db.connect(db_path)
    conn.close()
    return db_path


@pytest.fixture
def tmp_skills_root(tmp_path, monkeypatch):
    """Temporary copy of skills/handwerk inside a fresh git repo."""
    import tool_remember_rule

    repo_root = tmp_path / "repo"
    skills_root = repo_root / "skills" / "handwerk"
    src = _MEIN_GESELLE_ROOT / "skills" / "handwerk"
    shutil.copytree(src, skills_root)

    # Initialise a git repo so the commit step has somewhere to land.
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "test@test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-q", "-m", "init"], check=True
    )

    monkeypatch.setattr(tool_remember_rule, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool_remember_rule, "SKILLS_ROOT", skills_root)
    return repo_root


@pytest.fixture
def tmp_ical(tmp_path, monkeypatch):
    """Provide a temporary ICS path, monkeypatched into tool_calendar."""
    import tool_calendar

    ics_path = tmp_path / "handwerk.ics"
    db_path = tmp_path / "handwerk.db"
    monkeypatch.setattr(tool_calendar, "ICS_PATH", ics_path)
    monkeypatch.setattr(tool_calendar, "DB_PATH", db_path)
    return ics_path
