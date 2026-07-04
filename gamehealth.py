#!/usr/bin/env python3
"""Game Health — a top-down, roster-grounded progression coach (stdlib only).

Scores the account across the game's crystal/resource domains (Galactic Legends,
GAC squad depth, Fleet, Roster depth), ranks the gaps by ROI, and asks Gemini to
turn that into a "focus this week / today" call. See docs/game-health-design.md.

Design principle: everything the model reasons over is COMPUTED from the real
roster here (grounded), not left to the model to guess. Ships are included.
"""
import json
import os

import gameplan  # reuse gl_readiness + load_goals/load_knowledge

HERE = os.path.dirname(os.path.abspath(__file__))
META_TEAMS_PATH = os.path.join(HERE, "meta_teams.json")

# The flywheel weighting: GLs lift every mode at once, so a GL gap is worth more
# than an equal-sized gap elsewhere. Fleet is cheap to fix, so it punches up.
DOMAIN_WEIGHTS = {
    "galactic_legends": 1.5,
    "gac_depth": 1.2,
    "fleet": 1.0,
    "roster_depth": 0.8,
}
DOMAIN_LABELS = {
    "galactic_legends": "Galactic Legends",
    "gac_depth": "GAC / squad depth",
    "fleet": "Fleet",
    "roster_depth": "Roster depth",
}


def load_meta_teams(path=META_TEAMS_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"squads": [], "fleets": []}


def _index(units):
    return {u.get("base_id"): u for u in units}


def _developed(u, min_relic=0, min_stars=7):
    return (u and (u.get("stars") or 0) >= min_stars
            and (u.get("relic") or 0) >= min_relic)


# --------------------------------------------------------------------------- #
# Domain scores (0-100), each grounded in the roster
# --------------------------------------------------------------------------- #
def score_galactic_legends(units, goals):
    readiness = gameplan.gl_readiness(units, goals)
    owned = [g for g in readiness if g["unlocked"]]
    in_prog = [g for g in readiness if not g["unlocked"]]
    best = max((g["met"] / g["total"] for g in in_prog if g["total"]), default=0.0)
    score = min(100, len(owned) * 20 + round(best * 20))
    return {"score": score, "owned": len(owned),
            "owned_names": [g["name"] for g in owned],
            "next": in_prog[0]["name"] if in_prog else None,
            "next_progress": round(best * 100),
            "state": f"{len(owned)} owned; next {round(best*100)}% ready"}


def fieldable_squads(units, meta_teams):
    """Teams you can put on a GAC/TW board = own every member at 7 stars.
    (Relic is strength, not fieldability — a 7-star GL at R0 still fields.)"""
    idx = _index(units)
    out = []
    for s in meta_teams.get("squads", []):
        if all(_developed(idx.get(b), min_relic=0, min_stars=7) for b in s["members"]):
            out.append(s["id"])
    return out


def score_squad_depth(units, meta_teams, target=9):
    fieldable = fieldable_squads(units, meta_teams)
    total = len(meta_teams.get("squads", []))
    score = min(100, round(len(fieldable) / target * 100)) if target else 0
    return {"score": score, "fieldable": fieldable, "meta_total": total,
            "state": f"{len(fieldable)} meta teams fieldable"}


def score_fleet(units, meta_teams):
    idx = _index(units)
    best_ratio, best_fleet, capitals = 0.0, None, 0
    for fl in meta_teams.get("fleets", []):
        has_cap = _developed(idx.get(fl["capital"]), min_relic=0, min_stars=7)
        if has_cap:
            capitals += 1
        ready = sum(1 for s in fl["ships"]
                    if _developed(idx.get(s), min_relic=0, min_stars=7))
        need = max(1, fl.get("min_ships", 3))
        ratio = (ready / need) if has_cap else 0.0
        if ratio > best_ratio:
            best_ratio, best_fleet = ratio, fl["name"]
    score = min(100, round(min(1.0, best_ratio) * 70) + min(30, capitals * 10))
    return {"score": score, "best_fleet": best_fleet, "capitals_owned": capitals,
            "state": (f"{best_fleet} strongest" if best_fleet
                      else "no synergized fleet")}


def score_roster_depth(units):
    chars = [u for u in units if u.get("type") == "character"]
    r7 = sum(1 for u in chars if (u.get("relic") or 0) >= 7)
    g13 = sum(1 for u in chars if (u.get("gear_level") or 0) >= 13)
    score = min(100, round(r7 / 40 * 100))  # ~40 relic-7s = deep bench
    return {"score": score, "relic7_plus": r7, "gear13": g13,
            "state": f"{r7} units at Relic 7+"}


# --------------------------------------------------------------------------- #
# Assemble + rank
# --------------------------------------------------------------------------- #
def assess_health(roster, goals, meta_teams, econ=None):
    units = roster.get("units", [])
    domains = {
        "galactic_legends": score_galactic_legends(units, goals),
        "gac_depth": score_squad_depth(units, meta_teams),
        "fleet": score_fleet(units, meta_teams),
        "roster_depth": score_roster_depth(units),
    }
    tw = sum(DOMAIN_WEIGHTS.values())
    overall = round(sum(domains[d]["score"] * DOMAIN_WEIGHTS[d]
                        for d in domains) / tw)
    return {"domains": domains, "overall": overall, "econ": econ or {}}


def rank_opportunities(health):
    """Rank domains by ROI = gap-to-100 × flywheel weight (biggest lever first)."""
    opps = []
    for d, v in health["domains"].items():
        gap = 100 - v.get("score", 0)
        roi = round(gap * DOMAIN_WEIGHTS.get(d, 1.0))
        opps.append({"domain": d, "label": DOMAIN_LABELS.get(d, d),
                     "score": v.get("score", 0), "roi": roi,
                     "why": v.get("state", "")})
    opps.sort(key=lambda o: o["roi"], reverse=True)
    return opps


# --------------------------------------------------------------------------- #
# Narrative synthesis
# --------------------------------------------------------------------------- #
def _meta_lines(roster):
    """Live account meta auto-pulled from the game API (fetch_swgoh.player_meta):
    arena ranks, GAC standing, Era progression, datacrons. [] if absent."""
    m = (roster or {}).get("meta") or {}
    out = []
    if m.get("squad_arena_rank") is not None:
        out.append(f"Squad Arena rank: {m['squad_arena_rank']}")
    if m.get("fleet_arena_rank") is not None:
        out.append(f"Fleet Arena rank: {m['fleet_arena_rank']}")
    if m.get("gac_league"):
        out.append(f"GAC: {m['gac_league']} division {m.get('gac_division')} "
                   f"(skill rating {m.get('gac_skill_rating')})")
    r = m.get("gac_recent") or {}
    if r.get("rank") is not None:
        out.append(f"Recent GAC: {r.get('wins')}W-{r.get('losses')}L, "
                   f"{r.get('points')} pts, rank {r.get('rank')}")
    if m.get("era_units"):
        out.append("Era units: " + ", ".join(
            f"{e.get('name', e['base_id'])} EL{e['era_level']}" for e in m["era_units"]))
    if m.get("loaned_era_level") is not None:
        out.append(f"Loaned unit Era Level: {m['loaned_era_level']}")
    if m.get("datacron_count"):
        out.append(f"Datacrons owned: {m['datacron_count']}")
    return out


def build_health_prompt(roster, health, opps, econ, knowledge):
    dlines = []
    for d, v in health["domains"].items():
        dlines.append(f"- {DOMAIN_LABELS.get(d, d)}: {v['score']}/100 — {v.get('state','')}")
    olines = [f"- {o.get('label', o.get('domain'))} (ROI {o['roi']}): "
              f"{o.get('why', '')}" for o in opps]
    econ = {k: v for k, v in (econ or {}).items() if str(v).strip()}
    econ_block = ("\nPLAYER-REPORTED ECONOMY (use for crystal-ROI; else infer):\n"
                  + json.dumps(econ) + "\n") if econ else \
                 "\n(No economy numbers provided — infer crystal ROI from roster + principles.)\n"
    know_block = gameplan._knowledge_block(knowledge)
    ml = _meta_lines(roster)
    meta_block = ("\nLIVE ACCOUNT META (auto-pulled from the game API — arena/GAC/Era):\n- "
                  + "\n- ".join(ml) + "\n") if ml else ""
    return (
        "You are an elite Star Wars: Galaxy of Heroes account coach. Give a "
        "TOP-DOWN health read and the highest-ROI focus. The scores below are "
        "COMPUTED from the real roster — do not contradict them.\n\n"
        "SWGOH is an economy: crystals/Kyber are the master resource, and Galactic "
        "Legends are the flywheel (they win GAC/TW/arena at once → more crystals → "
        "gear faster → more GLs). Weigh GL investment against cheap gaps that leak "
        "free crystals (e.g. an unsynergized fleet).\n\n"
        f"PLAYER: {roster.get('name')} — GP {roster.get('galactic_power')}.\n"
        + meta_block +
        "\nHEALTH SCORECARD:\n" + "\n".join(dlines) + "\n\n"
        f"BIGGEST GAPS (ROI-ranked):\n" + "\n".join(olines) + "\n"
        + econ_block + know_block +
        "\nRespond in three short sections:\n"
        "1. HEALTH: one line per domain — where they're strong vs the biggest gap.\n"
        "2. THIS WEEK: the single most impactful focus and why (tie to crystal/"
        "resource ROI), with the specific units/teams/ships to work on.\n"
        "3. TODAY: 2-3 concrete actions to start now.\n"
        "Be specific to THIS roster. Don't recommend units they don't own."
    )


def build_deep_dive_brief(roster, goals, meta_teams, knowledge, econ=None, question=""):
    """A complete, copy-pasteable account brief to hand Claude for an on-demand
    deep dive — everything computed here so Claude comes up to speed in one shot,
    no model call and no rate limit."""
    units = roster.get("units", [])
    health = assess_health(roster, goals, meta_teams, econ)
    opps = rank_opportunities(health)
    readiness = gameplan.gl_readiness(units, goals)
    chars = sorted([u for u in units if u.get("type") == "character"],
                   key=lambda x: x.get("power") or 0, reverse=True)
    ships = sorted([u for u in units if u.get("type") == "ship"],
                   key=lambda x: x.get("power") or 0, reverse=True)
    L = [f"# SWGOH Deep-Dive Brief — {roster.get('name')} (GP {roster.get('galactic_power')})", "",
         ("You are my SWGOH progression coach. Below is my current account state, "
          "computed from my live roster, plus the strategy knowledge my app uses. "
          "Come up to speed, then answer my question with deep, specific, "
          "roster-grounded reasoning."), "",
         f"## Health scorecard (overall {health['overall']}/100)"]
    for d, v in health["domains"].items():
        L.append(f"- {DOMAIN_LABELS[d]}: {v['score']}/100 — {v.get('state', '')}")
    L += ["", "## Biggest levers (ROI-ranked)"]
    L += [f"- {o['label']} (ROI {o['roi']}): {o['why']}" for o in opps]

    ml = _meta_lines(roster)
    if ml:
        L += ["", "## Live account meta (auto-pulled from the game API)"] + [f"- {x}" for x in ml]
    L += ["", "## Galactic Legends"]
    owned = [g for g in readiness if g["unlocked"]]
    L.append("Owned: " + (", ".join(g["name"] for g in owned) or "none"))
    for g in readiness:
        if g["unlocked"]:
            continue
        bits = [f"{g['met']}/{g['total']} reqs met"]
        if g["missing"]:
            bits.append("don't own: " + ", ".join(g["missing"]))
        if g["under_relic"]:
            bits.append("below target: " + ", ".join(
                (f"{u['name']} {u.get('stars', 0)}★→7★"
                 if "stars" in u.get("reason", []) else f"{u['name']} R{u['have']}→R{u['need']}")
                for u in g["under_relic"]))
        L.append(f"- {g['name']}: " + "; ".join(bits))

    L += ["", "## Top characters"]
    L += [f"- {u.get('name', u['base_id'])}: {u.get('stars')}★ R{u.get('relic')} "
          f"G{u.get('gear_level')} spd {u.get('speed')}" for u in chars[:20]]
    fl = health["domains"]["fleet"]
    L += ["", "## Fleet", f"Strongest: {fl.get('best_fleet')} | capitals owned: {fl.get('capitals_owned')}",
          "Top ships: " + ", ".join(f"{u.get('name', u['base_id'])} {u.get('stars')}★"
                                    for u in ships[:10])]
    gd = health["domains"]["gac_depth"]
    L += ["", "## GAC / squad depth",
          f"Fieldable meta teams ({len(gd.get('fieldable', []))}): "
          + ", ".join(gd.get('fieldable', []))]

    L += ["", "## Strategy & mechanics knowledge my app uses"]
    for key, label in [("progression_strategy", "Progression strategy"),
                       ("game_mechanics", "Game mechanics (classic + ERA/Coliseum)")]:
        items = [i.get("text") for i in (knowledge or {}).get(key, []) if i.get("text")]
        if items:
            L.append(f"### {label}")
            L += [f"- {t}" for t in items]
    econ = {k: v for k, v in (econ or {}).items() if str(v).strip()}
    if econ:
        L += ["", "## My economy inputs", json.dumps(econ)]
    L += ["", "## My question", question.strip() or "(type your question here)"]
    return "\n".join(L)


def generate_health(roster, goals, meta_teams, econ, knowledge, gemini_call, key):
    health = assess_health(roster, goals, meta_teams, econ)
    opps = rank_opportunities(health)
    prompt = build_health_prompt(roster, health, opps, econ, knowledge)
    return {"health": health, "opportunities": opps,
            "plan": gemini_call(prompt, key)}
