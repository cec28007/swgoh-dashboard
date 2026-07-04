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
KNOWLEDGE_PATH = os.path.join(HERE, "knowledge.json")


def load_goals(path=GOALS_PATH):
    with open(path) as f:
        return json.load(f).get("galactic_legends", [])


def load_knowledge(path=KNOWLEDGE_PATH):
    """The living meta layer (farming/gear/what's-meta). {} if absent."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _knowledge_block(knowledge):
    """Render the meta layer as prompt lines; '' when there's nothing useful."""
    if not knowledge:
        return ""
    sections = [
        ("progression_strategy", "How to progress efficiently (authoritative community guidance)"),
        ("acceleration_tips", "High-value acceleration tips (actionable)"),
        ("farming_priorities", "Farming priorities"),
        ("gear_relic_guidance", "Gear/relic guidance"),
        ("whats_meta", "What's meta right now"),
    ]
    out = []
    for key, label in sections:
        items = [i.get("text") for i in knowledge.get(key, []) if i.get("text")]
        if items:
            out.append(f"{label}: " + "; ".join(items))
    if not out:
        return ""
    return ("\nLIVING META KNOWLEDGE (community-sourced, factor this in):\n"
            + "\n".join(f"- {line}" for line in out) + "\n")


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
            else:
                have_r = u.get("relic") or 0
                have_s = u.get("stars") or 0
                need_r = r.get("relic", 0)
                reason = []
                if have_s < 7:
                    reason.append("stars")
                if have_r < need_r:
                    reason.append("relic")
                if not reason:
                    met += 1
                else:
                    under.append({"name": r["name"], "have": have_r,
                                  "need": need_r, "stars": have_s,
                                  "reason": reason})
        out.append({
            "id": gl["id"], "name": gl["name"],
            "unlocked": gl["id"] in by_id,
            "met": met, "total": len(reqs),
            "missing": missing, "under_relic": under,
            "gaps": len(missing) + len(under),
        })
    out.sort(key=lambda x: (x["unlocked"], x["gaps"]))
    return out


def build_plan_prompt(roster_summary, readiness, tokens, meta_titles, knowledge=None):
    owned = [g["name"] for g in readiness if g["unlocked"]]
    lines = []
    for g in readiness:
        if g["unlocked"]:
            continue
        bits = [f"{g['met']}/{g['total']} requirements met"]
        if g["missing"]:
            bits.append("don't own: " + ", ".join(g["missing"]))
        if g["under_relic"]:
            parts = []
            for u in g["under_relic"]:
                gap = []
                if "stars" in u.get("reason", []):
                    gap.append(f"{u.get('stars', 0)}★→7★")
                if "relic" in u.get("reason", []):
                    gap.append(f"R{u['have']}→R{u['need']}")
                parts.append(f"{u['name']} ({', '.join(gap)})")
            bits.append("below target: " + ", ".join(parts))
        lines.append(f"- {g['name']}: " + "; ".join(bits))
    tok = {k: v for k, v in (tokens or {}).items() if str(v).strip()}
    token_block = ("\nCurrent token balances (spend advice): "
                   + json.dumps(tok) + "\n") if tok else ""
    meta_block = ("\nWhat's meta/current right now (recent community content):\n"
                  + "\n".join(f"- {t}" for t in meta_titles) + "\n") if meta_titles else ""
    owned_block = ("Galactic Legends already UNLOCKED: " + ", ".join(owned) + ".\n"
                   if owned else "No Galactic Legends unlocked yet.\n")
    return (
        "You are an elite Star Wars: Galaxy of Heroes progression coach. The roster "
        "facts below are accurate — do not contradict the roster data. Give a "
        "PRIORITIZED, specific action plan to progress most efficiently, ranked by "
        "impact-per-effort. Cover: (1) the most realistic next Galactic Legend (the one "
        "closest by the requirements below) and the specific units to unlock/gear/relic; "
        "(2) the 2-3 highest-impact gear/relic/team moves; (3) efficient farming focus; "
        "(4) if token balances are given, what to spend them on. Be concise and specific.\n\n"
        "The GL requirement lists below are a curated best-effort of each GL's FULL "
        "prerequisite roster (units at 7 stars + the listed relic). They may be slightly "
        "incomplete or off — if you know a listed GL needs a unit missing here, or a relic "
        "target looks wrong, mention it briefly, but otherwise base your advice on this "
        "readiness. Do not overstate how close a GL is.\n\n"
        f"ROSTER SUMMARY:\n{roster_summary}\n\n"
        + owned_block
        + "GALACTIC LEGEND REQUIREMENT READINESS (your roster vs each GL's full prereqs, closest first):\n"
        + ("\n".join(lines) if lines else "- (all listed GLs already unlocked)") + "\n"
        + token_block + meta_block + _knowledge_block(knowledge)
    )


def priorities_text(roster, goals, tokens):
    """A compact summary of the player's progression priorities (for store advice)."""
    units = roster.get("units", [])
    readiness = gl_readiness(units, goals)
    owned = [g["name"] for g in readiness if g["unlocked"]]
    unstarted = [g["name"] for g in readiness if not g["unlocked"]]
    lines = [f"Player {roster.get('name')}, GP {roster.get('galactic_power')}."]
    if owned:
        lines.append("Owned Galactic Legends: " + ", ".join(owned) + ".")
    if unstarted:
        lines.append("Not yet unlocked: " + ", ".join(unstarted)
                     + " (each needs a large faction roster at Relic 5+; use their full "
                       "requirements — don't assume any is nearly done).")
    tok = {k: v for k, v in (tokens or {}).items() if str(v).strip()}
    if tok:
        lines.append("Current token balances: " + json.dumps(tok) + ".")
    chars = sorted([u for u in units if u.get("type") == "character"],
                   key=lambda x: x.get("power") or 0, reverse=True)
    lines.append("Top units: "
                 + ", ".join(u.get("name", u["base_id"]) for u in chars[:20]) + ".")
    return " ".join(lines)


def store_prompt(priorities):
    return (
        "You are a Star Wars: Galaxy of Heroes store advisor. The attached "
        "screenshot(s) show an in-game store or offers. Read EVERY item you can see "
        "across all images. For each item, output one markdown table row with columns: "
        "Item | Cost | Verdict | Why. Verdict is Buy, Maybe, or Skip. 'Why' is one short "
        "sentence tied to the player's priorities below (e.g. a unlock prereq they still "
        "need, a bottleneck material, already-maxed unit, or better use of the currency). "
        "Start with the table header row. Be specific to THIS player.\n\n"
        f"PLAYER PRIORITIES:\n{priorities}"
    )


def generate_plan(roster, goals, tokens, meta_titles, gemini_call, key,
                  knowledge=None):
    if knowledge is None:
        knowledge = load_knowledge()
    units = roster.get("units", [])
    chars = sorted([u for u in units if u.get("type") == "character"],
                   key=lambda x: x.get("power") or 0, reverse=True)
    summary = (f"{roster.get('name')} — GP {roster.get('galactic_power')}, "
               f"{len(chars)} characters. Top: "
               + ", ".join(u.get("name", u["base_id"]) for u in chars[:30]) + ".")
    readiness = gl_readiness(units, goals)
    prompt = build_plan_prompt(summary, readiness, tokens, meta_titles, knowledge)
    return gemini_call(prompt, key)
