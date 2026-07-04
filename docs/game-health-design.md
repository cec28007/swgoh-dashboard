# Game Health — from GL-checklist to a full progression coach

**Goal:** Walk into the tool, hit Game Plan, and get a top-down health read of the
whole account + the highest-ROI thing to focus on this week/today — not just "next GL."

## The model: SWGOH as a portfolio of crystal/resource streams

Each domain is a "business unit" that produces crystals or resources. Health is
scored per domain from the real roster; gaps are ranked by ROI (payoff ÷ effort).

Domains (v1): **Galactic Legends** (the flywheel engine), **GAC/Squad depth**,
**Fleet**, **Roster depth**. (Later: Conquest, Territory Battles/Wars, Arena rank.)

The flywheel: more GLs → win GAC/TW/arena → more crystals + Kyber → gear faster →
more GLs. So GLs usually win the ROI race *unless* a cheap gap is leaking free
crystals (e.g. an unsynergized fleet — near-zero effort, immediate payoff). The
engine makes that call from the actual roster.

## Grounding — what we can and can't measure

- **Can (from roster):** GL ownership/progress, how many meta squads/fleets are
  fieldable (depth), capital-ship + fleet synergy, roster depth (R7+/G13 counts).
- **Can't (not in the API):** arena/fleet rank, GAC league, crystals/day, Conquest
  tier. → an **optional economy input panel** (like the token inventory) lets the
  player supply these; blank degrades gracefully to roster inference.

## New data artifact: `meta_teams.json`

Like `goals.json`, but for **squads and fleets** — each with member base_ids, a
viability threshold (7★ + min relic / min stars), the modes it serves, and a note.
Depth/fleet scoring runs the roster against it. Base_ids validated against game
data (same resolver approach as goals.json). The weekly Tavily refresh gains the
job of keeping it current.

## Architecture

- `gamehealth.py` (stdlib): pure scoring functions per domain → `assess_health` →
  `rank_opportunities` (ROI) → `build_health_prompt` → `generate_health` (Gemini
  narrative "this week / today"). Ships are included (the old coach dropped them).
- `meta_teams.json`: seeded now, resolver-validated, refreshable.
- `/api/gamehealth` (PIN-gated) replaces/augments `/api/gameplan`.
- Frontend: scorecard (domain scores + biggest gaps) → the weekly/daily focus →
  collapsible detail; optional economy-input panel.

## Build order (each slice tested + deployed)

1. **Scoring engine + tests** (this slice) — pure, roster-grounded domain scores +
   ROI ranking, tested with synthetic data.
2. `meta_teams.json` seed + box resolver validation.
3. `/api/gamehealth` + Gemini narrative synthesis.
4. Frontend scorecard + economy-input panel.

## Out of scope (v1)
Conquest/TB/Arena precise scoring (added once economy inputs + more meta data land);
live rank tracking (not in API).
