# Submission Gate Checklist

Run this top-to-bottom right before clicking "Publish" on dev.to.
Tick each box only after the check has actually been done.

## Repository

- [ ] `https://github.com/Paul1451/mein-geselle` is public and reachable in a private window
- [ ] `LICENSE` is MIT and contains current year (2026)
- [ ] `README.md` quickstart was executed on a fresh clone within the last 24h
- [ ] No `.env` file, no `*.db`, no `*.ics`, no `*.pdf` committed (`gitignore` enforced)
- [ ] No raw API keys or bot tokens anywhere (run `git grep -E 'sk-|gho_|AKIA|[0-9]{9,10}:AA'`)
- [ ] Repo description filled in on GitHub: "Voice-first Hermes Agent co-worker for German tradespeople — Hermes Agent Challenge 2026 submission"
- [ ] Repo topic tags set: `hermes-agent`, `agentic-ai`, `german`, `telegram-bot`, `learning-loop`
- [ ] `git tag v1.0-submission` created and pushed

## Submission post

- [ ] Word count of prose (between `## The Problem` and `## Open-Source Repo`) is in the 420–520 range
- [ ] Top line is the disclaimer: `*This is a submission for the [Hermes Agent Challenge](...), Build With Hermes Agent.*`
- [ ] Three required tags set in dev.to editor (NOT just inline): `hermesagentchallenge`, `devchallenge`, `agents`
- [ ] At least 5 screenshots embedded; all images load when previewing on dev.to
- [ ] Demo video link is public (unlisted YouTube) and plays from incognito
- [ ] All five custom tool names appear by name in the body (`customer_db`, `calendar`, `lead_classify`, `angebot_draft`, `remember_rule`)
- [ ] Three Hermes agentic capabilities named: planning / tool use / learning loop / cross-session memory / skills
- [ ] Repo link is clickable and matches the public URL above
- [ ] Read the entire post aloud once for awkward phrasing — fix anything that makes you cringe

## Code + runtime

- [ ] `hermes doctor` is clean on the laptop you'll demo from
- [ ] Dashboard at `http://localhost:7070` shows "Gateway: running" right now
- [ ] All 4 mein_geselle tools resolve to `mein_geselle` toolset (run the verification one-liner in README)
- [ ] At least one `remember_rule` call from a fresh Telegram session triggered a commit visible on the dashboard
- [ ] Demo video matches storyboard scenes 1–5

## Video

- [ ] Recording is 2:30 ± 30s
- [ ] Audio is loud enough without clipping (peak around -6 dBFS)
- [ ] Subtitles (or hard captions) included for German messages
- [ ] No system notifications, no sensitive tabs visible
- [ ] Uploaded as **unlisted**, share link copied to the post

## Final 30-second sanity check

- [ ] You opened the dev.to draft in incognito and the page renders correctly
- [ ] You searched dev.to for "hermes mein geselle handwerk" and confirmed no one else submitted the same idea
- [ ] Submission deadline (2026-05-31 23:59 PDT) confirmed in your local timezone — submit by 18:00 Berlin to keep buffer
