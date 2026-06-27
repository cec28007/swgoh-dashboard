# SWGOH Roster Dashboard

Access your **Star Wars: Galaxy of Heroes** roster — and ask Claude strategy
questions about it — from any device (iPhone, MacBook, any browser), without
Claude Code running on your laptop. Hosted free on an Oracle Always-Free VM and
refreshed by a daily timer.

Zero dependencies — Python standard library only.

## Files

| File | Role |
|------|------|
| `fetch_swgoh.py` | Pulls your roster from the free **swgoh.gg** API → writes `swgoh_data.js`. Has a `mock` source for offline testing. |
| `swgoh.html` | The dashboard. KPIs, sortable/filterable roster, and an Ask box. |
| `ask_server.py` | Serves the page + `POST /api/ask`, which calls the Claude API with your roster as context. Your API key stays server-side. |
| `swgoh_data.js` | Auto-generated roster data. |
| `deploy/` | systemd units + Caddy config for the free always-on Oracle VM. |
| `docs/swgoh.md` | Full data-source rationale and deploy notes. |

## Quick start (local, on your Mac)

```bash
python3 fetch_swgoh.py mock --dry-run    # try the pipeline, no network
python3 fetch_swgoh.py swgoh             # pull YOUR real roster -> swgoh_data.js
```

Then turn on the Ask box (needs an Anthropic API key from
<https://console.anthropic.com> — pay-as-you-go, a fraction of a cent per
question; this is separate from a Claude subscription):

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
export ASK_MODEL=claude-sonnet-4-6       # optional
python3 ask_server.py                    # http://127.0.0.1:8787
```

Open <http://127.0.0.1:8787> and ask, e.g. *"What should I farm next for a Sith
team?"*

Ally code resolution: `--ally 611121817`, else the `SWGOH_ALLY_CODE` env var,
else the `ally_code` already in `swgoh_data.js`, else the built-in default.

## Free, always-on deploy (Oracle Always-Free VM)

See [`docs/swgoh.md`](docs/swgoh.md) and the `deploy/` folder: run `ask_server.py`
as a systemd service (API key in the unit's `Environment=`), have Caddy
reverse-proxy your subdomain to it, and add a daily timer that re-pulls the
roster. Result: `https://swgoh.yourdomain.com` works from any device.

## Data source

- **swgoh.gg API** (current): free, no login, full roster.
- **swgoh-comlink** (upgrade path): self-hosted service for the freshest,
  most complete data straight from the official game API.
