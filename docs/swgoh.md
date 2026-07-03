# SWGOH roster dashboard — access your profile from anywhere

A zero-dependency dashboard for your Star Wars: Galaxy of Heroes roster, plus an
"Ask" box that answers strategy questions using the **Claude API** with your
roster as context — and, when you attach a **game screenshot**, Claude's vision
reads the image (an enemy defense, a mod, a character screen) and answers from
what you actually own. A PIN protects the page and the paid endpoint. Hosted
free on an **Oracle Always-Free VM** and refreshed by a daily timer — so you can
open it from your iPhone, MacBook, or any browser without Claude Code running on
your laptop.

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
browser or git. When a screenshot is attached it is sent as a base64 image
block alongside your roster; the roster block is marked cacheable so repeated
questions in a sitting cost far less.

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # get one at console.anthropic.com
export ASK_MODEL=claude-opus-4-8         # optional; best screenshot advice
export APP_PIN=1234                       # lock the page + Ask box
export AUTH_SECRET=long-random-string     # signs the login cookie
python3 ask_server.py                    # http://127.0.0.1:8787
```

Open <http://127.0.0.1:8787>, enter the PIN, then ask — attach or paste a
screenshot for "who beats this?" questions. Cost is pay-as-you-go per question
(a few cents on Opus). With `APP_PIN` unset the lock is off (local dev only).

**Auth model:** `POST /api/login` checks the PIN and sets a signed
(`AUTH_SECRET`-HMAC) `auth` cookie; both `GET /` and `POST /api/ask` require it.
The app only ever holds your own roster (`swgoh_data.js`), so there is no way to
look anyone else up. **Images** must be PNG/JPEG/GIF/WebP and ≤ 4 MB.

## Free, always-on deploy (Oracle Always-Free VM)

Set up the VM, a DNS subdomain, and Caddy, then use the units in `deploy/`:

1. **Serve it.** Install `deploy/swgoh-ask.service` (set `ANTHROPIC_API_KEY`,
   `APP_PIN`, and `AUTH_SECRET` in its `Environment=` lines), then
   `systemctl enable --now swgoh-ask`. It listens on `127.0.0.1:8787`.
2. **Front it with Caddy for HTTPS.** HTTPS is **required** — the login cookie is
   `Secure` and clipboard image-paste needs a secure context.
   - **If this VM already runs the `personal-cloud` Caddy** (the single container
     front door shared with swgoh-mod-planner + SWU): do **not** start a second
     Caddy — they would fight over ports 80/443. Add a hostname block to that
     Caddyfile that proxies to this host service. Because Caddy runs in a
     container, target the host gateway:
     ```
     swgoh.yourdomain.com {
         reverse_proxy host.docker.internal:8787
     }
     ```
     and give the caddy service `extra_hosts: ["host.docker.internal:host-gateway"]`.
     (Alternatively, dockerize `ask_server.py` as a compose service and proxy by
     service name.) **Confirm which Caddy is authoritative on the box before
     wiring this.**
   - **If there is no other Caddy**, use the standalone `deploy/Caddyfile`
     (`swgoh.yourdomain.com { reverse_proxy 127.0.0.1:8787 }`) with a host Caddy.
3. **Refresh daily.** Install `deploy/swgoh-fetch.service` + `swgoh-fetch.timer`
   (they run `deploy/run_fetch.sh`, which pulls the roster and commits
   `swgoh_data.js` only when it changed), then
   `systemctl enable --now swgoh-fetch.timer`.

Also open ports 80/443 in the Oracle security list.

**Hostname:** this stack uses **sslip.io** off the server's reserved IP
(129.146.69.139) — no registrar needed. The mod-planner already uses
`SWGOH_DOMAIN` (its value is in `personal-cloud/.env` on the box). The dashboard
needs its OWN label on the same IP, e.g. set `DASH_DOMAIN=swgoh-dash.129-146-69-139.sslip.io`
in the Caddy block. It resolves automatically and Caddy issues a matching cert.

Then `https://<DASH_DOMAIN>` works from any device — enter the PIN once and your
phone remembers it.

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
