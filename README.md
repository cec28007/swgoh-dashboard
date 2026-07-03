# SWGOH Roster Dashboard

Access your **Star Wars: Galaxy of Heroes** roster — and ask an AI strategy
questions about it, **with a game screenshot for context** — from any device
(iPhone, MacBook, any browser). Hosted free on an Oracle Always-Free VM and
refreshed by a daily timer.

Ask a question, attach or paste a screenshot (an enemy defense, a mod, a
character screen), and **Gemini** answers using both the image and your actual
roster. Runs on Gemini's free API tier. Protected by a PIN so it isn't open on
the internet.

Zero dependencies — Python standard library only.

## Files

| File | Role |
|------|------|
| `fetch_swgoh.py` | Pulls your roster from the free **swgoh.gg** API → writes `swgoh_data.js`. Has a `mock` source for offline testing. |
| `swgoh.html` | The dashboard. KPIs, sortable/filterable roster, and an Ask box. |
| `ask_server.py` | Serves the page + `POST /api/ask`, which calls the Gemini API with your roster (and optional screenshot) as context. Your API key stays server-side. |
| `swgoh_data.js` | Auto-generated roster data. |
| `deploy/` | systemd units + Caddy config for the free always-on Oracle VM. |
| `docs/swgoh.md` | Full data-source rationale and deploy notes. |

## Quick start (local, on your Mac)

```bash
python3 fetch_swgoh.py mock --dry-run    # try the pipeline, no network
python3 fetch_swgoh.py swgoh             # pull YOUR real roster -> swgoh_data.js
```

Then turn on the Ask box (needs a Gemini API key from
<https://aistudio.google.com/app/apikey> — free tier, separate from any Gemini
subscription). Copy `.env.example` to `.env` for the full list of settings:

```bash
export GEMINI_API_KEY=AIza-your-key-here
export ASK_MODEL=gemini-2.5-flash        # optional; -pro for sharper answers
export APP_PIN=1234                       # optional locally; required in prod
export AUTH_SECRET=some-long-random-string
python3 ask_server.py                    # http://127.0.0.1:8787
```

Open <http://127.0.0.1:8787>, enter the PIN, and ask — attach a screenshot for
questions like *"who on my roster beats this defense?"* When `APP_PIN` is unset
the lock is disabled (local dev only).

Ally code resolution: `--ally 611121817`, else the `SWGOH_ALLY_CODE` env var,
else the `ally_code` already in `swgoh_data.js`, else the built-in default.

## Free, always-on deploy (Oracle Always-Free VM)

See [`docs/swgoh.md`](docs/swgoh.md) and the `deploy/` folder: run `ask_server.py`
as a systemd service (secrets in the unit's `Environment=`), have Caddy
reverse-proxy your subdomain to it, and add a daily timer that re-pulls the
roster. Result: `https://swgoh.yourdomain.com` works from any device.

> **Note:** if this VM already runs the `personal-cloud` Caddy front door, do
> **not** start a second Caddy — add a hostname block to that Caddyfile pointing
> at `ask_server.py` (port 8787) instead. Give it its own **sslip.io** label off
> the server IP (e.g. `swgoh-dash.129-146-69-139.sslip.io`), like the rest of the
> stack — no registrar needed. HTTPS is required for the Secure login cookie and
> clipboard image-paste. See `docs/swgoh.md`.

## Data source

- **swgoh.gg API** (current): free, no login, full roster.
- **swgoh-comlink** (upgrade path): self-hosted service for the freshest,
  most complete data straight from the official game API.
