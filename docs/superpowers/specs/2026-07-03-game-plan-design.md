# Design — Grounded "Game Plan" progression coach

**Status:** Approved for spec review
**Date:** 2026-07-03
**Repo:** `swgoh-dashboard`
**Depends on:** roster+stats enrichment (shipped), digest (shipped), Gemini Ask (shipped).

## Goal
A **Game Plan** section on the dashboard: tap **Generate my plan** and Gemini returns a
ranked, roster-specific progression roadmap — led by the player's **fastest Galactic
Legend path**, with high-value gear/relic and meta-team callouts, and token-spending tips
when balances are entered. On-demand (heavy call), $0 on Gemini free tier.

## Why it can be good: grounding
The value comes from feeding Gemini **computed facts**, not asking it to recall them:
- **Real roster** — 337 units, true gear/relic/speed/zetas/GP (from comlink + stats).
- **Exact GL-readiness math** — computed server-side from a maintained `goals.json`
  (Galactic Legends → prerequisite units + required 7★/relic). Produces, precisely, which
  requirements the player meets, which units are missing, and which are under-relic.
- **CG's own data** — `recommendedSquad` / `unitGuideDefinition` from the game-data cache,
  surfaced where clean (team/gear guidance from CG, not memory). *Enrichment; degrade
  gracefully if absent.*
- **Current meta** — reuse the latest `digest.js` items (recent creator/guide titles) as a
  "what's strong now" signal so advice isn't stuck at Gemini's training cutoff.
- Gemini then **synthesizes** a ranked plan over these facts.

## Constraints (carried)
- **Zero dependency — stdlib only.** Gemini via `urllib` (reuse `post_to_gemini`).
- Secrets server-side; PIN-gated endpoint.
- Runs in the `swgoh-dash` container; roster/gamedata/digest read from box files.
- Model `gemini-2.5-flash` (free tier).

## Currencies: optional type-in inputs
comlink cannot read inventory (game's public API exposes no wallet — confirmed). The
player's real need is a couple of spend currencies, so the Game Plan has **optional input
fields**: **Lightspeed Tokens, Era Shipment Tokens, Crystals** (easily extended). Values
are saved in the browser's `localStorage` (persist per device) and sent with the plan
request. Blank → the plan simply omits token advice. No login, no inventory sync, no risk.

## Components
- **`goals.json`** (new, maintained data) — `{"galactic_legends":[{"id","name",
  "requirements":[{"base_id","name","relic"}...]}...]}`. Seeded from game data where
  derivable, else curated; the one hand-maintained file (update as new GLs release).
- **`gameplan.py`** (new, zero-dep, pure + testable):
  - `load_goals(path) -> list`.
  - `gl_readiness(units, goals) -> list` — per GL: `{name, unlocked(bool), met, total,
    missing:[names], under_relic:[{name,have,need}]}`. `unlocked` = the GL's own base_id is
    in the roster. Ranks by fewest gaps.
  - `build_plan_prompt(roster_summary, readiness, tokens, meta_titles) -> str`.
  - `generate_plan(roster, goals, tokens, meta_titles, gemini_call) -> str` — composes the
    above, calls Gemini (injected for tests), returns the plan markdown text.
- **`ask_server.py`** — `POST /api/gameplan` (PIN-gated): reads `swgoh_data.js`,
  `goals.json`, `gamedata_cache.json` (optional enrichment), `digest.js` (meta titles),
  takes `{tokens}` from the body, runs `generate_plan`, returns `{plan}`. `ValueError`→400,
  errors→500.
- **`swgoh.html`** — a **Game Plan** card: the three token inputs (localStorage-backed),
  a **Generate my plan** button, and a rendered plan area. Plan text rendered
  HTML-escaped with `white-space:pre-wrap` (safe; no markdown-injection). Loading + 401→PIN
  states mirror the Ask box.

## Data flow
Page load → token fields hydrate from localStorage. User edits tokens (saved). Taps
Generate → `POST /api/gameplan {tokens}` → server reads roster/goals/gamedata/digest,
computes `gl_readiness`, builds prompt, calls Gemini → `{plan}` → rendered in the card.

## Testing (TDD)
- `gl_readiness`: detects unlocked GL (own base_id present); counts met/total; lists missing
  units and under-relic units with have/need; ranks closest-first; empty goals → empty.
- `load_goals`: parses the file; missing file → clear error.
- `build_plan_prompt`: includes roster summary, readiness gaps, tokens (when present), and
  meta titles.
- `generate_plan`: with a faked Gemini call returns the plan text; passes computed facts
  into the prompt.
- `/api/gameplan`: 401 without cookie; with cookie + faked Gemini → `{plan}`.
- Manual: generate live on the box; confirm the plan cites real roster facts (e.g. R10 Leia)
  and, when tokens entered, gives spend advice; verify the card renders on a phone.

## Cost & safety
One Gemini call per tap (on-demand); free tier, token-heavy but bounded. Read-only,
PIN-gated. `goals.json` is the only maintained data.

## Out of scope (v1)
Parsing the raw game `requirement` tree (rabbit hole — curate instead); screenshot-reading
token balances (type-in for now; vision-read is an easy later add); auto-inventory /
account login (structurally impossible via public API, and refused on principle);
non-GL goal types (fleet, datacrons, GAC ladders) beyond what Gemini adds as callouts.
