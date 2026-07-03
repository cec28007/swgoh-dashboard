# Design — Phase 2: daily "Today for You" tips digest

**Status:** Approved for spec review
**Date:** 2026-07-03
**Repo:** `swgoh-dashboard` (cec28007/swgoh-dashboard)
**Depends on:** Phase 1 (roster via comlink + enrichment, Gemini Ask box) — shipped.

## Goal
A **"Today for You"** panel at the top of the dashboard. Once a day it scans open
SWGOH content sources, ranks what's relevant to the player's *current progression*
(driven by what changed in their roster), and shows a short list — each item a link
with a one-line "why this matters to you." Runs on Gemini's free tier: **$0**.

## Constraints (carried from Phase 1)
- **Zero dependencies — Python standard library only.** RSS via `xml.etree`,
  JSON APIs via `urllib`, no new packages.
- Secrets in the server environment only (`GEMINI_API_KEY` exists;
  `YOUTUBE_API_KEY` new, optional).
- Runs on the Oracle box in the `swgoh-dash` container; a daily cron triggers it.
- Gemini model `gemini-2.5-flash` (free tier), reused from the Ask box.

## Relevance signal: the roster diff
The daily roster refresh already writes `swgoh_data.js`. The digest keeps a compact
snapshot (`roster_snapshot.json`) and diffs the new roster against it to detect
**what's new for the player**: newly unlocked units, star-ups (rarity), gear-tier
jumps, relic increases, new zetas/omicrons. This "what changed" list is the primary
relevance driver (unlock Rotta yesterday → Rotta content ranks top). On the first
run (no prior snapshot) the diff is empty and the digest falls back to generic
"what's hot" ranking against the roster's weak spots.

## Content sources (open feeds; no scraping, no locked platforms)
1. **YouTube — curated creators (free RSS):** ~10–12 hard-coded top SWGOH channel
   IDs; pull recent uploads from `https://www.youtube.com/feeds/videos.xml?channel_id=<id>`.
   No API key. Always on.
2. **YouTube — topic search (Data API v3):** when `YOUTUBE_API_KEY` is set, search
   `search?part=snippet&q=<term>&order=date&type=video` for terms built from the
   roster diff (new unit names) plus a couple of evergreen terms. Falls back silently
   to RSS-only when the key is absent or the quota errors.
3. **Reddit r/SWGOH (free JSON):** `https://www.reddit.com/r/SWGOH/top.json?t=day`
   and `/new.json` with a descriptive User-Agent. Surfaces guides and official
   patch/news megathreads.

Each candidate is normalized to `{title, url, source, published, snippet}`.

## Pipeline — new `digest.py` (zero-dependency)
Small, testable units:
- `load_snapshot(path)` / `save_snapshot(roster, path)` — compact per-unit facts
  (base_id, name, stars, gear_level, relic, zetas, omicrons).
- `compute_roster_diff(current, previous) -> {new_units, star_ups, gear_ups, relic_ups, new_zetas}`
  — pure; each entry names the unit and the change.
- `fetch_youtube_rss(channel_ids) -> [candidate]` — parse Atom feeds with `xml.etree`.
- `search_youtube_api(terms, key) -> [candidate]` — Data API; `[]` when no key.
- `fetch_reddit(subreddit) -> [candidate]` — parse the listing JSON.
- `build_candidates(...)` — merge + dedupe by URL, cap to a sane number (e.g. 40).
- `rank_with_gemini(roster_summary, diff, candidates, key) -> digest` — one Gemini
  call; prompt asks for JSON: an ordered list of `{title, url, source, why}` (the
  `why` ties it to the player's roster/diff), plus a one-line `headline`. Parse and
  validate; on failure, fall back to a non-AI heuristic (diff-name matches in titles).
- `write_digest(digest, path)` — writes `digest.js` as `window.SWGOH_DIGEST = {...}`.
- `main()` — orchestrates; writes `digest.js` and updates the snapshot.

Network is injected (a `fetcher` param) so the pure logic is unit-tested without
hitting the internet or spending tokens (the Gemini/HTTP calls are faked in tests).

## Dashboard panel (`swgoh.html`)
A **"🗞 Today for You"** card above the KPIs, styled like the existing cards. Loads
`digest.js` (served ungated like `swgoh_data.js`); renders the `headline` + the
ranked items (title links open in a new tab, source pill, the `why` line). If
`digest.js` is missing/empty, the panel hides itself. No framework — same inline
style as the rest of the page.

## Deploy
- `digest.py` runs in the `swgoh-dash` container (bind-mounted repo). `digest.js`
  and `roster_snapshot.json` are box-generated runtime files (like `swgoh_data.js`)
  — **rsync must exclude them.**
- New daily cron on the box, a few minutes after the roster refresh:
  `docker compose exec -T swgoh-dash python3 digest.py`.
- `YOUTUBE_API_KEY` added to the `swgoh-dash` service env (optional) + `.env.example`
  in both repos.

## Testing
- `compute_roster_diff` — new unlock, star-up, gear-up, relic-up, new-zeta all detected;
  no-change → empty.
- `fetch_youtube_rss` / `fetch_reddit` — parse sample feed/JSON fixtures into candidates.
- `build_candidates` — merge + dedupe by URL + cap.
- `rank_with_gemini` — with a faked Gemini response, returns the parsed digest; on
  malformed JSON, the heuristic fallback runs.
- `write_digest` — emits valid `window.SWGOH_DIGEST = {...};`.
- Manual: run once on the box, confirm `digest.js` populates and the panel renders.

## Cost & safety
$0 — Gemini free tier, YouTube free quota, Reddit/RSS free. One bounded call/day.
Read-only; the page stays PIN-gated. YouTube API is optional and fails soft.

## Out of scope (v1)
Email delivery; datacron/GAC/TW feeds; per-item thumbnails; user-tunable creator list
(hard-coded for now); notifications.
