---
name: customer-intake
description: "Classify inbound Handwerker messages and route them."
version: 0.1.1
author: paul
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [handwerk, intake, classification, routing, mein-geselle]
    category: domain
    requires_toolsets: [mein_geselle]
---

# Customer Intake Skill

First-touch triage for every inbound message Maler Schulz receives —
WhatsApp text, voice note, e-mail, missed-call follow-up. The goal is to
attach a single label to the message and hand it off to the correct
downstream skill. Nothing more.

## When to Use

- A new message lands in any inbound channel (voice transcript, SMS,
  WhatsApp, e-mail) and no label has been attached yet.
- The user explicitly asks: "Was will der Kunde?" or "Sortier mir das ein".
- Before any reply is drafted — labelling is always the first step.

Do NOT use this skill to *reply* to the customer. It only classifies and
routes; reply drafting lives in other skills (`angebot_style`,
`notfall_routing`, etc.).

## Prerequisites

- The `customer_db` tool is registered (toolset `mein_geselle`) so the
  intake step can look up an existing customer by name / phone.
- Inbound message available as plain text. If voice, transcribe first
  using Hermes' transcription tools.

## Procedure

1. **Read the message verbatim.** Keep umlauts (ä ö ü ß) and any
   greeting/closing — they carry tone signals.

2. **Identify the sender.** Call `customer_db` with
   `action=get_customer, query=<name-or-phone-from-header>`. If no match,
   note `sender_known=false` — the reply will need a more formal opener.

3. **Pick exactly one label** from this fixed set:

   | Label       | Trigger signals                                        |
   |-------------|--------------------------------------------------------|
   | `notfall`   | "Wasserschaden", "Rohrbruch", "Stromausfall", "sofort",|
   |             | "dringend", "läuft aus", time pressure < 24 h          |
   | `anfrage`   | First contact, "Angebot", "Kostenvoranschlag",         |
   |             | "Würden Sie...", scope / pricing question              |
   | `follow_up` | References a known appointment / Angebot / running job |
   | `smalltalk` | Pure social chatter, holiday greetings, thank-you      |

   Tie-breakers (apply in order):
   - Any `notfall` keyword wins, regardless of other labels.
   - If both `anfrage` and `follow_up` apply, choose `follow_up` when
     the message refers to an existing `customer_id` in the DB.
   - `smalltalk` only when none of the others fit.

4. **Route.** Emit a single structured handoff:

   ```json
   {
     "label": "anfrage",
     "customer_id": 7,
     "sender_known": true,
     "summary_de": "Frau Rossi fragt nach Termin für Esszimmer im Juli.",
     "next_skill": "angebot_style"
   }
   ```

   Routing table:
   - `notfall`   → `notfall_routing`
   - `anfrage`   → `angebot_style`
   - `follow_up` → (no dedicated skill yet; draft polite status update)
   - `smalltalk` → (no dedicated skill yet; one-line friendly reply)

5. **Log.** Append a one-line entry to the customer's `notes_md` via
   `customer_db.upsert_customer` (or via the appointments table when the
   message is tied to a specific job). Format:
   `YYYY-MM-DD inbound/<label>: <one-sentence summary>`.

## Notes

- The skill MUST stay deterministic: same input → same label. Do not let
  small-talk politeness leak into a `notfall` message ("Hallo lieber
  Herr Schulz, mein Rohr ist gebrochen" is still `notfall`).
- Version 0.1.0 — labels and routing table are expected to evolve as
  Maler Schulz reviews mis-classifications.

## 📒 Learned Rules

- 2026-05-28T14:14 [global] (scheduling): Termine nie vor 08:30 Uhr vereinbaren — Schulz fährt vorher Material.
