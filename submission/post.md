*This is a submission for the [Hermes Agent Challenge](https://dev.to/challenges/hermes-agent-2026-05-15), Build With Hermes Agent.*

> Scope notes: the demo quote is 8 qm bath wall area at ~4500 EUR net (before 19% MwSt). WeasyPrint PDF rendering has a known macOS Pango arm64/Rosetta issue documented in the README (markdown drafts always work; PDF works on aarch64 Python). The WhatsApp adapter was scoped out — Telegram covers everything in this demo.

## The Problem Every Tradesperson Knows

It's Tuesday, 10:42. Painter Schulz is on a 4-metre ladder in a Berlin Altbau, roller in one hand. His phone vibrates in the dropcloth pocket. Voicemail: "Hallo, mein Bad..." He can climb down — and lose 20 minutes of cutting in — or ignore it and lose the customer. Schulz takes 30+ calls a day. He has no back office, no receptionist, no CRM. Leads die in voicemail. Quotes get written at 22:00 on the kitchen table. So I built him an AI co-worker.

## What I Built

**Mein Geselle** ("My Apprentice") is a Hermes-Agent-powered Telegram bot that runs a tradesperson's customer back-office from a voice memo. Multi-tool, voice-first, and — the part judges should care about — its skills evolve from real conversations. Schulz talks; the bot remembers, drafts, schedules, and gets better.

```
Telegram voice/text ──► Hermes Gateway (STT)
       │
       ▼
┌─────────────────────────────────────────┐
│   Hermes Agent Loop (planning + tools)  │
└─────────────────────────────────────────┘
  │       │        │        │       │       │
  ▼       ▼        ▼        ▼       ▼       ▼
customer  calendar lead_   angebot  remember skill_
  _db              classify _draft   _rule   manage
  │       │        │        │       │       │
  ▼       ▼        ▼        ▼       ▼       ▼
 SQLite  iCal     route    PDF +   appends  versions
 (CRM)   (RFC5545) inbox    DB      Learned  & evolves
                                    Rules
```

## Tech Stack

1. Hermes Agent v0.14 (Foundation Release) — agent loop, gateway, skill system, FTS5 session memory
2. Python 3.13 + uv venv
3. SQLite + FTS5 for both customer CRM and Hermes' cross-session recall
4. Telegram Bot Gateway (built-in Hermes adapter) with Whisper STT
5. WeasyPrint + Jinja2 for German Angebot PDFs (19% MwSt, discount rules)
6. FastAPI + HTMX for the "Workshop Console" dashboard
7. DeepSeek v4 Pro via OpenRouter as the planner (Hermes is model-agnostic; this is just what I happen to use)

## How I Used Hermes Agent

**Multi-step planning.** A 10-second voice memo — "Bitte draft mir ein Angebot für Frau Müller, das Bad das wir letzte Woche besprochen haben" — becomes a three-tool plan: `customer_db` lookup, `angebot_draft` (8 qm walls, 4500 EUR net), then the response. One user message, three tool turns, no hand-holding.

**Tool ecosystem.** Five custom tools sit alongside Hermes' 60+ built-ins, registered via `registry.register()` in `tools/` under the toolset `mein_geselle`: `customer_db`, `calendar`, `lead_classify`, `angebot_draft`, and `remember_rule`. Average tool latency is 0.14 s.

**The learning loop — the part I'm proudest of.** When Schulz says "Bei Frau Müller immer 5% Skonto", Hermes' built-in `skill_manage` *could* edit the relevant skill — but lighter planners don't always reach for it on a casual correction. So I wrote `remember_rule`, a thin opinionated wrapper that turns natural-language corrections into versioned skill edits with a git commit. Now even DeepSeek triggers the loop on phrases like "merk dir das". Each call bumps the skill's semver, appends a dated bullet to a `## 📒 Learned Rules` section, and commits to git — so the evolution is versioned AND visible on the dashboard timeline.

**Cross-session memory.** Hermes' FTS5 session store plus the built-in `session_search` tool lets the agent recall "Frau Müller asked about Mahnung last week" without me wiring anything custom.

**Visible evolution.** The dashboard ships a hand-rolled SVG chart of total skill-LOC per commit. Day 1 baseline vs today is a visible diff — the user sees their apprentice grow up.

## Demo

- `screenshot_1_telegram_lookup.png` — Paul asks about Frau Müller in Telegram; Hermes returns her details and last job.
- `screenshot_2_telegram_skonto.png` — Paul says "5% Skonto for Frau Müller"; Hermes acks and bumps the skill.
- `screenshot_3_dashboard_overview.png` — full Workshop Console with skill cards and recent calls.
- `screenshot_4_skill_diff.png` — clicked-into diff showing the rule appended under Learned Rules.
- `screenshot_5_skill_timeline.png` — the timeline strip after a week of evolution.

Video walkthrough: [unlisted YouTube link — paste once recorded]

## Open-Source Repo

- GitHub: https://github.com/Paul1451/mein-geselle
- License: MIT
- Setup: clone, `hermes setup`, symlink tools, `python mein-geselle/tools/seed.py`, `hermes gateway run`

Built by Paul Klopsch (@Paul1451), HTW Berlin.

`#hermesagentchallenge`, `#devchallenge`, `#agents`
