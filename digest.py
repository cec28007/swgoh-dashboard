#!/usr/bin/env python3
"""Phase 2 — the daily "Today for You" tips digest (zero-dependency, stdlib only).

Scans open SWGOH content sources (curated YouTube creators via RSS, optional
YouTube topic-search, Reddit r/SWGOH), figures out what changed in the player's
roster since yesterday, asks Gemini to rank what's relevant, and writes digest.js
for the dashboard panel to render. Runs once a day from the swgoh-dash container.
"""
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DIGEST_OUT = os.path.join(HERE, "digest.js")
SNAPSHOT_PATH = os.path.join(HERE, "roster_snapshot.json")
DATA_PATH = os.path.join(HERE, "swgoh_data.js")

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
YOUTUBE_API_KEY_ENV = "YOUTUBE_API_KEY"
ASK_MODEL = os.environ.get("ASK_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
USER_AGENT = "swgoh-dashboard-digest/1.0 (personal use)"

# Curated top SWGOH creators (channel IDs for free RSS; no API key needed).
# Verified from the channels' pages. Add more UC ids here anytime.
CURATED_CHANNELS = [
    "UCPG8s-QI4td93Jufi1tJ7LQ",  # AhnaldT101
    "UCtyygqXwJK5NhAnbqAjh9SQ",  # The Gaming Merchant
    "UC4mqZZ-rseQnPok8lB0EGJA",  # Skelturix
    "UCWdPo55TETX-OzAyaANsiNw",  # DBofficial125
    "UC4Fio8dtuN6ixoiT2-uHVrA",  # Endall Beall
]


MAX_FETCH_BYTES = 5 * 1024 * 1024  # cap response size (bounds XML-expansion abuse)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        # Zero-dependency constraint rules out defusedxml; ElementTree does not
        # resolve external entities, feeds are YouTube/Reddit over HTTPS, and we
        # bound the input size here to limit billion-laughs-style expansion.
        return resp.read(MAX_FETCH_BYTES).decode("utf-8", errors="replace")


def _get_json(url):
    return json.loads(_get(url))


_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"


def fetch_youtube_rss(channel_ids, fetcher=_get):
    """Recent uploads from curated channels via their free Atom RSS feeds."""
    out = []
    for cid in channel_ids:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        try:
            root = ET.fromstring(fetcher(url))
        except Exception:  # noqa: BLE001 - one bad feed shouldn't sink the rest
            continue
        for e in root.findall(f"{_ATOM}entry"):
            title = (e.findtext(f"{_ATOM}title") or "").strip()
            link_el = e.find(f"{_ATOM}link")
            url_v = link_el.get("href") if link_el is not None else None
            desc = ""
            grp = e.find(f"{_MEDIA}group")
            if grp is not None:
                desc = (grp.findtext(f"{_MEDIA}description") or "").strip()
            author = e.findtext(f"{_ATOM}author/{_ATOM}name") or "YouTube"
            if title and url_v:
                out.append({"title": title, "url": url_v, "source": "YouTube",
                            "published": e.findtext(f"{_ATOM}published") or "",
                            "snippet": (desc or author)[:300]})
    return out


def fetch_reddit(subreddit="SWGOH", fetcher=_get_json):
    """Top-of-day + new posts from a subreddit's public listing JSON."""
    out = []
    for feed in ("top.json?t=day", "new.json"):
        url = f"https://www.reddit.com/r/{subreddit}/{feed}?limit=25"
        try:
            data = fetcher(url)
        except Exception:  # noqa: BLE001
            continue
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            perma = p.get("permalink")
            if not p.get("title") or not perma:
                continue
            out.append({"title": p["title"].strip(),
                        "url": "https://www.reddit.com" + perma,
                        "source": "Reddit",
                        "published": str(p.get("created_utc", "")),
                        "snippet": (p.get("selftext") or "")[:300]})
    return out


def search_youtube_api(terms, key, fetcher=_get_json):
    """Topic search via YouTube Data API v3. Empty when no key or on error."""
    if not key:
        return []
    out = []
    for term in terms:
        q = urllib.parse.quote(term)
        url = ("https://www.googleapis.com/youtube/v3/search?part=snippet"
               f"&type=video&order=date&maxResults=5&q={q}&key={key}")
        try:
            data = fetcher(url)
        except Exception:  # noqa: BLE001 - quota/errors fail soft
            continue
        for item in data.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            sn = item.get("snippet") or {}
            if not vid:
                continue
            out.append({"title": (sn.get("title") or "").strip(),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "source": "YouTube",
                        "published": sn.get("publishedAt") or "",
                        "snippet": (sn.get("description") or "")[:300]})
    return out


def build_candidates(*lists, cap=40):
    """Merge candidate lists, dedupe by url (first wins), cap the total."""
    seen, out = set(), []
    for lst in lists:
        for c in lst:
            u = c.get("url")
            if not u or u in seen:
                continue
            seen.add(u)
            out.append(c)
            if len(out) >= cap:
                return out
    return out


def _extract_json(text):
    """Pull a JSON object out of a model reply (tolerates ```json fences)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


def _diff_names(diff):
    names = [u["name"] for u in diff.get("new_units", [])]
    for key in ("star_ups", "gear_ups", "relic_ups", "new_zetas"):
        names += [u["name"] for u in diff.get(key, [])]
    return names


def fallback_rank(diff, candidates, cap=8):
    """Non-AI ranking: surface candidates whose title matches a changed unit."""
    names = _diff_names(diff)
    matched, rest = [], []
    for c in candidates:
        title = (c.get("title") or "").lower()
        hit = next((n for n in names if n and n.lower() in title), None)
        item = {"title": c.get("title"), "url": c.get("url"),
                "source": c.get("source"),
                "why": f"Mentions {hit} (new for you)" if hit else "Recent SWGOH content"}
        (matched if hit else rest).append(item)
    headline = ("New for you: " + ", ".join(names[:3])) if names else "Today in SWGOH"
    return {"headline": headline, "items": (matched + rest)[:cap]}


def gemini_generate(prompt, key):
    """One Gemini generateContent call; returns the reply text."""
    # 2.5-flash spends "thinking" tokens against this budget; the ranked JSON
    # needs room after that, so keep it generous to avoid a truncated reply.
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": 8192}}
    url = f"{GEMINI_URL}/{ASK_MODEL}:generateContent"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"x-goog-api-key": key,
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    cands = out.get("candidates", [])
    parts = cands[0].get("content", {}).get("parts", []) if cands else []
    return "".join(p.get("text", "") for p in parts)


def _rank_prompt(roster_summary, diff, candidates):
    lines = [f"- [{c['source']}] {c['title']} :: {c['url']}" for c in candidates]
    return (
        "You are a Star Wars: Galaxy of Heroes coach. Pick the most useful content "
        "for THIS player today and explain why in one short sentence each, tying it "
        "to their roster or recent changes. Prefer items about units they just "
        "unlocked or upgraded. When the candidates allow, favor a MIX of content "
        "types (how-to/farming guides, new-character kits and reveals, event/raid/GAC "
        "strategy, meta and counters) over near-duplicate 'roster review' videos — "
        "but still return up to 8 of the most useful items.\n\n"
        f"PLAYER ROSTER SUMMARY:\n{roster_summary}\n\n"
        f"WHAT CHANGED SINCE YESTERDAY:\n{json.dumps(diff)}\n\n"
        f"CANDIDATE CONTENT (title :: url):\n" + "\n".join(lines) + "\n\n"
        'Reply with ONLY JSON: {"headline": "<=8 words", "items": '
        '[{"title","url","source","why"}]} — at most 8 items, ranked best first, '
        "using only urls from the candidate list."
    )


def rank_with_gemini(roster_summary, diff, candidates, key, gemini_call=gemini_generate):
    """Ask Gemini to rank candidates; fall back to a heuristic on any failure."""
    if not candidates:
        return {"headline": "No new content today", "items": []}
    try:
        text = gemini_call(_rank_prompt(roster_summary, diff, candidates), key)
        d = _extract_json(text)
        valid = [i for i in d.get("items", []) if i.get("url") and i.get("title")]
        if not valid:
            raise ValueError("no valid items")
        return {"headline": d.get("headline") or "Today for you", "items": valid[:8]}
    except Exception:  # noqa: BLE001 - any failure -> deterministic fallback
        return fallback_rank(diff, candidates)


def write_digest(digest_obj, path=DIGEST_OUT):
    digest_obj = dict(digest_obj)
    digest_obj.setdefault("generated", date.today().isoformat())
    body = json.dumps(digest_obj, indent=2, ensure_ascii=False)
    with open(path, "w") as f:
        f.write("// Auto-generated by digest.py — do not edit by hand.\n")
        f.write("window.SWGOH_DIGEST = " + body + ";\n")


_SNAP_FIELDS = ("base_id", "name", "stars", "gear_level", "relic", "zetas", "omicrons")


def save_snapshot(units, path=SNAPSHOT_PATH):
    snap = [{k: u.get(k) for k in _SNAP_FIELDS} for u in units]
    with open(path, "w") as f:
        json.dump(snap, f)


def load_snapshot(path=SNAPSHOT_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def load_roster(path=DATA_PATH):
    """Read the units + player meta out of swgoh_data.js."""
    with open(path) as f:
        txt = f.read()
    obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    return obj


def roster_summary(roster, top=25):
    units = roster.get("units", [])
    chars = sorted([u for u in units if u.get("type") == "character"],
                   key=lambda x: x.get("power") or 0, reverse=True)
    top_names = ", ".join(u.get("name", u["base_id"]) for u in chars[:top])
    gp = roster.get("galactic_power")
    return (f"Player {roster.get('name')} — GP {gp}, {len(chars)} characters. "
            f"Top units: {top_names}.")


def search_terms_from_diff(diff, extra=("SWGOH new characters", "SWGOH meta")):
    terms = [f"SWGOH {u['name']}" for u in diff.get("new_units", [])]
    for key in ("relic_ups", "gear_ups"):
        terms += [f"SWGOH {u['name']} guide" for u in diff.get(key, [])[:2]]
    terms += list(extra)
    # dedupe, keep order, cap
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t); out.append(t)
    return out[:6]


def build_digest(roster, previous, yt_key, gem_key,
                 channels=None, fetch_text=_get, fetch_json=_get_json,
                 gemini_call=gemini_generate):
    """Full pipeline: diff -> gather -> rank. Returns the digest dict."""
    units = roster.get("units", [])
    diff = compute_roster_diff(units, previous)
    rss = fetch_youtube_rss(channels or CURATED_CHANNELS, fetcher=fetch_text)
    api = search_youtube_api(search_terms_from_diff(diff), yt_key, fetcher=fetch_json)
    reddit = fetch_reddit("SWGOH", fetcher=fetch_json)
    candidates = build_candidates(api, reddit, rss, cap=40)
    return rank_with_gemini(roster_summary(roster), diff, candidates, gem_key,
                            gemini_call=gemini_call)


def main():
    roster = load_roster()
    previous = load_snapshot()
    gem_key = os.environ.get(GEMINI_API_KEY_ENV, "")
    yt_key = os.environ.get(YOUTUBE_API_KEY_ENV, "")
    digest_obj = build_digest(roster, previous, yt_key, gem_key)
    write_digest(digest_obj)
    save_snapshot(roster.get("units", []))
    print(f"digest: {len(digest_obj.get('items', []))} items | "
          f"headline: {digest_obj.get('headline')}")
    return 0


def compute_roster_diff(current, previous):
    """What changed for the player since the last snapshot.

    Returns dict of lists: new_units [{name}], star_ups/gear_ups/relic_ups/
    new_zetas [{name, from, to}]. Empty on first run (no previous) to avoid
    flagging the entire roster as "new".
    """
    empty = {"new_units": [], "star_ups": [], "gear_ups": [],
             "relic_ups": [], "new_zetas": []}
    if not previous:
        return empty
    prev = {u["base_id"]: u for u in previous}
    diff = {k: [] for k in empty}
    for u in current:
        p = prev.get(u["base_id"])
        if p is None:
            diff["new_units"].append({"name": u.get("name", u["base_id"])})
            continue
        name = u.get("name", u["base_id"])
        for key, field in (("star_ups", "stars"), ("gear_ups", "gear_level"),
                           ("relic_ups", "relic"), ("new_zetas", "zetas")):
            old, new = p.get(field) or 0, u.get(field) or 0
            if new > old:
                diff[key].append({"name": name, "from": old, "to": new})
    return diff


if __name__ == "__main__":
    import sys
    sys.exit(main())
