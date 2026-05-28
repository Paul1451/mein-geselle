# 7-Day Demo Script — "Mein Geselle"

Drives the agent through a realistic week in the life of Maler Schulz (Berlin painter).
Each message exercises one or more custom tools and the learning loop.
Run via:

```bash
hermes chat -q "<message>" -Q -t hermes-cli,mein_geselle --continue mein_geselle_demo
```

| # | Day | User message (German) | Expected tools | Expected outcome |
|---|---|---|---|---|
| 1 | Mo | "Hat Frau Müller schon mal angerufen? Ich brauch ihre Telefonnummer." | customer_db | Returns customer record + phone |
| 2 | Mo | "Bitte draft mir ein Angebot für Frau Müller über Bad-Sanierung, 8 qm Fliesen neu, ungefähr 4500 Euro netto." | customer_db + angebot_draft | New Angebot in DB, markdown draft returned |
| 3 | Mo | "Bei Frau Müller gib immer 5% Skonto bei sofort-Zahlung. Merk dir das für künftige Angebote." | remember_rule | Patches angebot_style/SKILL.md, git commit |
| 4 | Di | "Notfall! Familie Yıldırım hat Wasserschaden in der Küche. Was machen wir?" | lead_classify + customer_db | Classifies as notfall urgency=5, suggests immediate callback |
| 5 | Di | "Ab jetzt: bei Wasserschäden immer zuerst den Notdienst-Klempner Frank Becker anrufen. Merk dir das." | remember_rule | Patches notfall_routing/SKILL.md, git commit |
| 6 | Mi | "Welche Termine hat Herr Becker diese Woche?" | customer_db | Lists upcoming appointments for Becker |
| 7 | Do | "Bitte buch Frau Kowalski für Freitag 14 Uhr für Wohnzimmer streichen, 25 qm." | calendar + customer_db | Books slot, no conflict, returns event UID |
| 8 | Fr | "Termine vor 8:30 will ich nie wieder, ich fahr zur Baustelle. Merk dir das für die Zukunft." | remember_rule | Patches customer_intake/SKILL.md, git commit |

After all 8 runs, the dashboard at `http://localhost:7070` will show:
- 3 new commits on the skill timeline (angebot-style, notfall-routing, customer-intake)
- "Currently Evolving" pulse on at least one skill
- Activity feed with the 8 inbound messages
- SVG growth chart spiking visibly
