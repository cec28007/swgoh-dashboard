# Design — Screenshot-aware "Ask" box (phone-first) + daily tips digest

**Status:** Approved for spec review
**Date:** 2026-07-03
**Repo:** `swgoh-dashboard` (cec28007/swgoh-dashboard)
**Ally code:** 611121817

---

## Why this repo (and not swgoh-mod-planner)

There are two SWGOH apps in the personal portfolio:

- **`swgoh-mod-planner`** — the large FastAPI + React mod optimizer (26 pages, comlink
  data, multi-tenant). Deploys via the `personal-cloud` compose stack. No Ask box.
- **`swgoh-dashboard`** — this repo. A zero-dependency, stdlib-only "roster + **Ask
  Claude**" tool: `ask_server.py` (serves the page + `POST /api/ask` → Claude API),
  `swgoh.html` (already responsive), roster from **swgoh.gg** into `swgoh_data.js`,
  refreshed daily. Already has a working (text-only) Ask box and an Oracle deploy path.

The user's request — "reach it from my phone and share game screenshots with it" — is
**80% already built here.** Building it into the mod-planner would reinvent the Ask box,
the mobile UI, and the deploy. So the feature lands in `swgoh-dashboard`.

**Hard constraint carried forward:** this app is deliberately **zero-dependency,
Python-standard-library only.** No new packages (no `anthropic` SDK, no web framework).
The Anthropic call stays raw `urllib`. Any design that needs a pip install is wrong here.

---

## Phase 1 — Screenshot-aware Ask box (build first)

### Goal
On a phone: open the app, type a question, attach a game screenshot (or paste one),
get an answer that uses **both** the image (Claude vision) **and** the user's roster.
Behind a password. The screenshot supplies the live situation (enemy team, a mod, a
character screen); the roster supplies what the user actually owns.

### 1. The page — `swgoh.html`
- Add an **attach control** to the existing `.ask` box: a file input with
  `accept="image/*"` (on iOS this offers Camera / Photo Library) **and** paste-to-attach
  (listen for `paste` with image clipboard data on the textarea).
- Show a **thumbnail preview** of the attached image with a **remove (×)** button.
- On **Ask**: if an image is attached, read it as a base64 data URL and include it in the
  POST body: `{ question, roster, image: { media_type, data } }` (data = base64 without the
  `data:...;base64,` prefix). No image → body is unchanged from today.
- Keep the interaction **single-turn** (one question + optional image → one answer),
  matching the current design. Follow-up threading is explicitly out of scope for v1.
- Everything else on the page (KPIs, roster table) is untouched. The page is already
  responsive (`<meta viewport>`, fluid grid), so no separate mobile pass is needed.

### 2. The server — `ask_server.py`
- **Vision:** in `ask_claude()`, build the message `content` as a list. When an image is
  present, prepend an image block:
  `{"type":"image","source":{"type":"base64","media_type":<mt>,"data":<b64>}}`
  followed by the text block (roster + question). No image → text block only (today's
  behavior).
- **Model:** change default from `claude-sonnet-4-6` to **`claude-opus-4-8`**
  (still overridable via `ASK_MODEL`).
- **Answer length:** raise `max_tokens` 1024 → **2048** (counter/gear advice runs long).
- **Roster caching (cost):** send the slim roster as a cached block
  (`"cache_control":{"type":"ephemeral"}`) so repeated questions in a sitting reuse it at
  ~1/10th input cost. The roster is stable between daily refreshes and exceeds the cache
  minimum, so this applies cleanly.
- **System prompt:** extend it to say the user may attach a **screenshot from the game**
  (an enemy defense, a mod's stats, a character screen); read the image and answer using
  the roster provided; recommend only from what the user owns.
- **Image guard:** reject images over ~4 MB (before base64) with a clear error, so a huge
  paste can't blow up the request.

### 3. The lock — PIN + signed cookie (stdlib `hmac`)
Mirrors the SWU app's `SWU_APP_PIN` pattern; implemented in `ask_server.py`.

- New env: **`APP_PIN`** (the shared password) and **`AUTH_SECRET`** (server signing key).
- **Token:** `hmac_sha256(AUTH_SECRET, "authed")` hex digest. A static signed token is
  fine for one user / one PIN. (Optional hardening: fold an issue-date into the signed
  payload for expiry — noted, not required for v1.)
- **`POST /api/login`** — body `{pin}`. Constant-time compare (`hmac.compare_digest`)
  against `APP_PIN`. On success, set `Set-Cookie: auth=<token>; HttpOnly; Secure;
  SameSite=Lax; Path=/`. On failure, 401.
- **Gate:** `GET /` (the page) and `POST /api/ask` both require a valid `auth` cookie
  (verified with `compare_digest`). Missing/invalid → for the page, serve a minimal PIN
  prompt (a tiny inline form that POSTs to `/api/login` then reloads); for `/api/ask`,
  return 401.
- **Single-roster note:** this app only ever contains the user's own roster (baked into
  `swgoh_data.js` by `fetch_swgoh.py`). There is no lookup-anyone endpoint, so the
  "only my ally code" requirement is satisfied structurally — no code needed for it.

### 4. Config / secrets (server env only, never committed)
`.gitignore` already excludes `.env`, `*.key`, `*.pem`, `swgoh_config.json`.

| Env var | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Personal Anthropic key (not Canyon's) | — (required) |
| `ASK_MODEL` | Model id | `claude-opus-4-8` |
| `APP_PIN` | The password | — (required) |
| `AUTH_SECRET` | Cookie signing key | — (required) |
| `ASK_PORT` | Listen port | `8787` |

### 5. Deploy on Oracle (integrate with the single front door)
The repo's `deploy/` already has `swgoh-ask.service` (runs `ask_server.py` on
`127.0.0.1:8787`), `swgoh-fetch.{service,timer}` (daily roster pull, 08:17), and a
standalone `deploy/Caddyfile`.

**The reconciliation:** the Oracle box already runs **one** Caddy — the containerized
front door from `personal-cloud` — which owns ports 80/443. The dashboard's standalone
`deploy/Caddyfile` assumes a second host Caddy on the same ports, which would collide.
The dashboard must instead be **added as one more hostname on the existing front door.**

Recommended wiring (confirm against live server state at deploy time):
1. Run `ask_server.py` as the host systemd service `swgoh-ask` (already stubbed), listening
   on `127.0.0.1:8787`. Update its unit env: `ASK_MODEL=claude-opus-4-8`, plus `APP_PIN`
   and `AUTH_SECRET`.
2. Add a block to **personal-cloud's** `Caddyfile` for a new `$DASH_DOMAIN`, reverse-proxying
   to the host service. Since Caddy runs in a container, proxy to the host gateway
   (`host.docker.internal:8787` with `extra_hosts: ["host.docker.internal:host-gateway"]`
   on the caddy service) — **or**, if cleaner, dockerize `ask_server.py` as a compose service
   and proxy to it by service name. Decide at deploy time based on what's already running.
3. Point a hostname (`$DASH_DOMAIN`) at the server IP; ensure Oracle security list allows
   80/443. HTTPS (needed for `Secure` cookie + clipboard paste) is automatic via Caddy once
   the domain resolves. If no domain exists, a free `duckdns.org` name works.
4. Keep the daily `swgoh-fetch.timer` as-is.

**Open item (deploy-time, not feature-blocking):** confirm the current live state of the
Oracle box — is the dashboard already partly deployed? which Caddy is authoritative? — and
finalize step 2. The Phase 1 feature code (vision + PIN + model) is independent of this.

### 6. Testing
- **Fake-Claude unit test:** monkeypatch the HTTP call in `ask_server.py`; assert that when
  an image is supplied, the request body contains an `image` content block with the right
  `media_type`/`data` plus the roster+question text block, and that the returned text is the
  faked answer. No network, no spend.
- **Auth test:** `POST /api/ask` without a valid cookie → 401; with a cookie signed by
  `AUTH_SECRET` → passes to the (faked) Claude call. Wrong PIN to `/api/login` → 401; correct
  PIN → sets the cookie.
- **Guard test:** oversize image → clean error, no Claude call.
- **Manual:** verify the real screenshot flow in a phone-sized browser (attach + paste +
  answer) before calling it done.

### Out of scope (Phase 1)
Follow-up conversation threads; mod-level roster grounding (screenshot carries the mod);
any change to `swgoh-mod-planner`; email/push; the daily digest (Phase 2).

---

## Phase 2 — Daily "Today for You" tips digest (deferred; spec later)

Captured here so the decisions aren't lost. **Full design happens after Phase 1 ships.**

**What:** once a day, scan open SWGOH content sources, rank what's relevant to the user's
*current progression*, and show a **"Today for You"** panel on the dashboard.

**Decisions locked:**
- **Delivery:** in-app panel on the dashboard (chosen as the easier path — no email
  server / deliverability). Email can be added later.
- **Scan model:** **Haiku** (cheapest tier), targeting **~$1–3/month**. Ranking/summarizing
  doesn't need Opus; Opus stays reserved for the user's direct Ask-box questions.
- **Sources (open feeds only):** YouTube (search + creator-channel feeds + free
  transcripts), Reddit (r/SWGOH), official forums / patch notes, gaming-news feeds. Locked
  platforms (Discord, X/Twitter) are excluded — against ToS or newly expensive. YouTube +
  Reddit is where the user's real examples (Rotta farming, Coliseum leveling) live.
- **Relevance signal:** diff the daily roster snapshot to detect *what's new for the user*
  (e.g. "just unlocked Rotta yesterday") and lead the digest with content for that. This is
  what makes it a personal digest rather than generic news.

**Cost shape (once/day, bounded — no runaway):** content gathering (YouTube/Reddit/RSS/
transcripts) is free-tier; the single daily AI ranking call on Haiku is a few cents/day.

**To resolve in the Phase 2 spec:** exact source list + query construction; how many
candidates/transcripts to feed the model; where the digest data file lives and how the
panel renders it; scheduling (extend the existing systemd timer); dedup across days;
handling the roster-diff on the very first run.

---

## Build order
1. **Phase 1** — screenshot-aware Ask box + PIN + Opus + Oracle wiring. Ship it.
2. **Phase 2** — daily tips digest. Separate spec → plan → build.
