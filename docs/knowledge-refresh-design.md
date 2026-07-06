# SWGOH Knowledge Base — auto-refresh design

**Goal:** Keep GL requirements and current-meta guidance fresh over time without hand-maintaining them, while staying trustworthy (every fact carries where it came from and when).

## The problem
GL prerequisite rosters and "what's meta" drift as CG changes the game and the
community discovers things. A static comparison database rots silently. We need
a **stable schema** whose **values update over time** with **provenance**, and a
**refresh loop** that proposes updates for human approval.

## Store — git-versioned files (no database)
Two files in the repo, read fresh per request by the coach:

- `goals.json` — GL unlock requirements (existing schema). Each GL gains
  `source` + `as_of`; the loop may stamp per-unit `source/as_of/confidence`.
- `knowledge.json` — the meta layer: farming priorities, gear/relic guidance,
  what's-meta. Each item carries `text` + `source` (url) + `confidence`.
- `meta_teams.json` — squad/fleet compositions the Team Builder scores readiness
  against. Each entry carries `source` + `confidence`. **Root-cause note:** this
  file was originally hand-authored from memory with no review gate (unlike
  goals.json/knowledge.json) — that's why the GL Leia entry was wrong until a
  player corrected it with real ladder evidence. It's now under the same
  refresh discipline as everything else (see below), closing that gap.

Git is the history: every approved refresh is a commit → full audit + one-command
rollback when the community jumps the gun on a rumor.

## Refresh loop — Tavily (retrieve) + Gemini (synthesize), cron on the box
`refresh_knowledge.py` (zero-dependency stdlib, same as everything here):

1. **Retrieve** — Tavily REST search across game sources (swgoh.gg,
   swgohevents.com, Reddit, YouTube). Tavily searches from *its* servers, so it
   reaches swgoh.gg content that 403s the Oracle IP directly — that was the
   original blocker.
2. **Synthesize** — feed results + the current files to Gemini (already wired on
   the box); it emits proposed `goals.json` + `knowledge.json` in our schema with
   `source`/`as_of`/`confidence` on every fact, plus a plain-English changelog.
3. **Validate** — reject the proposal if any `base_id` doesn't resolve against
   the live game-data name map or any relic is out of range. Garbage never
   reaches a proposal.
4. **Propose, don't apply** — write `goals.proposed.json`,
   `knowledge.proposed.json`, and `KNOWLEDGE_CHANGELOG.md`. Live files untouched.
5. **Approve** — human skims the changelog/diff; `--promote` swaps proposed →
   live, and the change is committed to git (on the Mac, the canonical repo).

**Team refresh (meta_teams.json):** same pipeline, member-name resolution
instead of base_id passthrough — Gemini proposes squads/fleets using real
character/ship NAMES (it can't reliably produce our internal ids), each name
is resolved against the live game-data name map, and a squad is dropped
entirely (not partially kept) if any member fails to resolve. Merging is
diff-aware: a new `id` is added outright; a proposal matching an existing id
with a near-identical roster (≤2 members different) updates it in place; a
proposal matching an existing id but meaningfully different (>2 members
different) is kept as a **new variant** rather than silently overwriting a
verified entry — this is exactly the GL Leia Old-Ben/Kanan vs Jyn/Raddus case.
**On-demand override:** `--add-team "<name>" "<Unit One, Unit Two, ...>"
[--source "..."]` appends a squad you've personally verified (e.g. real ladder
evidence) straight to the live file, no proposal step — mirrors `--add-video`'s
rationale (you vetted it, so the review gate isn't needed).

**Cadence:** weekly host cron on the box (`refresh_knowledge.py`, no args),
beside the existing digest cron — the team refresh runs as part of the same
default flow, no separate cron entry needed.

## Trust boundary
Research *proposes*; a human *approves*; git *records*. Community info is
sometimes premature or wrong, so nothing auto-commits to the live files.

## Out of scope (v1)
In-app approval UI (review is a changelog + git diff for now); academic-grade
citation verification (game facts aren't in academic databases).
