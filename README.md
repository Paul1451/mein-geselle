# Mein Geselle

> A voice-first [Hermes Agent](https://github.com/NousResearch/hermes-agent) co-worker for German tradespeople. Telegram voice in → Hermes plans, looks up customers, drafts quotes, books appointments, replies → side-effects in calendar, CRM and PDFs out.

Built as a submission for the [Hermes Agent Challenge 2026](https://dev.to/challenges/hermes-agent-2026-05-15) (Nous Research × DEV).

## Why this exists

Tradespeople (Maler, Installateure, Elektriker) take 30+ customer calls a day. They are usually on a ladder, in a crawl space, or holding a torque wrench — none of which mix well with typing a quote. Inbound leads die because nobody picks up. *Mein Geselle* is a Hermes-powered back-office that listens to a 10-second voice memo and does the rest.

## What it does

- **Voice-first intake** via Telegram (Whisper STT)
- **Inbound triage:** classifies messages as `notfall`, `anfrage`, `follow_up`, or `smalltalk` with an urgency score
- **Customer recall** via local SQLite (`customers`, `appointments`, `angebote`) with fuzzy name + phone match
- **Appointment booking** against a local iCal calendar with conflict detection
- **Quote drafting** with German VAT (19%), discount thresholds, and PDF export (WeasyPrint)
- **Skills that evolve:** Hermes' learning loop captures the tradesperson's style (greeting, discounting habits, signature) into `skills/handwerk/angebot_style/SKILL.md` over weeks of use

## Architecture

```
Tradesperson (Telegram voice/text)
        │
        ▼
Hermes Gateway ──► STT (Whisper) ──► Hermes Agent Loop
                                          │
        ┌──────────────────┬──────────────┼──────────────────┬─────────────┐
        ▼                  ▼              ▼                  ▼             ▼
  customer_db         calendar      lead_classify      angebot_draft   skills/
   (SQLite)        (iCal+SQLite)     (deterministic)   (PDF+Jinja)    (auto-
                                                                      evolving)
        │
        ▼
   Side effects: Telegram reply · iCal event · Angebot-PDF · CRM row
```

## Custom tools (all in `tools/`)

| Tool | Purpose |
|---|---|
| `tool_customer_db.py` | CRUD over local customer/appointment/Angebot SQLite |
| `tool_calendar.py` | Free-slot search + booking against `~/.hermes/data/handwerk.ics` (RFC-5545) |
| `tool_lead_classify.py` | Deterministic keyword classifier for inbound messages |
| `tool_angebot_draft.py` | German Angebot drafter with line items, 19% VAT, discount rules, PDF export |

Each follows Hermes' `registry.register()` pattern under the `mein_geselle` toolset.

## Skills (in `skills/handwerk/`)

| Skill | Initial state | Evolves via |
|---|---|---|
| `customer_intake` | Hand-seeded classification SOP | Corrected classifications |
| `angebot_style` | Empty stub | Tradesperson edits to drafts |
| `notfall_routing` | Hand-seeded keyword + escalation rules | Real emergency cases |

## Quickstart

Prereqs: macOS or Linux, Python 3.13, `uv`, a Telegram bot from [@BotFather](https://t.me/BotFather), an [OpenRouter](https://openrouter.ai) (or Anthropic/Nous Portal) API key.

```bash
# 1. Install Hermes Agent
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# 2. Clone Mein Geselle next to it
git clone https://github.com/Paul1451/mein-geselle.git

# 3. Link tools + skills into Hermes
for f in mein-geselle/tools/tool_*.py; do
  ln -sfn "$(pwd)/$f" "hermes-agent/tools/mg_$(basename $f)"
done
ln -sfn "$(pwd)/mein-geselle/skills/handwerk" hermes-agent/skills/handwerk

# 4. Configure
hermes setup           # model + provider
hermes setup gateway   # Telegram

# 5. Seed demo data + run
python mein-geselle/tools/seed.py
hermes gateway run
```

Send a voice memo or text to your bot and watch the agent plan.

## Demo

[Embed video — added after submission day]

## License

MIT. See [LICENSE](LICENSE).

Built by Paul Klopsch ([@Paul1451](https://github.com/Paul1451)), HTW Berlin.
