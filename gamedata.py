#!/usr/bin/env python3
"""Game-data enrichment for the SWGOH dashboard (zero-dependency, stdlib only).

comlink's /player gives roster facts (rarity, gear, relic) but only internal IDs
and no zeta/omicron flags or display names. This module pulls the game's own data
+ localization from the local comlink and builds the lookup maps that turn a raw
roster into a fully-labelled one:

  base_id            -> display name        (game data units + localization)
  skill_id           -> zeta/omicron tiers  (game data skill collection)

The maps are cached to a JSON file and only refreshed when the game version
changes (checked cheaply via /metadata).
"""
import json
import os
import urllib.request

COMLINK_URL = os.environ.get("COMLINK_URL", "http://swgoh-comlink:3000")

_GP_KEYS = {
    "galactic_power": "STAT_GALACTIC_POWER_ACQUIRED_NAME",
    "character_gp": "STAT_CHARACTER_GALACTIC_POWER_ACQUIRED_NAME",
    "ship_gp": "STAT_SHIP_GALACTIC_POWER_ACQUIRED_NAME",
}


# ---- pure transforms (unit-tested) --------------------------------------

def parse_localization(text):
    """Parse a `KEY|Value` localization blob into a dict, skipping comments."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "|" not in line:
            continue
        key, val = line.split("|", 1)
        out[key] = val
    return out


def build_name_map(units, loc):
    """base_id -> localized display name (falls back to base_id)."""
    names = {}
    for u in units:
        base_id = u.get("baseId") or u.get("id")
        if not base_id:
            continue
        names[base_id] = loc.get(u.get("nameKey", ""), base_id)
    return names


def build_icon_map(units):
    """base_id -> portrait thumbnail key (for game-assets.swgoh.gg/textures/<key>.png)."""
    out = {}
    for u in units:
        base_id = u.get("baseId") or u.get("id")
        thumb = u.get("thumbnailName")
        if base_id and thumb:
            out[base_id] = thumb
    return out


def build_skill_map(skills):
    """skill_id -> {zeta_tier, omicron_tier} as 0-based tier indices.

    A /player skill's `tier` is 0-based (0 = un-upgraded, N-1 = a maxed N-tier
    skill), so we store the matching 0-based index of the zeta/omicron tier.
    """
    out = {}
    for s in skills:
        zeta = omi = None
        for i, tier in enumerate(s.get("tier", [])):
            if zeta is None and tier.get("isZetaTier"):
                zeta = i
            if omi is None and tier.get("isOmicronTier"):
                omi = i
        out[s.get("id")] = {"zeta_tier": zeta, "omicron_tier": omi}
    return out


def count_zeta_omi(unit_skills, skill_map):
    """Count applied zetas/omicrons for one unit's skills against the skill map."""
    zetas = omis = 0
    for sk in unit_skills:
        info = skill_map.get(sk.get("id"))
        if not info:
            continue
        tier = int(sk.get("tier") or 0)
        if info["zeta_tier"] is not None and tier >= info["zeta_tier"]:
            zetas += 1
        if info["omicron_tier"] is not None and tier >= info["omicron_tier"]:
            omis += 1
    return zetas, omis


def gp_totals(profile_stat):
    """Pull real galactic-power totals from a /player profileStat list."""
    by_key = {s.get("nameKey"): s.get("value") for s in profile_stat}
    out = {}
    for field, key in _GP_KEYS.items():
        v = by_key.get(key)
        out[field] = int(v) if v not in (None, "") else None
    return out


# ---- network + cache (glue) ---------------------------------------------

def _post(url, endpoint, payload, timeout=180):
    req = urllib.request.Request(
        f"{url.rstrip('/')}/{endpoint}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_maps(comlink_url=None, poster=_post):
    """Build {version, names, skills} from comlink game data + localization."""
    url = comlink_url or COMLINK_URL
    meta = poster(url, "metadata", {})
    version = meta.get("latestGamedataVersion")
    loc_bundle = meta.get("latestLocalizationBundleVersion")

    units = poster(url, "data", {"payload": {
        "version": version, "includePveUnits": False, "requestSegment": 3}}).get("units", [])
    skills = poster(url, "data", {"payload": {
        "version": version, "includePveUnits": False, "requestSegment": 1}}).get("skill", [])
    loc_blob = poster(url, "localization", {"payload": {"id": loc_bundle}, "unzip": True})
    loc = parse_localization(loc_blob.get("Loc_ENG_US.txt", ""))

    return {
        "version": version,
        "names": build_name_map(units, loc),
        "skills": build_skill_map(skills),
        "icons": build_icon_map(units),
    }


def load_or_refresh(cache_path, comlink_url=None, poster=_post):
    """Return maps from cache when the game version is unchanged, else refresh."""
    url = comlink_url or COMLINK_URL
    cached = None
    try:
        with open(cache_path) as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = None

    current = poster(url, "metadata", {}).get("latestGamedataVersion")
    if cached and cached.get("version") == current:
        return cached

    maps = fetch_maps(url, poster=poster)
    try:
        with open(cache_path, "w") as f:
            json.dump(maps, f)
    except OSError:
        pass
    return maps
