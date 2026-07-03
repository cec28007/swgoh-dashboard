#!/usr/bin/env python3
"""Per-unit stat + GP computation via the swgoh-stats (Crinolo) service.

comlink's /player has no computed stats or per-unit GP. The swgoh-stats service
(same Docker network) takes the rosterUnit array and returns each unit with a
`.gp` and a full final-stats block (Speed, Health, Offense, ...). Zero deps.
"""
import json
import os
import urllib.request

STATS_URL = os.environ.get("STATS_URL", "http://swgoh-stats:3223")


def fetch_stats(roster_units, url=None, flags="gameStyle,calcGP"):
    """POST the rosterUnit array to swgoh-stats; return units with gp + stats."""
    base = (url or STATS_URL).rstrip("/")
    req = urllib.request.Request(
        f"{base}/api?flags={flags}",
        data=json.dumps(roster_units).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_stats_map(stats_units):
    """unit id -> {gp, stats} where stats is the final (in-game) stat block."""
    out = {}
    for u in stats_units:
        uid = u.get("id") or u.get("definitionId")
        if not uid:
            continue
        raw = u.get("stats") or {}
        final = raw.get("final") if isinstance(raw, dict) and "final" in raw else raw
        out[uid] = {"gp": u.get("gp"), "stats": final or {}}
    return out
