#!/usr/bin/env python3
"""Team Builder — suggested squads/fleets from the real roster (stdlib only).

Reuses gamehealth's roster helpers + meta_teams.json (already validated base_ids).
Answers three questions: what can I field right now, what's closest to complete
(and with which specific unit), and which owned-but-under-relic members would
most strengthen teams I already field.
"""
import gameplan
import gamehealth
from gamehealth import _index, _developed  # noqa: F401 - shared roster helpers


def squad_readiness(units, squad, name_map=None):
    idx = _index(units)
    name_map = name_map or {}
    min_relic = squad.get("min_relic", 0)
    members = []
    for b in squad["members"]:
        u = idx.get(b)
        members.append({
            "base_id": b, "name": (u.get("name") if u else name_map.get(b, b)),
            "owned": u is not None, "stars": (u.get("stars") if u else 0) or 0,
            "relic": (u.get("relic") if u else 0) or 0,
            "fieldable": _developed(u, min_relic=0, min_stars=7),
        })
    gaps = [m for m in members if not m["fieldable"]]
    weak = [m for m in members if m["fieldable"] and m["relic"] < min_relic]
    return {"id": squad["id"], "name": squad["name"], "kind": "squad",
            "modes": squad.get("modes", []), "min_relic": min_relic,
            "members": members, "total": len(members),
            "owned_count": sum(1 for m in members if m["owned"]),
            "ready": len(gaps) == 0, "gaps": gaps, "weak": weak}


def fleet_readiness(units, fleet, name_map=None):
    idx = _index(units)
    name_map = name_map or {}
    cap_u = idx.get(fleet["capital"])
    cap_fieldable = _developed(cap_u, min_relic=0, min_stars=7)
    ships = []
    for b in fleet["ships"]:
        u = idx.get(b)
        ships.append({"base_id": b, "name": (u.get("name") if u else name_map.get(b, b)),
                      "owned": u is not None, "stars": (u.get("stars") if u else 0) or 0,
                      "fieldable": _developed(u, min_relic=0, min_stars=7)})
    need = max(1, fleet.get("min_ships", 3))
    ready_ships = [s for s in ships if s["fieldable"]]
    short_by = max(0, need - len(ready_ships))
    ship_gaps = [s for s in ships if not s["fieldable"]][:short_by]
    cap_name = cap_u.get("name") if cap_u else name_map.get(fleet["capital"], fleet["capital"])
    cap_gap = [] if cap_fieldable else [
        {"base_id": fleet["capital"], "name": cap_name,
         "owned": cap_u is not None, "fieldable": False}]
    return {"id": fleet["id"], "name": fleet["name"], "kind": "fleet",
            "capital": {"base_id": fleet["capital"], "owned": cap_u is not None,
                        "fieldable": cap_fieldable},
            "ships": ships, "min_ships": need, "ready_ship_count": len(ready_ships),
            "ready": cap_fieldable and len(ready_ships) >= need,
            "gaps": cap_gap + ship_gaps}


def build_team_readiness(units, meta_teams, name_map=None):
    return {"squads": [squad_readiness(units, s, name_map) for s in meta_teams.get("squads", [])],
            "fleets": [fleet_readiness(units, f, name_map) for f in meta_teams.get("fleets", [])]}


def rank_team_suggestions(readiness):
    """Split into fieldable now vs closest-to-complete (fewest gaps first)."""
    all_teams = readiness["squads"] + readiness["fleets"]
    ready = [t for t in all_teams if t["ready"]]
    almost = sorted([t for t in all_teams if not t["ready"]], key=lambda t: len(t["gaps"]))
    return {"ready": ready, "almost": almost}


def upgrade_priorities(readiness, top=8):
    """Which NEW units to acquire/star next. Ranked by leverage (how many
    near-complete teams it unlocks), then by how close those teams already are."""
    almost = [t for t in (readiness["squads"] + readiness["fleets"]) if not t["ready"]]
    tally = {}
    for t in almost:
        for g in t["gaps"]:
            e = tally.setdefault(g["base_id"], {"base_id": g["base_id"], "name": g["name"],
                                                "teams": [], "best_gap_size": 999})
            e["teams"].append(t["name"])
            e["best_gap_size"] = min(e["best_gap_size"], len(t["gaps"]))
    out = sorted(tally.values(), key=lambda e: (-len(e["teams"]), e["best_gap_size"]))
    return out[:top]


def strengthen_priorities(readiness, top=8):
    """For squads already fieldable, which owned-but-under-relic members to
    invest in next — ranked by teams closest to full strength (fewest weak)."""
    ready = sorted([t for t in readiness["squads"] if t["ready"] and t.get("weak")],
                   key=lambda t: len(t["weak"]))
    out = []
    for t in ready:
        for w in t["weak"]:
            out.append({"team": t["name"], "base_id": w["base_id"], "name": w["name"],
                        "have_relic": w["relic"], "need_relic": t["min_relic"]})
    return out[:top]


def build_team_prompt(roster, readiness, suggestions, upgrade_pri, strengthen_pri, knowledge):
    ready_lines = [f"- {t['name']} ({t['kind']})" for t in suggestions["ready"]]
    almost_lines = []
    for t in suggestions["almost"][:10]:
        gap_names = ", ".join(g["name"] for g in t["gaps"])
        almost_lines.append(f"- {t['name']}: needs {gap_names}")
    upg_lines = [f"- {u['name']}: unlocks {len(u['teams'])} team(s) — "
                f"{', '.join(u['teams'])}" for u in upgrade_pri]
    str_lines = [f"- {s['team']}: relic {s['name']} from R{s['have_relic']} to "
                f"R{s['need_relic']}" for s in strengthen_pri]
    know_block = gameplan._knowledge_block(knowledge)
    return (
        "You are a Star Wars: Galaxy of Heroes team-building coach. The data below "
        "is COMPUTED from the real roster and validated meta squads/fleets — do not "
        "contradict it. Recommend which teams to play now, which to build toward "
        "next, and which existing teams to strengthen for the best effectiveness "
        "gain per unit of effort.\n\n"
        f"PLAYER: {roster.get('name')} — GP {roster.get('galactic_power')}.\n\n"
        "READY TO FIELD NOW:\n" + ("\n".join(ready_lines) or "- (none yet)") + "\n\n"
        "CLOSEST TO COMPLETE (fewest gaps first):\n"
        + ("\n".join(almost_lines) or "- (none)") + "\n\n"
        "HIGHEST-LEVERAGE NEW UNITS TO ACQUIRE (unlock the most teams):\n"
        + ("\n".join(upg_lines) or "- (none)") + "\n\n"
        "BEST RELIC/GEAR INVESTMENTS IN TEAMS YOU ALREADY FIELD:\n"
        + ("\n".join(str_lines) or "- (none)") + "\n"
        + know_block +
        "\nRespond with: (1) which ready team to lean on right now and where to "
        "play it (GAC/TW/Fleet Arena), (2) the single best team to build toward "
        "next and the specific unit to prioritize, (3) the best strengthen-in-place "
        "investment. Be specific to this roster."
    )


def generate_teams(roster, meta_teams, knowledge, gemini_call, key, name_map=None):
    units = roster.get("units", [])
    readiness = build_team_readiness(units, meta_teams, name_map)
    suggestions = rank_team_suggestions(readiness)
    upg = upgrade_priorities(readiness)
    strengthen = strengthen_priorities(readiness)
    prompt = build_team_prompt(roster, readiness, suggestions, upg, strengthen, knowledge)
    return {"readiness": readiness, "ready": suggestions["ready"],
            "almost": suggestions["almost"], "upgrade_priorities": upg,
            "strengthen_priorities": strengthen, "plan": gemini_call(prompt, key)}
