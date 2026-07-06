#!/usr/bin/env python3
"""Auto-refresh the SWGOH knowledge base (zero-dependency, stdlib only).

Retrieves current GL requirements + community meta via Tavily (whose servers
reach swgoh.gg content the Oracle IP is 403'd from), synthesizes them into our
schema with Gemini, and writes a *proposal* (goals.proposed.json /
knowledge.proposed.json + KNOWLEDGE_CHANGELOG.md) for human approval. Nothing
touches the live files until `--promote`.

Design: docs/knowledge-refresh-design.md. Retrieval and model calls are injected
as callables so the logic is testable without network.
"""
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.request
from datetime import date

# Reuse the proven Gemini URL + JSON-extraction + YouTube search plumbing.
from digest import GEMINI_URL, ASK_MODEL, _extract_json, search_youtube_api

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS_PATH = os.path.join(HERE, "goals.json")
KNOWLEDGE_PATH = os.path.join(HERE, "knowledge.json")
META_TEAMS_PATH = os.path.join(HERE, "meta_teams.json")
GAMEDATA_CACHE = os.path.join(HERE, "gamedata_cache.json")

# Squads/fleets that differ from an existing id by more than this many members
# are a genuinely different team, not a refresh of the same one — kept as a
# separate variant rather than silently overwriting a verified entry. (The
# GL Leia Old-Ben/Kanan vs Jyn/Raddus case is exactly this: 4 of 5 differ.)
VARIANT_DIFF_THRESHOLD = 2

TEAM_DOMAINS = ["swgoh.gg", "swgohevents.com", "gaming-fans.com", "allclash.com"]

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_API_KEY_ENV = "TAVILY_API_KEY"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

# Domains worth trusting for hard requirement data; Tavily searches from its own
# servers so these resolve even though swgoh.gg 403s our datacenter IP directly.
REQUIREMENT_DOMAINS = ["swgoh.gg", "swgohevents.com", "gamerofthegalaxy.com"]

# Meta queries not tied to a specific GL: (kind, query, domains). domains=None
# means open web (Tavily ranks freely); a list locks the query to those sites.
# Tavily reaches reddit.com even though our datacenter IP is 403'd hitting it.
META_QUERIES = [
    ("meta", "SWGOH current meta teams and counters 2026", None),
    ("farming", "SWGOH gear and relic material farming priority guide 2026", None),
    ("new_gl", "SWGOH newest Galactic Legend requirements 2026", REQUIREMENT_DOMAINS),
    ("tips", "SWGOH efficiency tips tricks to accelerate progression f2p 2026", None),
    ("strategy", "SWGOH progression strategy energy crystal efficiency farming "
     "priority roadmap what to focus on mid game", None),
    ("mechanics", "SWGOH Era system Coliseum Era Levels Episode Track how it works "
     "2026 game mechanics guide", None),
    ("community", "SWGOH best tips to progress faster and avoid mistakes",
     ["reddit.com", "gaming-fans.com"]),
]

# Search terms for the weekly "let Gemini watch a few guide videos" pass.
TIP_VIDEO_TERMS = [
    "SWGOH tips tricks accelerate progression 2026",
    "SWGOH f2p efficiency guide 2026",
    "SWGOH beginner mistakes to avoid",
]

WATCH_PROMPT = (
    "This is a Star Wars: Galaxy of Heroes video. Extract the CONCRETE, ACTIONABLE "
    "tips a player could use to accelerate progression — efficiency tricks, farming "
    "routes/priorities, resource and energy management, event/raid strategy, common "
    "mistakes to avoid. Skip intros, sponsors, and filler. Output each tip as one "
    "short, COMPLETE, self-contained bullet line starting with '- '. Do NOT output "
    "section headings or category labels (a line that's just a name ending in ':'); "
    "fold any needed context into the tip itself. If the video has no such tips, "
    "output nothing."
)

MAX_RELIC = 9
_YT_RE = re.compile(r"^https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+")


def is_youtube_url(url):
    return bool(_YT_RE.match(url or ""))


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
def build_research_queries(goals, meta_queries=META_QUERIES):
    """One requirements query per GL, plus the meta queries. Deterministic.

    Each query carries an optional `domains` whitelist: requirement queries are
    locked to the trusted sites; meta queries pick their own (open web or a
    specific community site).
    """
    out = []
    for gl in goals:
        out.append({
            "kind": "requirements",
            "gl_id": gl.get("id"),
            "query": (f"{gl.get('name')} SWGOH Galactic Legend unlock "
                      "requirements characters relic levels"),
            "domains": REQUIREMENT_DOMAINS,
        })
    for kind, q, domains in meta_queries:
        out.append({"kind": kind, "gl_id": None, "query": q, "domains": domains})
    return out


def _tavily_post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tavily_search(query, key, include_domains=None, max_results=5,
                  poster=_tavily_post):
    """One Tavily search; returns [{title, url, content}]. [] on failure."""
    payload = {"api_key": key, "query": query, "search_depth": "advanced",
               "max_results": max_results, "include_answer": False}
    if include_domains:
        payload["include_domains"] = include_domains
    try:
        data = poster(TAVILY_URL, payload)
    except Exception:  # noqa: BLE001 - one failed search shouldn't sink the run
        return []
    out = []
    for r in data.get("results", []):
        if r.get("url"):
            out.append({"title": r.get("title", ""), "url": r["url"],
                        "content": (r.get("content") or "")[:1500]})
    return out


def gather(queries, key, searcher=tavily_search, per_query=5):
    """Run every query, attach its results, honoring each query's domain focus."""
    out = []
    for q in queries:
        results = searcher(q["query"], key, include_domains=q.get("domains"),
                           max_results=per_query)
        out.append({**q, "results": results})
    return out


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #
def build_synthesis_prompt(goals, knowledge, gathered):
    research_lines = []
    for g in gathered:
        tag = g["gl_id"] or g["kind"]
        for r in g.get("results", []):
            research_lines.append(f"[{tag}] {r['title']} :: {r['url']}\n{r['content']}")
    return (
        "You maintain a Star Wars: Galaxy of Heroes knowledge base. Using the "
        "RESEARCH below, produce an UPDATED version of the two data structures.\n\n"
        "HARD RULES:\n"
        "- Use ONLY `base_id` values that already appear in CURRENT GOALS. NEVER "
        "invent a base_id. If research suggests a NEW unit belongs to a GL but its "
        "base_id isn't already in CURRENT GOALS, do NOT add it to requirements — "
        "instead add a note to `changes` like 'VERIFY: SEE may now need <unit> — "
        "add base_id manually'.\n"
        "- GL unlock requirements are CHARACTERS only. Never add ships/fleets.\n"
        "- Preserve each GL's existing `id` exactly.\n"
        "- Relic values are integers 0-9 (never null). `stars` gate is always 7.\n"
        "- Stamp every changed/added requirement with source (a url from the "
        "research) and confidence (high|medium|low). Leave unchanged facts as-is.\n"
        "- For knowledge (progression_strategy, acceleration_tips, "
        "farming_priorities, gear_relic_guidance, whats_meta), each item is "
        "{text, source, confidence}. Refresh whats_meta with what the research shows "
        "is current. Populate acceleration_tips from any [youtube_tips], [tips], or "
        "[community] research with CONCRETE actionable tips. Maintain "
        "progression_strategy from [strategy]/[community] research as DURABLE, "
        "high-level 'how to progress efficiently' guidance (energy/crystal spend, "
        "farming priority, account-stage direction) — keep existing entries and add "
        "distinct ones; prefer authoritative sources. Maintain game_mechanics from "
        "[mechanics]/[strategy] research as durable explanations of how the game's "
        "systems work (classic gear/relic vs the ERA/Coliseum/Episode track and how "
        "they interconnect) — keep existing entries and add distinct ones.\n\n"
        "Reply with ONLY JSON, no prose:\n"
        '{"goals": [<same shape as CURRENT GOALS galactic_legends>], '
        '"knowledge": {"progression_strategy":[...], "game_mechanics":[...], '
        '"acceleration_tips":[...], '
        '"farming_priorities":[...], "gear_relic_guidance":[...], "whats_meta":[...], '
        '"sources":[...]}, '
        '"changes": ["<one short human sentence per change>"]}\n\n'
        f"CURRENT GOALS:\n{json.dumps(goals, ensure_ascii=False)}\n\n"
        f"CURRENT KNOWLEDGE:\n{json.dumps(knowledge, ensure_ascii=False)}\n\n"
        f"RESEARCH:\n" + "\n\n".join(research_lines)
    )


def gemini_generate_large(prompt, key, max_tokens=32768):
    """Like digest.gemini_generate but with a big output budget — the full
    goals+knowledge JSON blows past the digest's 8192-token cap and truncates."""
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": max_tokens}}
    url = f"{GEMINI_URL}/{ASK_MODEL}:generateContent"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"x-goog-api-key": key,
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    cands = out.get("candidates", [])
    parts = cands[0].get("content", {}).get("parts", []) if cands else []
    return "".join(p.get("text", "") for p in parts)


def _gemini_watch(url, prompt, key):
    """Have Gemini watch a YouTube video (its servers fetch it — sidesteps our
    datacenter IP block on captions) and return the extracted text."""
    # Gemini 2.5 spends "thinking" tokens against this budget before the tips,
    # so keep it generous or the last tip truncates mid-sentence.
    payload = {"contents": [{"parts": [
                   {"file_data": {"file_uri": url}}, {"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": 8192}}
    req = urllib.request.Request(
        f"{GEMINI_URL}/{ASK_MODEL}:generateContent",
        data=json.dumps(payload).encode(),
        headers={"x-goog-api-key": key, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    cands = out.get("candidates", [])
    parts = cands[0].get("content", {}).get("parts", []) if cands else []
    return "".join(p.get("text", "") for p in parts)


def watch_video(url, key, caller=_gemini_watch):
    """Extract acceleration tips from one YouTube video. Rejects non-YouTube urls."""
    if not is_youtube_url(url):
        raise ValueError("only YouTube video links can be watched")
    return caller(url, WATCH_PROMPT, key)


def gather_video_tips(terms, yt_key, gem_key, searcher=search_youtube_api,
                      watcher=watch_video, limit=3):
    """Find recent guide videos and have Gemini extract tips from the top few.
    [] when either key is missing (fails soft — video tips are a bonus source)."""
    if not yt_key or not gem_key:
        return []
    videos = searcher(terms, yt_key)[:limit]
    out = []
    for v in videos:
        url = v.get("url")
        if not url:
            continue
        try:
            content = watcher(url, gem_key)
        except Exception:  # noqa: BLE001 - one bad video shouldn't sink the run
            continue
        if content and content.strip():
            out.append({"kind": "youtube_tips", "gl_id": None,
                        "query": v.get("title", ""),
                        "results": [{"title": v.get("title", ""), "url": url,
                                     "content": content}]})
    return out


def _bullets(text):
    """Pull bullet/numbered lines out of a model reply as clean tip strings.

    Skips section-header fragments (a bold label ending in ':' with no body,
    e.g. '- **Captain Teva (Utility)**:') which aren't actual tips.
    """
    tips = []
    for line in (text or "").splitlines():
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if not s or s == line.strip():  # only lines that were actually bulleted
            continue
        plain = s.replace("*", "").replace("_", "").strip()
        if plain.endswith(":"):  # dangling header, no tip body
            continue
        tips.append(s)
    return tips


def parse_synthesis(text):
    """Extract the proposal JSON; require the goals + knowledge keys."""
    d = _extract_json(text)
    if "goals" not in d or "knowledge" not in d:
        raise ValueError("synthesis JSON missing 'goals' or 'knowledge'")
    d.setdefault("changes", [])
    return d


def synthesize(goals, knowledge, gathered, key, gemini_call=gemini_generate_large):
    prompt = build_synthesis_prompt(goals, knowledge, gathered)
    return parse_synthesis(gemini_call(prompt, key))


# --------------------------------------------------------------------------- #
# Validation + changelog
# --------------------------------------------------------------------------- #
def _req_ok(r, name_map):
    bid = r.get("base_id")
    relic = r.get("relic")
    if name_map and bid not in name_map:
        return False, f"unknown base_id {bid}"
    if not isinstance(relic, int) or not (0 <= relic <= MAX_RELIC):
        return False, f"bad relic {relic!r} for {bid}"
    return True, ""


def sanitize_proposed(proposed, name_map):
    """Drop requirements the model got wrong (hallucinated ids, ships with no
    relic) rather than rejecting the whole refresh. Returns (clean, dropped)."""
    clean = dict(proposed)
    clean_goals, dropped = [], []
    for g in proposed.get("goals", []):
        kept = []
        for r in g.get("requirements", []):
            ok, why = _req_ok(r, name_map)
            (kept if ok else dropped).append(r if ok else
                                             f"{g.get('id', '?')}: {r.get('name', r.get('base_id'))} — {why}")
        clean_goals.append({**g, "requirements": kept})
    clean["goals"] = clean_goals
    return clean, dropped


def validate_proposed(proposed, name_map):
    """Reject garbage before it becomes a proposal. [] means clean."""
    issues = []
    for g in proposed.get("goals", []):
        gid = g.get("id", "?")
        for r in g.get("requirements", []):
            ok, why = _req_ok(r, name_map)
            if not ok:
                issues.append(f"{gid}: {why}")
    return issues


def changelog(old_goals, new_goals):
    """Human-readable diff of GL requirement rosters (old vs proposed)."""
    old_by = {g["id"]: g for g in old_goals}
    lines = []
    for g in new_goals:
        gid, name = g["id"], g.get("name", g["id"])
        o = old_by.get(gid)
        if o is None:
            lines.append(f"{name}: new GL added ({len(g.get('requirements', []))} units)")
            continue
        old_r = {r["base_id"]: r for r in o.get("requirements", [])}
        new_r = {r["base_id"]: r for r in g.get("requirements", [])}
        for bid, r in new_r.items():
            if bid not in old_r:
                lines.append(f"{name}: +{r.get('name', bid)} (R{r.get('relic')})")
            elif old_r[bid].get("relic") != r.get("relic"):
                lines.append(f"{name}: {r.get('name', bid)} "
                             f"R{old_r[bid].get('relic')}→R{r.get('relic')}")
        for bid, r in old_r.items():
            if bid not in new_r:
                lines.append(f"{name}: -{r.get('name', bid)} (removed)")
    for gid, o in old_by.items():
        if gid not in {g["id"] for g in new_goals}:
            lines.append(f"{o.get('name', gid)}: GL removed")
    return lines


# --------------------------------------------------------------------------- #
# Propose / promote
# --------------------------------------------------------------------------- #
def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_name_map(path=GAMEDATA_CACHE):
    return _load(path, {}).get("names", {})


# --------------------------------------------------------------------------- #
# Team refresh — meta_teams.json (squads/fleets), same discipline as goals.json:
# gather -> synthesize (names, not base_ids — the model can't reliably produce
# our internal ids) -> resolve every name against real game data -> merge as a
# NEW VARIANT rather than overwrite when a proposal meaningfully differs from
# an existing id -> changelog -> promote. Previously meta_teams.json was
# hand-authored from memory with no review gate — this closes that gap.
# --------------------------------------------------------------------------- #
def _normalize_name(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _build_reverse_name_map(name_map):
    """normalized display name -> best base_id (prefers real playable units
    over event/raid/inherit variants, which share the same display name)."""
    def better(cur, new):
        cu, nu = ("_" in cur), ("_" in new)
        if cu != nu:
            return new if (cu and not nu) else cur
        return new if len(new) < len(cur) else cur
    rev = {}
    for bid, nm in name_map.items():
        key = _normalize_name(nm)
        rev[key] = bid if key not in rev else better(rev[key], bid)
    return rev


def resolve_unit_name(name, name_map, reverse_map=None):
    """A display name OR an already-valid base_id -> base_id, else None."""
    if name in name_map:
        return name
    rev = reverse_map if reverse_map is not None else _build_reverse_name_map(name_map)
    return rev.get(_normalize_name(name))


def build_team_queries(meta_teams):
    """Discovery queries (find new meta teams) plus one per existing squad/fleet
    (find its current best composition — the mechanism that would have caught
    the wrong GL Leia team if it existed from the start)."""
    out = [
        {"kind": "discovery", "query": "SWGOH best GAC meta squads 2026", "domains": TEAM_DOMAINS},
        {"kind": "discovery", "query": "SWGOH best fleet arena meta teams 2026", "domains": TEAM_DOMAINS},
    ]
    for s in meta_teams.get("squads", []):
        out.append({"kind": "squad", "query": f"{s['name']} SWGOH best team allies squad 2026",
                    "domains": TEAM_DOMAINS})
    for f in meta_teams.get("fleets", []):
        out.append({"kind": "fleet", "query": f"{f['name']} SWGOH fleet best ships 2026",
                    "domains": TEAM_DOMAINS})
    return out


def build_team_prompt(meta_teams, gathered):
    lines = []
    for g in gathered:
        for r in g.get("results", []):
            lines.append(f"[{g['kind']}] {r['title']} :: {r['url']}\n{r['content']}")
    return (
        "You maintain a Star Wars: Galaxy of Heroes team-composition reference. "
        "Using the RESEARCH below, propose current best squads/fleets.\n\n"
        "HARD RULES:\n"
        "- Express every member as its real, exact in-game CHARACTER OR SHIP NAME "
        "(e.g. 'Jyn Erso', not a made-up id). Never invent a name you didn't see "
        "in the research.\n"
        "- If a team you propose is a genuinely DIFFERENT composition from an "
        "existing squad of the same concept (e.g. an alternate ally lineup), give "
        "it a distinguishing name — do not just repeat the old name.\n"
        "- min_relic is the relic level the squad plays best at (integer 0-9).\n"
        "- Stamp source (a url from the research) and confidence (high|medium|low) "
        "on every squad/fleet.\n\n"
        "Reply with ONLY JSON, no prose:\n"
        '{"squads": [{"id": "<short_snake_case>", "name": "...", '
        '"members": ["<exact character name>", ...], "min_relic": <int>, '
        '"modes": ["gac"|"tw"|"arena"], "source": "...", "confidence": "..."}], '
        '"fleets": [{"id": "...", "name": "...", "capital": "<exact ship name>", '
        '"ships": ["<exact ship name>", ...], "min_ships": <int>, '
        '"source": "...", "confidence": "..."}]}\n\n'
        f"CURRENT REFERENCE (for context — don't just repeat these unchanged):\n"
        f"{json.dumps(meta_teams, ensure_ascii=False)[:4000]}\n\n"
        "RESEARCH:\n" + "\n\n".join(lines)
    )


def parse_team_synthesis(text):
    d = _extract_json(text)
    if "squads" not in d:
        raise ValueError("team synthesis JSON missing 'squads'")
    d.setdefault("fleets", [])
    return d


def synthesize_teams(meta_teams, gathered, key, gemini_call=gemini_generate_large):
    prompt = build_team_prompt(meta_teams, gathered)
    return parse_team_synthesis(gemini_call(prompt, key))


def resolve_proposed_squads(squads, name_map):
    """Resolve every member name to a base_id; drop the whole squad (not just
    the bad member) if ANY name fails to resolve — a half-resolved team isn't
    safely usable for readiness checks, it's just unverified content."""
    reverse_map = _build_reverse_name_map(name_map)
    resolved, dropped = [], []
    for s in squads:
        members, unresolved = [], []
        for n in s.get("members", []):
            bid = resolve_unit_name(n, name_map, reverse_map)
            (members if bid else unresolved).append(bid or n)
        if unresolved:
            dropped.append(f"{s.get('name', s.get('id'))}: unresolved member(s) "
                           f"{', '.join(unresolved)}")
            continue
        resolved.append({**s, "members": members})
    return resolved, dropped


def _member_diff_count(a, b):
    return len(set(a) ^ set(b))


def merge_team_proposals(current, proposed_squads):
    """New id -> add. Same id, near-identical composition -> update in place
    (a refresh of the same known team). Same id, meaningfully different
    composition -> add as a NEW variant — never silently overwrite a verified
    entry with an unverified one."""
    squads = [dict(s) for s in current.get("squads", [])]
    by_id = {s["id"]: i for i, s in enumerate(squads)}
    changes = []
    for p in proposed_squads:
        pid = p["id"]
        if pid not in by_id:
            squads.append(p)
            changes.append(f"{p['name']}: new squad added")
            continue
        existing = squads[by_id[pid]]
        if _member_diff_count(existing["members"], p["members"]) <= VARIANT_DIFF_THRESHOLD:
            squads[by_id[pid]] = {**existing, **p, "members": p["members"]}
            changes.append(f"{p['name']}: updated")
        else:
            variant_id = f"{pid}_{sum(1 for s in squads if s['id'].startswith(pid))}"
            squads.append({**p, "id": variant_id})
            changes.append(f"{p['name']}: added as a new VARIANT (kept existing "
                           f"'{existing['name']}' untouched — compositions differ)")
    return {"squads": squads, "fleets": current.get("fleets", [])}, changes


def add_team(name, member_base_ids, source, directory=HERE, min_relic=5, modes=None):
    """On-demand: append a squad you've personally verified (e.g. real ladder
    evidence) straight into the live file — you vetted it, so no proposal step.
    Mirrors add_video's rationale exactly."""
    path = os.path.join(directory, "meta_teams.json")
    meta = _load(path, {"squads": [], "fleets": []})
    squad = {"id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
             "name": name, "members": member_base_ids, "min_relic": min_relic,
             "modes": modes or ["gac"], "source": source, "confidence": "high"}
    meta.setdefault("squads", []).append(squad)
    with open(path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return squad


def add_video(url, gem_key, watcher=watch_video, directory=HERE):
    """On-demand: watch ONE video you found and append its tips straight into
    the live knowledge base (you vetted the source, so no proposal step)."""
    tips = _bullets(watcher(url, gem_key))
    path = os.path.join(directory, "knowledge.json")
    know = _load(path, {})
    bucket = know.setdefault("acceleration_tips", [])
    have = {i.get("text") for i in bucket}
    for t in tips:
        if t not in have:
            bucket.append({"text": t, "source": url, "confidence": "high"})
    with open(path, "w") as f:
        json.dump(know, f, indent=2, ensure_ascii=False)
    return tips


def write_proposals(proposed, changes, directory=HERE, meta_teams=None):
    goals_out = {"_note": "PROPOSED GL requirements — review then promote. "
                 "Generated by refresh_knowledge.py.",
                 "as_of": date.today().isoformat(),
                 "galactic_legends": proposed.get("goals", [])}
    gp = os.path.join(directory, "goals.proposed.json")
    kp = os.path.join(directory, "knowledge.proposed.json")
    cp = os.path.join(directory, "KNOWLEDGE_CHANGELOG.md")
    with open(gp, "w") as f:
        json.dump(goals_out, f, indent=2, ensure_ascii=False)
    know = dict(proposed.get("knowledge", {}))
    know.setdefault("as_of", date.today().isoformat())
    with open(kp, "w") as f:
        json.dump(know, f, indent=2, ensure_ascii=False)
    paths = {"goals": gp, "knowledge": kp, "changelog": cp}
    if meta_teams is not None:
        mp = os.path.join(directory, "meta_teams.proposed.json")
        with open(mp, "w") as f:
            json.dump(meta_teams, f, indent=2, ensure_ascii=False)
        paths["meta_teams"] = mp
    with open(cp, "w") as f:
        f.write(f"# Proposed knowledge changes — {date.today().isoformat()}\n\n")
        f.write("\n".join(f"- {c}" for c in changes) if changes
                else "- (no structural changes; content/provenance refresh only)")
        f.write("\n\nReview, then apply with: `python3 refresh_knowledge.py --promote`\n")
    return paths


def promote(directory=HERE):
    """Swap proposed files into the live files. Returns False if none pending."""
    gp = os.path.join(directory, "goals.proposed.json")
    kp = os.path.join(directory, "knowledge.proposed.json")
    mp = os.path.join(directory, "meta_teams.proposed.json")
    if not os.path.exists(gp):
        return False
    shutil.move(gp, os.path.join(directory, "goals.json"))
    if os.path.exists(kp):
        shutil.move(kp, os.path.join(directory, "knowledge.json"))
    if os.path.exists(mp):
        shutil.move(mp, os.path.join(directory, "meta_teams.json"))
    return True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    gem_key = os.environ.get(GEMINI_API_KEY_ENV, "")

    if "--promote" in argv:
        ok = promote()
        print("promoted proposal to live files" if ok else "no proposal to promote")
        return 0 if ok else 1

    if "--add-video" in argv:
        url = argv[argv.index("--add-video") + 1] if len(argv) > argv.index("--add-video") + 1 else ""
        if not gem_key:
            print("missing GEMINI_API_KEY", file=sys.stderr)
            return 2
        if not is_youtube_url(url):
            print("usage: --add-video <youtube-url>", file=sys.stderr)
            return 2
        tips = add_video(url, gem_key)
        print(f"added {len(tips)} tip(s) from {url} to knowledge.json:")
        for t in tips:
            print("  -", t)
        return 0

    if "--add-team" in argv:
        i = argv.index("--add-team")
        if len(argv) < i + 3:
            print('usage: --add-team "<team name>" "<Unit One, Unit Two, ...>" '
                  '[--source "..."]', file=sys.stderr)
            return 2
        name, members_csv = argv[i + 1], argv[i + 2]
        source = argv[argv.index("--source") + 1] if "--source" in argv else "player-verified"
        name_map = load_name_map()
        reverse_map = _build_reverse_name_map(name_map)
        member_names = [m.strip() for m in members_csv.split(",") if m.strip()]
        resolved, unresolved = [], []
        for n in member_names:
            bid = resolve_unit_name(n, name_map, reverse_map)
            (resolved if bid else unresolved).append(bid or n)
        if unresolved:
            print("Couldn't resolve: " + ", ".join(unresolved), file=sys.stderr)
            return 2
        squad = add_team(name, resolved, source)
        print(f"added squad '{name}' to meta_teams.json: {resolved} (source: {source})")
        return 0

    tav_key = os.environ.get(TAVILY_API_KEY_ENV, "")
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not tav_key or not gem_key:
        print("missing TAVILY_API_KEY or GEMINI_API_KEY", file=sys.stderr)
        return 2

    goals = _load(GOALS_PATH, {}).get("galactic_legends", [])
    knowledge = _load(KNOWLEDGE_PATH, {})
    current_meta = _load(META_TEAMS_PATH, {"squads": [], "fleets": []})
    name_map = load_name_map()

    gathered = gather(build_research_queries(goals), tav_key)
    gathered += gather_video_tips(TIP_VIDEO_TERMS, yt_key, gem_key)  # Gemini-watch
    proposed = synthesize(goals, knowledge, gathered, gem_key)

    # Drop what the model got wrong (hallucinated ids, ships) rather than
    # discarding the whole refresh; dropped items are surfaced for review.
    proposed, dropped = sanitize_proposed(proposed, name_map)

    issues = validate_proposed(proposed, name_map)
    if issues:  # should be empty after sanitize — belt-and-suspenders
        print("REJECTED proposal — validation issues after sanitize:", file=sys.stderr)
        for i in issues[:20]:
            print("  -", i, file=sys.stderr)
        return 3

    changes = changelog(goals, proposed.get("goals", []))
    if dropped:
        changes.append(f"({len(dropped)} unverifiable entries dropped — see below)")
        changes += [f"  dropped: {d}" for d in dropped[:30]]

    # Team refresh — same discipline now applied to meta_teams.json.
    team_gathered = gather(build_team_queries(current_meta), tav_key)
    team_proposed = synthesize_teams(current_meta, team_gathered, gem_key)
    resolved_squads, team_dropped = resolve_proposed_squads(
        team_proposed.get("squads", []), name_map)
    merged_meta, team_changes = merge_team_proposals(current_meta, resolved_squads)
    changes += team_changes
    if team_dropped:
        changes.append(f"({len(team_dropped)} team entries dropped — see below)")
        changes += [f"  dropped: {d}" for d in team_dropped[:30]]

    paths = write_proposals(proposed, changes, meta_teams=merged_meta)
    print(f"proposal written ({len(changes)} change line(s), "
          f"{len(dropped) + len(team_dropped)} dropped):")
    for c in changes[:25]:
        print("  -", c)
    print("review:", paths["changelog"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
