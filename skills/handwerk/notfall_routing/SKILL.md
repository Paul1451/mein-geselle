---
name: notfall-routing
description: "Emergency triage SOP for Handwerker incidents."
version: 0.1.2
author: paul
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [handwerk, notfall, emergency, triage, sop, mein-geselle]
    category: domain
    requires_toolsets: [mein_geselle]
---

# Notfall Routing Skill

Emergency triage Standard Operating Procedure (SOP). Hit this skill the
moment `customer_intake` returns `label = "notfall"`. The objective is
simple and non-negotiable: **a human call-back must happen within 15
minutes of the inbound message timestamp.**

## When to Use

- `customer_intake` labelled the message `notfall`.
- ANY of the trigger keywords below appear in the inbound text, even if
  earlier classification missed them (defence in depth):

  - `Wasserschaden`
  - `Rohrbruch`
  - `Stromausfall`
  - `Gasgeruch`
  - `Schimmel akut`
  - `Heizung aus` (+ winter months)
  - `Brand` / `Brandgeruch`
  - `Einbruch` (broken door / window)
  - `läuft aus`, `tropft durch`, `Decke nass`
  - explicit `dringend` / `sofort` / `Notfall`

  Match is case-insensitive and tolerates German declension
  (`Wasserschadens`, `Rohrbruches`, etc.).

## Prerequisites

- `customer_db` tool available — we will create / append an entry in
  the incident log via the `appointments` table with
  `status = "incident"`.
- A reachable phone number for Maler Schulz so the agent can prompt the
  human operator (the agent never auto-dials; it only alerts).

## Procedure

1. **Acknowledge within 60 seconds.** Auto-reply in German, calm tone:

   > "Wir haben Ihre Nachricht erhalten und melden uns innerhalb von
   > 15 Minuten telefonisch bei Ihnen zurück. Bitte halten Sie Ihr
   > Telefon bereit."

   Use the formal `Sie`. Never promise a specific arrival time at this
   stage — only the 15-minute call-back.

2. **Identify the caller.** Look up via
   `customer_db.get_customer(query=<phone-or-name>)`. If unknown,
   create a stub record with `upsert_customer` (name = sender display,
   phone = inbound number, `notes_md = "Notfall-Erstkontakt"`).

3. **Log the incident.** Insert an appointment row with:
   - `title`: `"NOTFALL: <one-line description>"`
   - `starts_at`: inbound timestamp (UTC, ISO-8601)
   - `status`: `"incident"`
   - `notes_md`: full original message text + matched keyword list

   This row is the source of truth for the 15-minute SLA timer.

4. **Alert Maler Schulz.** Push a high-priority notification (channel
   choice belongs to the host integration — Telegram, SMS, push). The
   payload MUST contain:
   - customer name (or "Unbekannt"),
   - phone number to call back,
   - one-line incident summary,
   - link / id of the incident row from step 3.

5. **Track SLA.** If no human acknowledgement is recorded against the
   incident row within 15 minutes, escalate: re-alert on every
   configured fallback channel until acknowledged. Do not give up.

6. **After the call-back happens**, update the incident row's
   `notes_md` with a one-line outcome and either:
   - set `status = "planned"` if a remediation appointment was booked,
   - set `status = "resolved"` if no on-site visit was needed.

## Hard Rules

- NEVER auto-promise an on-site arrival time. Only the 15-min call-back.
- NEVER drop the alert because the agent "thinks" the situation is
  minor. Triage is the human's decision after the call-back.
- ALWAYS preserve umlauts (ä ö ü ß) in stored text — both customer
  message and our reply.
- The SLA clock is wall-clock minutes, not "agent turns".

## Notes

- This is v0.1.0 — keyword list and channels are expected to grow with
  real-world incidents. New keywords should be added with a short
  justification in the version bump's commit message.

## 📒 Learned Rules

- 2026-05-28T14:14 [global] (emergency): Notfälle (Wasserschaden, Strom aus) sofort an Maler Schulz mobil eskalieren.  _(src: Bei akutem Wasserschaden direkt anrufen, nicht warten.)_
- 2026-05-28T14:30 [global] (emergency): Bei Wasserschäden immer zuerst den Notdienst-Klempner Frank Becker anrufen.  _(src: Ab jetzt: bei Wasserschäden immer zuerst den Notdienst-Klempner Frank Becker anrufen.)_
