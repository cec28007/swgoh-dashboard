# SWGOH roster dashboard — access your profile from anywhere

A zero-dependency dashboard for your Star Wars: Galaxy of Heroes roster, plus an
"Ask" box that answers strategy questions using the **Claude API** with your
roster as context. Hosted free on an **Oracle Always-Free VM** and refreshed by
a daily timer — so you can open it from your iPhone, MacBook, or any browser
without Claude Code running on your laptop.

This is the same architecture as the Tesla dashboard in this repo
(`fetch_tesla.py` -> `data.js` -> static page on the Oracle VM), with two
additions: SWGOH as the data source, and a small Claude-backed Q&A endpoint.

## The pieces

| File | Role |
|------|------|
| `fetch_swgoh.py` | Pulls your roster from the free **swgoh.gg** API -> writes `swgoh_data.js`. |
| `swgoh.html` | The dashboard. Reads `swgoh_data.js`. KPIs, sortable/filterable roster, Ask box. |
| `ask_server.py` | Serves the page + a `POST /api/ask` endpoint that calls Claude. Key stays server-side. |
| `swgoh_data.js` | Auto-generated roster data (committed so the page works as a static file too). |

## Quick start (local)

```bash
python3 fetch_swgoh.py mock --dry-run    # try the pipeline, no network
python3 fetch_swgoh.py swgoh             # real pull -> swgoh_data.js (needs network)
open swgoh.html                          # view it (Ask box needs the server, below)
```

Ally code resolution: `--ally 611121817`, else `SWGOH_ALLY_CODE` env var, else the
`ally_code` already in `swgoh_data.js`, else the built-in default.

## Turn on the Ask box

The Ask box posts to `POST /api/ask`, which `ask_server.py` answers by calling
Claude. Your API key lives **only** in the server environment — never in the
browser or git.

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # get one at console.anthropic.com
export ASK_MODEL=claude-sonnet-4-6       # optional; cheaper/faster default
python3 ask_server.py                    # http://127.0.0.1:8787
```

Open <http://127.0.0.1:8787> and ask, e.g. "What should I farm next for a Sith
team?" Cost is pay-as-you-go per question (typically a fraction of a cent).

## Free, always-on deploy (Oracle Always-Free VM)

Set up the VM, a DNS subdomain, and Caddy, then use the units in `deploy/`:

1. **Serve it.** Install `deploy/swgoh-ask.service` (set `ANTHROPIC_API_KEY` in
   its `Environment=` line), then `systemctl enable --now swgoh-ask`. Point Caddy
   at it with `deploy/Caddyfile`:
   ```
   swgoh.yourdomain.com {
       reverse_proxy 127.0.0.1:8787
   }
   ```
2. **Refresh daily.** Install `deploy/swgoh-fetch.service` + `swgoh-fetch.timer`
   (they run `deploy/run_fetch.sh`, which pulls the roster and commits
   `swgoh_data.js` only when it changed), then
   `systemctl enable --now swgoh-fetch.timer`.

That's it — `https://swgoh.yourdomain.com` works from any device, no laptop
needed.

## Data source notes

- **swgoh.gg API** (current): free, no login, returns your full roster
  (characters + ships, stars, gear/relic, GP, zetas/omicrons). It's swgoh.gg's
  cached copy of your public profile, so it reflects your last in-game sync.
- **swgoh-comlink** (upgrade path): a self-hosted service that talks to the
  official game API for the freshest/most complete data. If you outgrow
  swgoh.gg, add a `comlink` source to `fetch_swgoh.py` — the dashboard doesn't
  change.

## Later: Oracle Autonomous Database

`swgoh_data.js` is file-based, which keeps this simple. If you later want
queryable history (track GP/relics over months), point `fetch_swgoh.py` at an
Oracle Autonomous DB instead of a flat file — additive, not a rewrite.
