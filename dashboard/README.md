# Mein Geselle — Workshop Console

Single-page dashboard that visually proves Hermes is learning over time.

- **Backend:** FastAPI + Jinja2 (server-side rendered)
- **Frontend:** HTMX for activity-feed auto-refresh + skill-diff expansion
- **Reads (all read-only):**
  - `mein-geselle/skills/handwerk/*/SKILL.md` (frontmatter + body)
  - git history of those files via `git log --follow`
  - `~/.hermes/data/handwerk.db` for customer/appointment/angebot counts
  - `~/.hermes/state.db` for the last 10 sessions/messages (falls back to tailing `~/.hermes/logs/agent.log`)
  - `~/.hermes/gateway_state.json` + `os.kill(pid, 0)` for gateway liveness
  - today's `tool ... completed` lines in `~/.hermes/logs/agent.log` for tool-call counts

## Install & run

```bash
cd /Users/paul/Desktop/hermes-challenge/hermes-agent
source .venv/bin/activate
uv pip install fastapi uvicorn jinja2 pyyaml

cd ../mein-geselle/dashboard
python app.py
```

Open <http://localhost:7070/>.

## Routes

| Route | Purpose |
| --- | --- |
| `GET /` | Main dashboard (3-column grid) |
| `GET /activity` | HTMX partial — activity feed, polled every 3s |
| `GET /skill/{slug}/diff/{sha}` | HTMX partial — unified diff vs HEAD |
| `GET /healthz` | Liveness probe |

## Layout

- **Left** — Skills as clipboard-style cards with commit-dot timeline; click a dot to expand the unified diff vs HEAD.
- **Middle** — Workshop floor: 2×2 tile counters (Customers / Appointments / Angebote / Tool-Calls Today) and the live message feed (auto-refresh).
- **Right** — Hand-rolled inline SVG line chart of total skill LOC over commit history, with Day 1 / Today / % delta summary, and a full commit log anchor.

Header shows gateway liveness pill (green dot = `gateway_state == running` AND pid alive).

## Notes

- All German strings (`angebot_style`, `Müller`, …) render via UTF-8.
- The SVG chart degrades gracefully when there's only one commit per skill: it draws a single baseline point. As more commits land, the line grows.
- If the `sessions` table in `state.db` is missing or has an unexpected schema, the activity feed falls back to parsing the last 10 inbound/response lines from `agent.log`.
