#!/usr/bin/env python3
"""Pull a SWGOH player roster and write it to swgoh_data.js.

Mirrors the Tesla pipeline (fetch_tesla.py -> data.js): a small, dependency-free
script that grabs your profile and normalizes it into a flat shape the static
dashboard (swgoh.html) can render. Run it by hand, or on a daily timer from the
Oracle Always-Free VM (see docs/swgoh.md).

Sources:
  comlink real pull from the local swgoh-comlink service (official game API;
          use this on the Oracle box — swgoh.gg's public API is now blocked)
  swgoh   real pull from the free swgoh.gg public API (now 403s; kept for ref)
  mock    canned roster for testing the pipeline with no network/credentials

Usage:
  python3 fetch_swgoh.py mock --dry-run     # try it offline, print, don't write
  python3 fetch_swgoh.py swgoh              # real pull -> swgoh_data.js
  python3 fetch_swgoh.py swgoh --ally 611121817

Ally code resolution order: --ally flag, then SWGOH_ALLY_CODE env var, then
the `ally_code` already saved in swgoh_data.js, then the built-in default.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date

import gamedata
import stats as stats_svc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "swgoh_data.js")
GAMEDATA_CACHE = os.path.join(HERE, "gamedata_cache.json")
DEFAULT_ALLY = "611121817"
SWGOH_GG = "https://swgoh.gg/api/player/{ally}/"
# Local swgoh-comlink service (same Docker network on the Oracle box). Talks to
# the official game API, so it works where swgoh.gg's public API is now blocked.
COMLINK_URL = os.environ.get("COMLINK_URL", "http://swgoh-comlink:3000")

# swgoh.gg encodes combat type and offsets relic tier; normalize both here.
COMBAT_CHARACTER, COMBAT_SHIP = 1, 2


def clean_ally(code):
    return re.sub(r"\D", "", str(code or ""))


def resolve_ally(cli_ally):
    if cli_ally:
        return clean_ally(cli_ally)
    if os.environ.get("SWGOH_ALLY_CODE"):
        return clean_ally(os.environ["SWGOH_ALLY_CODE"])
    existing = read_existing()
    if existing.get("ally_code"):
        return clean_ally(existing["ally_code"])
    return DEFAULT_ALLY


def read_existing():
    try:
        with open(OUT, "r") as f:
            txt = f.read()
        m = re.search(r"window\.SWGOH_DATA\s*=\s*(\{.*\})\s*;?\s*$", txt, re.S)
        if m:
            return json.loads(m.group(1))
    except (OSError, ValueError):
        pass
    return {}


def http_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) swgoh-dashboard",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def relic_level(unit):
    """swgoh.gg relic_tier: 1 = locked, 2 = relic 0, 3 = relic 1 ... so -2."""
    rt = unit.get("relic_tier")
    if rt is None:
        return 0
    return max(0, rt - 2)


def normalize_swgoh_gg(payload):
    data = payload.get("data", {})
    units = []
    for u in payload.get("units", []):
        d = u.get("data", u)
        units.append(
            {
                "base_id": d.get("base_id"),
                "name": d.get("name"),
                "type": "ship" if d.get("combat_type") == COMBAT_SHIP else "character",
                "stars": d.get("rarity", 0),
                "level": d.get("level", 0),
                "gear_level": d.get("gear_level", 0),
                "relic": relic_level(d),
                "power": d.get("power", 0),
                "zetas": len(d.get("zeta_abilities", []) or []),
                "omicrons": len(d.get("omicron_abilities", []) or []),
                "url": ("https://swgoh.gg" + d["url"]) if d.get("url") else None,
            }
        )
    units.sort(key=lambda x: x.get("power", 0), reverse=True)
    return {
        "name": data.get("name"),
        "ally_code": clean_ally(data.get("ally_code")),
        "level": data.get("level"),
        "guild_name": data.get("guild_name"),
        "galactic_power": data.get("galactic_power"),
        "character_gp": data.get("character_galactic_power"),
        "ship_gp": data.get("ship_galactic_power"),
        "last_updated": date.today().isoformat(),
        "source": "swgoh.gg",
        "units": units,
    }


def _comlink_is_ship(u):
    """1 = character, 2 = ship. Fallback: no relic + no equipment => ship."""
    ct = u.get("combatType")
    if ct in (1, 2, "1", "2", "COMBAT_TYPE_CHAR", "COMBAT_TYPE_SHIP"):
        return ct in (2, "2", "COMBAT_TYPE_SHIP")
    return not u.get("relic") and not u.get("equipment")


def _comlink_relic(u):
    """comlink relic.currentTier: 1/2 = no relic, 3 = relic 1 ... so -2."""
    ct = (u.get("relic") or {}).get("currentTier")
    return max(0, (ct or 0) - 2)


def _est_power(stars, gear_level, relic, level, is_ship):
    """Rough GP-ish estimate. comlink /player has no per-unit GP, so we derive a
    monotonic value for sorting/ranking. Ballpark, not exact."""
    if is_ship:
        return stars * 4000 + level * 80
    return stars * 3000 + gear_level * 1500 + relic * 2000 + level * 50


def normalize_comlink(payload, maps=None, stats_map=None):
    """Convert a comlink /player payload into the dashboard roster shape.

    `maps` (gamedata.load_or_refresh) adds real names + zeta/omicron counts.
    `stats_map` (stats.build_stats_map) adds real per-unit GP, Speed, and the
    full final-stat block. Without them, names fall back to base IDs, zeta/omi
    are 0, and power is an estimate.
    """
    names = (maps or {}).get("names", {})
    skill_map = (maps or {}).get("skills", {})
    stats_map = stats_map or {}
    units = []
    for u in payload.get("rosterUnit", []):
        base_id = (u.get("definitionId") or "").split(":")[0]
        is_ship = _comlink_is_ship(u)
        stars = int(u.get("currentRarity") or 0)
        tier = int(u.get("currentTier") or 0)
        level = int(u.get("currentLevel") or 0)
        relic = 0 if is_ship else _comlink_relic(u)
        gear_level = 0 if is_ship else tier
        zetas, omis = (gamedata.count_zeta_omi(u.get("skill", []), skill_map)
                       if skill_map else (0, 0))
        st = stats_map.get(u.get("id"))
        if st:
            power = int(st.get("gp") or 0)
            unit_stats = st.get("stats") or {}
            speed = unit_stats.get("Speed")
        else:
            power = _est_power(stars, gear_level, relic, level, is_ship)
            unit_stats = {}
            speed = None
        units.append({
            "base_id": base_id,
            "name": names.get(base_id, base_id),
            "type": "ship" if is_ship else "character",
            "stars": stars, "level": level, "gear_level": gear_level,
            "relic": relic, "power": power, "speed": speed,
            "zetas": zetas, "omicrons": omis, "stats": unit_stats, "url": None,
        })
    units.sort(key=lambda x: x["power"], reverse=True)

    gp = gamedata.gp_totals(payload.get("profileStat", []))
    est_char = sum(u["power"] for u in units if u["type"] == "character")
    est_ship = sum(u["power"] for u in units if u["type"] == "ship")
    galactic = gp["galactic_power"] if gp["galactic_power"] is not None else est_char + est_ship
    char_gp = gp["character_gp"] if gp["character_gp"] is not None else est_char
    ship_gp = gp["ship_gp"] if gp["ship_gp"] is not None else est_ship

    # Only estimates need scaling. With real per-unit GP (stats_map) the Power
    # column already sums to the real totals; scale only when we fell back to
    # estimates but do know the real GP totals from profileStat.
    if not stats_map and gp["character_gp"] is not None and est_char:
        for u in units:
            if u["type"] == "character":
                u["power"] = round(u["power"] / est_char * gp["character_gp"])
    if not stats_map and gp["ship_gp"] is not None and est_ship:
        for u in units:
            if u["type"] == "ship":
                u["power"] = round(u["power"] / est_ship * gp["ship_gp"])
    return {
        "name": payload.get("name"),
        "ally_code": clean_ally(payload.get("allyCode")),
        "level": payload.get("level"),
        "guild_name": payload.get("guildName"),
        "galactic_power": galactic,
        "character_gp": char_gp,
        "ship_gp": ship_gp,
        "last_updated": date.today().isoformat(),
        "source": "comlink",
        "units": units,
    }


def fetch_comlink(ally, url=None):
    base = (url or COMLINK_URL).rstrip("/")
    body = json.dumps({"payload": {"allyCode": clean_ally(ally)}, "enums": False}).encode()
    req = urllib.request.Request(
        base + "/player", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def mock_roster(ally):
    units = [
        # name, type, stars, gear, relic, power, zetas, omicrons
        ("Jedi Knight Luke Skywalker", "character", 7, 13, 9, 78450, 3, 1),
        ("Supreme Leader Kylo Ren", "character", 7, 13, 8, 75210, 4, 2),
        ("Grand Master Yoda", "character", 7, 13, 7, 61230, 2, 0),
        ("Darth Vader", "character", 7, 13, 6, 58900, 3, 0),
        ("Rey", "character", 7, 12, 0, 41200, 2, 1),
        ("Executor", "ship", 7, 0, 0, 88200, 0, 0),
        ("Home One", "ship", 7, 0, 0, 52100, 0, 0),
        ("Millennium Falcon", "ship", 7, 0, 0, 47800, 0, 0),
    ]
    out = []
    for nm, tp, st, gl, rl, pw, ze, om in units:
        out.append(
            {
                "base_id": re.sub(r"\W+", "_", nm).upper(),
                "name": nm, "type": tp, "stars": st, "level": 85,
                "gear_level": gl, "relic": rl, "power": pw,
                "zetas": ze, "omicrons": om, "url": None,
            }
        )
    char_gp = sum(u["power"] for u in out if u["type"] == "character")
    ship_gp = sum(u["power"] for u in out if u["type"] == "ship")
    return {
        "name": "Mock Commander", "ally_code": ally, "level": 85,
        "guild_name": "Sample Guild", "galactic_power": char_gp + ship_gp,
        "character_gp": char_gp, "ship_gp": ship_gp,
        "last_updated": date.today().isoformat(), "source": "mock",
        "units": out,
    }


def write_out(roster):
    body = json.dumps(roster, indent=2, ensure_ascii=False)
    with open(OUT, "w") as f:
        f.write("// Auto-generated by fetch_swgoh.py — do not edit by hand.\n")
        f.write("window.SWGOH_DATA = " + body + ";\n")


def main(argv):
    source = "swgoh"
    ally_arg = None
    dry = False
    it = iter(argv)
    for a in it:
        if a in ("swgoh", "mock", "comlink"):
            source = a
        elif a == "--dry-run":
            dry = True
        elif a == "--ally":
            ally_arg = next(it, None)
        elif a.startswith("--ally="):
            ally_arg = a.split("=", 1)[1]

    ally = resolve_ally(ally_arg)
    if source == "mock":
        roster = mock_roster(ally)
    elif source == "comlink":
        try:
            payload = fetch_comlink(ally)
        except Exception as e:  # noqa: BLE001 - surface a clear message and exit
            print(f"ERROR fetching comlink for ally {ally}: {e}", file=sys.stderr)
            return 1
        try:
            maps = gamedata.load_or_refresh(GAMEDATA_CACHE)
        except Exception as e:  # noqa: BLE001 - names/zetas are enrichment, not fatal
            print(f"WARN game-data enrichment unavailable ({e}); using base IDs",
                  file=sys.stderr)
            maps = None
        try:
            stats_map = stats_svc.build_stats_map(
                stats_svc.fetch_stats(payload.get("rosterUnit", [])))
        except Exception as e:  # noqa: BLE001 - stats are enrichment, not fatal
            print(f"WARN stats service unavailable ({e}); GP/speed estimated",
                  file=sys.stderr)
            stats_map = None
        roster = normalize_comlink(payload, maps, stats_map)
    else:
        try:
            payload = http_json(SWGOH_GG.format(ally=ally))
        except Exception as e:  # noqa: BLE001 - surface a clear message and exit
            print(f"ERROR fetching swgoh.gg for ally {ally}: {e}", file=sys.stderr)
            return 1
        roster = normalize_swgoh_gg(payload)

    chars = sum(1 for u in roster["units"] if u["type"] == "character")
    ships = sum(1 for u in roster["units"] if u["type"] == "ship")
    print(
        f"{roster.get('name')} ({roster.get('ally_code')}) — "
        f"GP {roster.get('galactic_power'):,} | {chars} characters, {ships} ships "
        f"[source: {roster['source']}]"
    )
    if dry:
        print("--dry-run: not writing swgoh_data.js")
        return 0
    write_out(roster)
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
