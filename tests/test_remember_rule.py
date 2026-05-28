"""Tests for tool_remember_rule — append + bump semver + git commit."""
from __future__ import annotations

import json
import re
import subprocess

import tool_remember_rule as rr


def _read_skill(repo_root, dir_name):
    return (repo_root / "skills" / "handwerk" / dir_name / "SKILL.md").read_text(encoding="utf-8")


def test_quoting_appends_to_angebot_style(tmp_skills_root):
    out = json.loads(rr.remember_rule_tool({
        "rule": "Always use matte finish.", "category": "quoting",
    }))
    assert out["ok"] is True
    assert "angebot_style/SKILL.md" in out["skill_file"]
    body = _read_skill(tmp_skills_root, "angebot_style")
    assert "Always use matte finish." in body


def test_consecutive_calls_bump_patch(tmp_skills_root):
    before = _read_skill(tmp_skills_root, "angebot_style")
    base = re.search(r"^version:\s*(\d+)\.(\d+)\.(\d+)", before, re.MULTILINE)
    maj, minor, patch = int(base.group(1)), int(base.group(2)), int(base.group(3))
    for i in range(1, 4):
        rr.remember_rule_tool({"rule": f"r{i}", "category": "quoting"})
    after = _read_skill(tmp_skills_root, "angebot_style")
    m = re.search(r"^version:\s*(\d+)\.(\d+)\.(\d+)", after, re.MULTILINE)
    assert (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (maj, minor, patch + 3)


def test_git_commit_created(tmp_skills_root):
    out = json.loads(rr.remember_rule_tool({
        "rule": "Commit this rule.", "category": "quoting",
    }))
    assert out["git_commit"] is not None
    log = subprocess.run(
        ["git", "-C", str(tmp_skills_root), "log", "--oneline", "-1"],
        capture_output=True, text=True,
    )
    assert "Commit this rule" in log.stdout


def test_customer_scope_in_bullet(tmp_skills_root):
    rr.remember_rule_tool({
        "rule": "Loves Skonto.", "category": "quoting",
        "scope": "customer:Müller",
    })
    body = _read_skill(tmp_skills_root, "angebot_style")
    assert "[customer:Müller]" in body


def test_empty_rule_returns_error(tmp_skills_root):
    out = json.loads(rr.remember_rule_tool({"rule": "  ", "category": "quoting"}))
    assert out["ok"] is False
    assert "rule" in out["error"].lower()


def test_umlauts_and_emoji_survive(tmp_skills_root):
    rule = "Familie Yıldırım mag Müller-style 🎨 finish"
    rr.remember_rule_tool({"rule": rule, "category": "quoting"})
    body = _read_skill(tmp_skills_root, "angebot_style")
    assert "Yıldırım" in body and "🎨" in body and "Müller" in body
