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

**Cadence:** weekly host cron on the box, beside the existing digest cron.

## Trust boundary
Research *proposes*; a human *approves*; git *records*. Community info is
sometimes premature or wrong, so nothing auto-commits to the live files.

## Out of scope (v1)
In-app approval UI (review is a changelog + git diff for now); academic-grade
citation verification (game facts aren't in academic databases).
