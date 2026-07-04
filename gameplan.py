#!/usr/bin/env python3
"""Grounded "Game Plan" progression coach (zero-dependency, stdlib only).

Computes exact Galactic-Legend readiness from the real roster against a maintained
goals.json (GL -> prerequisite units + required relic), then asks Gemini to
synthesize a ranked progression plan over those facts plus optional token balances
and the current-meta digest.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS_PATH = os.path.join(HERE, "goals.json")


def load_goals(path=GOALS_PATH):
    with open(path) as f:
        return json.load(f).get("galactic_legends", [])


def gl_readiness(units, goals):
    """Per GL: unlocked?, met/total prereqs, missing units, under-relic units.

    Ranked closest-first: not-yet-unlocked GLs (fewest gaps first), then owned GLs.
    """
    by_id = {u.get("base_id"): u for u in units}
    out = []
    for gl in goals:
        reqs = gl.get("requirements", [])
        met, missing, under = 0, [], []
        for r in reqs:
            u = by_id.get(r["base_id"])
            if u is None:
                missing.append(r["name"])
            elif (u.get("relic") or 0) >= r.get("relic", 0) and (u.get("stars") or 0) >= 7:
                met += 1
            else:
                under.append({"name": r["name"], "have": u.get("relic") or 0,
                              "need": r.get("relic", 0)})
        out.append({
            "id": gl["id"], "name": gl["name"],
            "unlocked": gl["id"] in by_id,
            "met": met, "total": len(reqs),
            "missing": missing, "under_relic": under,
            "gaps": len(missing) + len(under),
        })
    out.sort(key=lambda x: (x["unlocked"], x["gaps"]))
    return out


def build_plan_prompt(roster_summary, readiness, tokens, meta_titles):
    lines = []
    for g in readiness:
        if g["unlocked"]:
            lines.append(f"- {g['name']}: UNLOCKED"
                         + (f"; still under-relic: {', '.join(u['name'] for u in g['under_relic'])}"
                            if g["under_relic"] else ""))
        else:
            bits = [f"{g['met']}/{g['total']} prereqs met"]
            if g["missing"]:
                bits.append("missing: " + ", ".join(g["missing"]))
            if g["under_relic"]:
                bits.append("under-relic: " + ", ".join(
                    f"{u['name']} R{u['have']}<R{u['need']}" for u in g["under_relic"]))
            lines.append(f"- {g['name']}: " + "; ".join(bits))
    tok = {k: v for k, v in (tokens or {}).items() if str(v).strip()}
    token_block = ("\nCurrent token balances (spend advice): "
                   + json.dumps(tok) + "\n") if tok else ""
    meta_block = ("\nWhat's meta/current right now (recent community content):\n"
                  + "\n".join(f"- {t}" for t in meta_titles) + "\n") if meta_titles else ""
    return (
        "You are an elite Star Wars: Galaxy of Heroes progression coach. Using the "
        "COMPUTED facts below (do not contradict them), give a PRIORITIZED, specific "
        "action plan to progress most efficiently, ranked by impact-per-effort. Cover: "
        "(1) the fastest Galactic Legend to unlock/finish and the exact next units to "
        "unlock/gear/relic; (2) the 2-3 highest-impact gear/relic/team moves next; "
        "(3) the most efficient farming focus; (4) if token balances are given, what to "
        "spend them on. Be concise and specific to THIS roster.\n\n"
        f"ROSTER SUMMARY:\n{roster_summary}\n\n"
        f"GALACTIC LEGEND READINESS (computed from the real roster, closest first):\n"
        + "\n".join(lines) + "\n"
        + token_block + meta_block
    )


def generate_plan(roster, goals, tokens, meta_titles, gemini_call, key):
    units = roster.get("units", [])
    chars = sorted([u for u in units if u.get("type") == "character"],
                   key=lambda x: x.get("power") or 0, reverse=True)
    summary = (f"{roster.get('name')} — GP {roster.get('galactic_power')}, "
               f"{len(chars)} characters. Top: "
               + ", ".join(u.get("name", u["base_id"]) for u in chars[:30]) + ".")
    readiness = gl_readiness(units, goals)
    prompt = build_plan_prompt(summary, readiness, tokens, meta_titles)
    return gemini_call(prompt, key)
