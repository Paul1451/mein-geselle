---
name: angebot-style
description: "Style rules for German Handwerker quotes (Angebote)."
version: 0.1.1
author: paul
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [handwerk, angebot, quote, style, tone, mein-geselle]
    category: domain
    requires_toolsets: [mein_geselle]
---

# Angebot Style Skill

INITIAL — to be evolved.

This skill captures the *house style* for every Angebot (quote) Maler
Schulz sends to clients. It is intentionally a stub: each slot below will
be filled in over time as we observe how the real Maler Schulz greets
customers, displays prices, handles discounts, and signs off. Until then
the skill carries safe defaults so the system can still emit a usable
draft.

## When to Use

- After `customer_intake` labels a message as `anfrage` or when an
  existing `follow_up` lands that requires updating an Angebot.
- Whenever the agent needs to draft an Angebot text or PDF body.
- Whenever the agent needs to render a reply that *references* an
  Angebot's price, scope, or status.

## Prerequisites

- `customer_db` tool available (the Angebot will be stored via
  `log_angebot` once accepted).
- For PDF output, a downstream renderer skill (not in scope here).

## Procedure (default until slots are filled)

1. Pull the customer record. Use the formal Anrede that matches the
   `name` field ("Sehr geehrte Frau ...", "Sehr geehrter Herr ...",
   "Sehr geehrte Familie ...").
2. Render the scope as a bulleted list — one line per work item.
3. Apply the VAT and discount rules from the slots below.
4. Close with the signature slot.
5. Save the Angebot via `customer_db.log_angebot` with `status="draft"`.

## Style Slots (to be evolved)

These slots are explicitly placeholders. Each will be replaced by a
concrete rule once we have real-world feedback from Maler Schulz.

### Slot: greeting style
- Default: `"Sehr geehrte/r Frau/Herr <Nachname>,"` on first contact;
  `"Hallo Frau/Herr <Nachname>,"` for repeat clients after ≥ 1 prior
  Angebot.
- TODO: confirm whether Schulz prefers `"Guten Tag"` over
  `"Sehr geehrte/r"` even on first contact.

### Slot: VAT display
- Default: line items net (`EUR`), totals net + 19 % MwSt. + brutto.
- TODO: confirm whether Schulz is Kleinunternehmer (§ 19 UStG) — if so
  suppress MwSt. line and add the § 19 disclaimer instead.

### Slot: discount thresholds
- Default: no automatic discount.
- TODO: capture rules such as "5 % Skonto bei Zahlung binnen 8 Tagen"
  or "Stammkundenrabatt 3 % ab dem 3. Auftrag".

### Slot: signature
- Default block:

  ```
  Mit freundlichen Grüßen
  Maler Schulz
  Malerbetrieb Schulz, Berlin
  Tel: <telefon> · E-Mail: <email>
  ```
- TODO: confirm exact wording, add Steuernummer / USt-IdNr. if required.

### Slot: tone
- Default: höflich, kurz, sachlich. Keine Anglizismen ("Angebot" statt
  "Quote", "Termin" statt "Slot").
- TODO: capture preferred level of formality per customer segment.

## Notes

- Keep every German string with correct umlauts (ä ö ü ß) — never strip
  to ASCII.
- This skill MUST NOT send anything. It only drafts. A separate skill
  (or human approval step) handles delivery.
- Version 0.1.0 — the slots above are tracked so we can diff style
  evolution between versions.

## 📒 Learned Rules

- 2026-05-28T14:14 [customer:Müller] (quoting): Always offer 5% Skonto for immediate payment.  _(src: Frau Müller zahlt gern sofort — merk dir das.)_
