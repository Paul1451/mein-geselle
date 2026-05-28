# Recording Playbook — Mein Geselle Demo Video

**Setup confirmed:** English VO + German subtitles · Paul = Schulz on his own Telegram · Live rule = Herr Becker Dienstag Vormittag.

Record each clip separately with `Cmd+Shift+5 → Auswahl aufzeichnen`. Aim for the listed length per clip. Stitch in iMovie.

---

## Clip 0 — Hold reel (pre-roll, optional, 5 s)

Just a still showing the README header or `submission/screenshots/dashboard_final.png` full-screen for 5 seconds. Useful as an opening freeze frame in iMovie.

---

## Clip 1 — The problem (0:20)

**On screen:** Black background + the text line below in white (use a Keynote slide or just a fullscreen browser tab with the text). No recording inside this clip — it can be a static image you film with screen-record selection just to keep frame rates consistent.

**Title text:**
> It's Tuesday, 10:42.
> Painter Schulz is on a 4-metre ladder.
> His phone rings.

**Voiceover (English, ~28 words):**
> *German tradespeople take thirty calls a day. They're on a ladder. They can't pick up. Leads die in voicemail. I built an AI co-worker that lives in Telegram, answers, schedules, quotes — and learns the tradesperson's style over time.*

---

## Clip 2 — Multi-tool plan (0:35)

**On screen layout:** Telegram on the left half, the dashboard `http://localhost:7070` on the right half. (Use macOS split view or arrange windows side-by-side at ~720 px each.)

**You type into Telegram (record this typed live):**
> Bitte draft mir ein Angebot für Frau Müller über Bad-Sanierung, 8 qm Fliesen neu, ungefähr 4500 Euro netto.

Wait for Hermes' reply. Once the reply lands, pan the screen recording briefly to the dashboard's "Workshop Floor" middle column — the new message should appear in the activity feed.

**Voiceover (~50 words):**
> *Watch this. One typed sentence — German, casual, ambiguous. Hermes plans without prompting: looks Frau Müller up in our SQLite customer DB, drafts a German Angebot with 19% VAT and the line items, and replies in seventeen seconds. Two tool calls, three API turns, no hand-holding from me.*

**Subtitle for the Telegram message (lower third):** *"Draft a quote for Frau Müller — bathroom renovation, 8 sqm tiles, around 4,500 euros net."*

---

## Clip 3 — The learning loop (0:40) ⭐ Money shot

**On screen layout:** Telegram on the left, dashboard on the right (same as Clip 2). KEEP the dashboard's `angebot-style` skill card visible — it's about to update live.

**You type into Telegram:**
> Bei Herr Becker immer Dienstag Vormittag anrufen, dann ist er am besten erreichbar. Merk dir das.

Watch the dashboard. Within ~20 seconds you should see:
1. The activity feed picks up your inbound message
2. The `customer-intake` skill card gets a *Currently Evolving* pulse
3. A NEW dot appears at the right end of the customer-intake card's commit timeline
4. The Skill Growth chart on the right ticks up by a few lines

Once the new dot appears, click it. The HTMX diff loads inline showing the bullet that was just appended:
```
- 2026-05-28T<time> [customer:Becker] (intake): Herr Becker am besten Dienstag Vormittag erreichbar.
```

**Voiceover (~55 words):**
> *Now the part I'm proudest of. When Schulz gives feedback — "Bei Herr Becker immer Dienstag Vormittag" — Hermes calls a custom tool I wrote, `remember_rule`. It turns natural-language corrections into versioned skill edits. The bullet lands in the right skill file, the semver bumps, a git commit happens, and the dashboard shows it live. The agent literally just learned.*

**Subtitle for the Telegram message:** *"Always call Mr Becker on Tuesday mornings — he's most reachable then. Remember that."*

**If the rule doesn't fire the first time:** rephrase to *"Bitte patche das customer-intake Skill: bei Herrn Becker dienstags vormittags anrufen, da am besten erreichbar"* — more explicit.

---

## Clip 4 — Cross-session recall (0:20)

**On screen:** Telegram only (full screen, no dashboard split).

**You type:**
> Hat Frau Müller schon mal angerufen?

Wait for the reply. Hermes should return her customer record AND include a line like `Zahlung: 5% Skonto bei Sofort-Zahlung` — which was taught in a previous conversation, not this one.

**Voiceover (~30 words):**
> *Different session, days later. Schulz asks about Frau Müller. The reply includes a payment-terms line that was taught weeks ago — surfaced by Hermes' built-in FTS5 session memory. No custom wiring from me.*

**Highlight tip:** in iMovie add a red highlight box around the `Zahlung: 5% Skonto` line.

---

## Clip 5 — Architecture flash (0:20)

**On screen:** The README open in your browser at https://github.com/Paul1451/mein-geselle, scrolled to the ASCII pipeline diagram.

Or alternatively, open `mein-geselle/README.md` in Markdown preview.

Slowly pan/zoom across the pipeline diagram while you read the VO.

**Voiceover (~40 words):**
> *Five custom Hermes tools — customer DB, calendar, lead classifier, Angebot drafter, and remember_rule — wired alongside Hermes' sixty plus built-ins under one toolset. Skills live as markdown, evolve through git commits. Cross-session memory via Hermes' FTS5 store. No extra glue code on my side.*

---

## Clip 6 — Outro (0:15)

**On screen:** The github.com/Paul1451/mein-geselle repo page, scrolled to the header so the description is visible.

**Voiceover (~25 words):**
> *MIT licensed, repo link in the post. Built for the Hermes Agent Challenge by Paul, HTW Berlin. Thanks for watching.*

---

## After recording — iMovie checklist

- [ ] Drop all 6 clips on the timeline in order
- [ ] Add the subtitle lower-thirds for German messages (Title style: lower third, Helvetica, white, semi-transparent dark background)
- [ ] Add a tool-name pill in the upper left for each clip — text only:
  - Clip 2: `tool: customer_db → angebot_draft`
  - Clip 3: `tool: remember_rule`
  - Clip 4: `tool: customer_db (cross-session memory)`
- [ ] Add a 0.5 s fade between clips
- [ ] No music (judges scan many submissions on mute — keep the focus on the VO)
- [ ] Export at 1080p, MP4, share to YouTube as **Unlisted**
- [ ] Copy the unlisted URL into `submission/post.md` (replace the `[unlisted YouTube link — paste once recorded]` line)
- [ ] Commit + push the updated post

---

## VO total length check

- Clip 1: 0:20
- Clip 2: 0:35
- Clip 3: 0:40
- Clip 4: 0:20
- Clip 5: 0:20
- Clip 6: 0:15
- **Total: 2:30** ✓

---

## Voiceover style notes

- **Pace:** slightly faster than conversational. Each clip's VO has a target word count that fits the time budget at ~120 wpm.
- **Tone:** practical, not salesy. Don't oversell. Let the demo carry the proof.
- **Common pitfalls:** rising intonation at clause ends ("right?") — avoid. Filler words "uhm" / "like" — avoid. Reading-monotone — avoid; mark the key words to stress.
- **Take 2-3 of each clip** if you have time. Pick the best in iMovie.
