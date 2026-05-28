# Video Storyboard — "Mein Geselle" (2:30 target)

Recording tool: macOS `Cmd+Shift+5` (screen record selection) for individual clips, stitched in iMovie.
Voice-over: English, no music, clear delivery. If voice is shaky, use subtitles.

---

## Scene 1 — The problem (0:00 – 0:20)

**Visual:** Stock-style still or short clip of a painter on a ladder, phone vibrating in pocket.
Or just text on black: "It's Tuesday, 10:42. Painter Schulz is on a ladder. His phone rings."
**VO:** "German tradespeople take 30 calls a day. They're on a ladder. They can't pick up. Leads die in voicemail. I built an AI co-worker that lives in Telegram, answers, schedules, quotes — and learns the tradesperson's style over time."

## Scene 2 — Voice memo → multi-tool plan (0:20 – 0:55)

**Visual:** Telegram chat window. Paul sends a German voice memo (or text):
*"Bitte draft mir ein Angebot für Frau Müller über Bad-Sanierung, 8 qm Fliesen neu, ungefähr 4500 Euro."*
Cut to terminal showing live agent.log: `customer_db completed → angebot_draft completed → response sent`.
Then back to Telegram showing Hermes' German reply with the quote summary.

**VO:** "One sentence in. Hermes plans, looks up Frau Müller in our SQLite CRM, drafts a German Angebot with 19% VAT, and replies in 17 seconds. Two tool calls, no hand-holding."

## Scene 3 — The learning loop (0:55 – 1:35) — THE MONEY SHOT

**Visual:** Telegram chat continues. Paul sends:
*"Bei Frau Müller gib immer 5% Skonto bei sofort-Zahlung. Merk dir das."*
Quick cut to dashboard at http://localhost:7070 — the `angebot-style` card now shows a "✨ Currently Evolving" pulse and a NEW dot on the commit timeline. Click the new dot, the diff appears: a new bullet under `## 📒 Learned Rules`.

**VO:** "When the user gives feedback, Hermes calls a custom tool — `remember_rule` — that turns the natural-language correction into a versioned, scoped skill edit, with a git commit. The dashboard reflects it live. Next conversation, Hermes already knows."

## Scene 4 — Cross-session recall (1:35 – 1:55)

**Visual:** New Telegram session next day. Paul writes:
*"Mach Frau Müller ein Angebot über die Küche, 12 qm. Du weißt ja Bescheid."*
Hermes' reply includes "wie besprochen: 5% Skonto bei sofort-Zahlung" — pulled from the rule we taught yesterday.

**VO:** "The next day. Same customer, different job. The 5% Skonto rule is already in the quote. That's the learning loop closing."

## Scene 5 — Architecture flash (1:55 – 2:15)

**Visual:** The ASCII pipeline diagram from the README, animated reveal of each tool box.

**VO:** "Five custom Hermes tools — customer_db, calendar, lead_classify, angebot_draft, remember_rule — wired alongside Hermes' 60+ built-ins. Skills live as markdown, evolve through git commits. Cross-session memory via Hermes' FTS5 session store, no extra wiring."

## Scene 6 — Outro (2:15 – 2:30)

**Visual:** README header on screen with GitHub URL.

**VO:** "Open-source under MIT. Repo link in the post. Built for the Hermes Agent Challenge by Paul, HTW Berlin."

---

## Cuts and overlays to add in post

- Subtitle the German messages with English translation in lower third.
- Lower-third pill on every cut: `customer_db`, `angebot_draft`, `remember_rule`, etc — naming the tool that just fired.
- Top-right pinned overlay: "Tool calls today: NN" (count up as the demo proceeds).

## Recording checklist

- [ ] Dashboard restarted with fresh state (commits visible from the demo runs)
- [ ] Telegram chat scrolled to start of conversation
- [ ] Browser zoomed so dashboard fits in frame at 1440 × 900
- [ ] System notifications muted (Do Not Disturb on)
- [ ] Terminal font size bumped to ≥ 16 pt for readability
- [ ] All German strings rendered with umlauts (sanity check `ä ö ü ß`)
