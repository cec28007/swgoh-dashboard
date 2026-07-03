import json
import unittest

import digest


def _unit(base_id, stars=7, gear=13, relic=5, zetas=0, name=None):
    return {"base_id": base_id, "name": name or base_id, "type": "character",
            "stars": stars, "gear_level": gear, "relic": relic,
            "zetas": zetas, "omicrons": 0}


class TestRosterDiff(unittest.TestCase):
    def test_new_unlock(self):
        prev = [_unit("GLLEIA")]
        cur = [_unit("GLLEIA"), _unit("ROTTATHEHUTT", name="Rotta the Hutt")]
        d = digest.compute_roster_diff(cur, prev)
        self.assertEqual([u["name"] for u in d["new_units"]], ["Rotta the Hutt"])

    def test_star_gear_relic_zeta_ups(self):
        prev = [_unit("X", stars=6, gear=12, relic=3, zetas=1, name="X")]
        cur = [_unit("X", stars=7, gear=13, relic=5, zetas=2, name="X")]
        d = digest.compute_roster_diff(cur, prev)
        self.assertEqual(d["star_ups"][0], {"name": "X", "from": 6, "to": 7})
        self.assertEqual(d["gear_ups"][0], {"name": "X", "from": 12, "to": 13})
        self.assertEqual(d["relic_ups"][0], {"name": "X", "from": 3, "to": 5})
        self.assertEqual(d["new_zetas"][0], {"name": "X", "from": 1, "to": 2})

    def test_no_change_is_empty(self):
        roster = [_unit("A"), _unit("B")]
        d = digest.compute_roster_diff(roster, roster)
        self.assertTrue(all(v == [] for v in d.values()))

    def test_no_previous_snapshot_is_empty(self):
        d = digest.compute_roster_diff([_unit("A")], [])
        self.assertEqual(d["new_units"], [])  # first run: no false "everything is new"


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Some Creator</title>
 <entry>
  <title>How to farm Rotta the Hutt fast</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=ABC123"/>
  <published>2026-07-02T10:00:00+00:00</published>
  <author><name>Some Creator</name></author>
  <media:group><media:description>Rotta farming guide</media:description></media:group>
 </entry>
</feed>"""

REDDIT = {"data": {"children": [
    {"data": {"title": "Rotta the Hutt kit reveal", "permalink": "/r/SWGOH/comments/x/rotta/",
              "url": "https://redd.it/x", "created_utc": 1782000000, "selftext": "details"}},
]}}


class TestYoutubeRss(unittest.TestCase):
    def test_parses_entries(self):
        cands = digest.fetch_youtube_rss(["UC_test"], fetcher=lambda url: ATOM)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c["title"], "How to farm Rotta the Hutt fast")
        self.assertEqual(c["url"], "https://www.youtube.com/watch?v=ABC123")
        self.assertEqual(c["source"], "YouTube")
        self.assertIn("Rotta", c["snippet"])

    def test_bad_feed_skipped(self):
        def boom(url):
            raise RuntimeError("down")
        self.assertEqual(digest.fetch_youtube_rss(["UC_x"], fetcher=boom), [])


class TestReddit(unittest.TestCase):
    def test_parses_posts(self):
        cands = digest.fetch_reddit(subreddit="SWGOH", fetcher=lambda url: REDDIT)
        titles = [c["title"] for c in cands]
        self.assertIn("Rotta the Hutt kit reveal", titles)
        c = cands[0]
        self.assertEqual(c["source"], "Reddit")
        self.assertTrue(c["url"].startswith("https://www.reddit.com/r/SWGOH/"))


class TestSearchYoutubeApi(unittest.TestCase):
    def test_no_key_returns_empty(self):
        self.assertEqual(digest.search_youtube_api(["Rotta"], key="", fetcher=lambda u: {}), [])

    def test_with_key_parses(self):
        resp = {"items": [{"id": {"videoId": "V1"},
                           "snippet": {"title": "Rotta guide", "description": "d",
                                       "channelTitle": "Chan", "publishedAt": "2026-07-02T00:00:00Z"}}]}
        cands = digest.search_youtube_api(["Rotta"], key="K", fetcher=lambda u: resp)
        self.assertEqual(cands[0]["url"], "https://www.youtube.com/watch?v=V1")
        self.assertEqual(cands[0]["source"], "YouTube")


class TestBuildCandidates(unittest.TestCase):
    def test_merge_dedupe_cap(self):
        a = [{"url": "u1", "title": "t1"}, {"url": "u2", "title": "t2"}]
        b = [{"url": "u2", "title": "dup"}, {"url": "u3", "title": "t3"}]
        out = digest.build_candidates(a, b, cap=2)
        urls = [c["url"] for c in out]
        self.assertEqual(len(out), 2)
        self.assertEqual(urls[0], "u1")  # order preserved, dedup by url


CANDS = [
    {"title": "How to farm Rotta the Hutt", "url": "u1", "source": "YouTube", "snippet": "s"},
    {"title": "Random GAC recap", "url": "u2", "source": "Reddit", "snippet": "s"},
]
DIFF = {"new_units": [{"name": "Rotta the Hutt"}], "star_ups": [], "gear_ups": [],
        "relic_ups": [], "new_zetas": []}


class TestRankWithGemini(unittest.TestCase):
    def test_parses_gemini_json(self):
        good = '```json\n{"headline":"For you today","items":[{"title":"Rotta farm","url":"u1","source":"YouTube","why":"new unlock"}]}\n```'
        d = digest.rank_with_gemini("summary", DIFF, CANDS, key="K",
                                    gemini_call=lambda prompt, key: good)
        self.assertEqual(d["headline"], "For you today")
        self.assertEqual(d["items"][0]["url"], "u1")

    def test_falls_back_on_bad_json(self):
        d = digest.rank_with_gemini("summary", DIFF, CANDS, key="K",
                                    gemini_call=lambda prompt, key: "not json at all")
        self.assertTrue(d["items"])            # fallback produced items
        self.assertTrue(d["headline"])
        # the Rotta item (matches the diff) should be present
        self.assertIn("u1", [i["url"] for i in d["items"]])


class TestFallbackRank(unittest.TestCase):
    def test_prioritizes_diff_matches(self):
        d = digest.fallback_rank(DIFF, CANDS)
        self.assertEqual(d["items"][0]["url"], "u1")  # Rotta match first


class TestSummaryAndTerms(unittest.TestCase):
    def test_roster_summary_mentions_top_units(self):
        units = [_unit("GLLEIA", name="Leia"), _unit("REY", name="Rey")]
        units[0]["power"] = 50000
        units[1]["power"] = 40000
        s = digest.roster_summary({"name": "Jaxen", "galactic_power": 7000000, "units": units})
        self.assertIn("Leia", s)
        self.assertIn("Jaxen", s)

    def test_search_terms_from_diff(self):
        terms = digest.search_terms_from_diff(DIFF)
        self.assertTrue(any("Rotta the Hutt" in t for t in terms))


class TestSnapshot(unittest.TestCase):
    def test_round_trip(self):
        import tempfile
        p = tempfile.mktemp(suffix=".json")
        units = [_unit("A", name="A"), _unit("B", name="B")]
        digest.save_snapshot(units, p)
        loaded = digest.load_snapshot(p)
        self.assertEqual({u["base_id"] for u in loaded}, {"A", "B"})

    def test_load_missing_is_empty(self):
        self.assertEqual(digest.load_snapshot("/nonexistent/path.json"), [])


class TestWriteDigest(unittest.TestCase):
    def test_writes_assignment(self):
        import tempfile
        p = tempfile.mktemp(suffix=".js")
        digest.write_digest({"headline": "h", "items": []}, p)
        txt = open(p).read()
        self.assertIn("window.SWGOH_DIGEST = ", txt)
        obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        self.assertEqual(obj["headline"], "h")


if __name__ == "__main__":
    unittest.main()
