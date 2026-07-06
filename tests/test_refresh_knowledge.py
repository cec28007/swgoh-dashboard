import json
import os
import tempfile
import unittest

import refresh_knowledge as rk


GOALS = [
    {"id": "SLKR", "name": "Supreme Leader Kylo Ren", "requirements": [
        {"base_id": "KYLOREN", "name": "Kylo Ren", "relic": 5},
    ]},
]


class TestQueries(unittest.TestCase):
    def test_one_query_per_gl_plus_meta(self):
        qs = rk.build_research_queries(GOALS)
        self.assertEqual(len(qs), len(GOALS) + len(rk.META_QUERIES))
        first = qs[0]
        self.assertEqual(first["kind"], "requirements")
        self.assertEqual(first["gl_id"], "SLKR")
        self.assertIn("Supreme Leader Kylo Ren", first["query"])
        # meta queries carry no gl_id
        self.assertTrue(any(q["gl_id"] is None for q in qs))


class TestGather(unittest.TestCase):
    def test_attaches_results_and_calls_searcher_per_query(self):
        calls = []

        def fake_searcher(query, key, include_domains=None, max_results=5):
            calls.append(query)
            return [{"title": "T", "url": "http://x/1", "content": "body"}]

        qs = rk.build_research_queries(GOALS)
        got = rk.gather(qs, "KEY", searcher=fake_searcher)
        self.assertEqual(len(calls), len(qs))
        self.assertEqual(got[0]["results"][0]["url"], "http://x/1")


class TestTavilySearch(unittest.TestCase):
    def test_parses_results_from_tavily_payload(self):
        def fake_poster(url, payload):
            self.assertIn("tavily", url)
            self.assertEqual(payload["query"], "q")
            return {"results": [
                {"title": "A", "url": "http://a", "content": "ca", "score": 0.9},
                {"title": "B", "url": "http://b", "content": "cb", "score": 0.5},
            ]}

        out = rk.tavily_search("q", "KEY", poster=fake_poster)
        self.assertEqual([r["url"] for r in out], ["http://a", "http://b"])
        self.assertEqual(out[0]["title"], "A")


class TestParseSynthesis(unittest.TestCase):
    def test_extracts_fenced_json(self):
        text = ("Here you go:\n```json\n"
                '{"goals": [], "knowledge": {"whats_meta": []}, "changes": ["x"]}'
                "\n```\n")
        d = rk.parse_synthesis(text)
        self.assertEqual(d["changes"], ["x"])
        self.assertIn("knowledge", d)

    def test_rejects_missing_keys(self):
        with self.assertRaises(ValueError):
            rk.parse_synthesis('{"nope": 1}')


class TestValidate(unittest.TestCase):
    NAME_MAP = {"KYLOREN": "Kylo Ren", "PHASMA": "Captain Phasma"}

    def test_clean_proposal_has_no_issues(self):
        proposed = {"goals": [{"id": "SLKR", "requirements": [
            {"base_id": "KYLOREN", "name": "Kylo Ren", "relic": 7}]}]}
        self.assertEqual(rk.validate_proposed(proposed, self.NAME_MAP), [])

    def test_unknown_base_id_is_flagged(self):
        proposed = {"goals": [{"id": "SLKR", "requirements": [
            {"base_id": "NOTREAL", "name": "Ghost", "relic": 7}]}]}
        issues = rk.validate_proposed(proposed, self.NAME_MAP)
        self.assertTrue(any("NOTREAL" in i for i in issues))

    def test_out_of_range_relic_is_flagged(self):
        proposed = {"goals": [{"id": "SLKR", "requirements": [
            {"base_id": "KYLOREN", "name": "Kylo Ren", "relic": 99}]}]}
        issues = rk.validate_proposed(proposed, self.NAME_MAP)
        self.assertTrue(any("relic" in i.lower() for i in issues))


class TestSourceDomains(unittest.TestCase):
    def test_requirement_queries_locked_to_trusted_domains(self):
        qs = rk.build_research_queries(GOALS)
        req = next(q for q in qs if q["kind"] == "requirements")
        self.assertEqual(req["domains"], rk.REQUIREMENT_DOMAINS)

    def test_a_community_query_targets_reddit(self):
        qs = rk.build_research_queries(GOALS)
        self.assertTrue(any(q.get("domains") and "reddit.com" in q["domains"]
                            for q in qs))

    def test_gather_passes_per_query_domains(self):
        seen = {}

        def fake_searcher(query, key, include_domains=None, max_results=5):
            seen[query] = include_domains
            return []

        qs = rk.build_research_queries(GOALS)
        rk.gather(qs, "K", searcher=fake_searcher)
        # the requirements query got the trusted domain list
        reqq = next(q for q in qs if q["kind"] == "requirements")["query"]
        self.assertEqual(seen[reqq], rk.REQUIREMENT_DOMAINS)


class TestVideoTips(unittest.TestCase):
    def test_gathers_tips_via_watcher_up_to_limit(self):
        def fake_search(terms, key, fetcher=None):
            return [{"title": f"V{i}", "url": f"http://y/{i}"} for i in range(5)]

        watched = []

        def fake_watch(url, key):
            watched.append(url)
            return "- Farm cantina energy daily\n- Save crystals for GL events"

        out = rk.gather_video_tips(["t"], "YT", "GEM",
                                   searcher=fake_search, watcher=fake_watch, limit=2)
        self.assertEqual(len(watched), 2)
        self.assertEqual(out[0]["kind"], "youtube_tips")
        self.assertIn("cantina", out[0]["results"][0]["content"])

    def test_empty_without_keys(self):
        self.assertEqual(rk.gather_video_tips(["t"], "", "GEM"), [])
        self.assertEqual(rk.gather_video_tips(["t"], "YT", ""), [])

    def test_watch_video_rejects_non_youtube(self):
        with self.assertRaises(ValueError):
            rk.watch_video("http://example.com/x", "K", caller=lambda u, p, k: "x")


class TestBullets(unittest.TestCase):
    def test_parses_bulleted_and_numbered_lines(self):
        text = "Intro line\n- Tip A\n* Tip B\n3. Tip C\n\n"
        self.assertEqual(rk._bullets(text), ["Tip A", "Tip B", "Tip C"])

    def test_drops_header_only_bullets_keeps_labeled_tips(self):
        text = ("- **Category X**:\n"                       # header only -> drop
                "- **Jotaz**: avoid buffing the boss\n"      # labeled tip -> keep
                "- Real standalone tip\n")
        self.assertEqual(rk._bullets(text),
                         ["**Jotaz**: avoid buffing the boss", "Real standalone tip"])


class TestAddVideo(unittest.TestCase):
    def test_appends_tips_to_knowledge_with_source(self):
        with tempfile.TemporaryDirectory() as d:
            json.dump({"acceleration_tips": [{"text": "old", "source": "s"}]},
                      open(os.path.join(d, "knowledge.json"), "w"))
            added = rk.add_video("https://youtu.be/abc123", "GEM",
                                 watcher=lambda u, k: "- Do the thing\n- And this",
                                 directory=d)
            self.assertEqual(added, ["Do the thing", "And this"])
            know = json.load(open(os.path.join(d, "knowledge.json")))
            tips = know["acceleration_tips"]
            self.assertEqual(len(tips), 3)  # 1 old + 2 new
            self.assertEqual(tips[-1]["source"], "https://youtu.be/abc123")


class TestSanitize(unittest.TestCase):
    NAME_MAP = {"KYLOREN": "Kylo Ren", "PHASMA": "Captain Phasma"}

    def test_drops_unknown_id_and_bad_relic_keeps_good(self):
        proposed = {"goals": [{"id": "SLKR", "name": "SLKR", "requirements": [
            {"base_id": "KYLOREN", "name": "Kylo Ren", "relic": 7},   # good
            {"base_id": "NOTREAL", "name": "Ghost", "relic": 5},      # bad id
            {"base_id": "MILLENNIUMFALCON", "name": "Falcon", "relic": None},  # ship
        ]}], "knowledge": {}}
        clean, dropped = rk.sanitize_proposed(proposed, self.NAME_MAP)
        reqs = clean["goals"][0]["requirements"]
        self.assertEqual([r["base_id"] for r in reqs], ["KYLOREN"])
        self.assertEqual(len(dropped), 2)
        self.assertTrue(any("NOTREAL" in d for d in dropped))
        # a cleaned proposal passes validation
        self.assertEqual(rk.validate_proposed(clean, self.NAME_MAP), [])


class TestChangelog(unittest.TestCase):
    def test_reports_relic_change_and_added_unit(self):
        old = [{"id": "SLKR", "name": "SLKR", "requirements": [
            {"base_id": "KYLOREN", "name": "Kylo Ren", "relic": 5}]}]
        new = [{"id": "SLKR", "name": "SLKR", "requirements": [
            {"base_id": "KYLOREN", "name": "Kylo Ren", "relic": 7},
            {"base_id": "PHASMA", "name": "Captain Phasma", "relic": 3}]}]
        lines = rk.changelog(old, new)
        joined = "\n".join(lines)
        self.assertIn("Kylo Ren", joined)
        self.assertIn("R5", joined)
        self.assertIn("R7", joined)
        self.assertIn("Captain Phasma", joined)

    def test_reports_new_gl(self):
        new = [{"id": "JABBA", "name": "Jabba", "requirements": []}]
        lines = rk.changelog([], new)
        self.assertTrue(any("Jabba" in ln and "new" in ln.lower() for ln in lines))


class TestWriteAndPromote(unittest.TestCase):
    def test_write_then_promote_swaps_live_files(self):
        with tempfile.TemporaryDirectory() as d:
            # seed live files
            json.dump({"galactic_legends": [{"id": "OLD"}]},
                      open(os.path.join(d, "goals.json"), "w"))
            json.dump({"whats_meta": []},
                      open(os.path.join(d, "knowledge.json"), "w"))
            proposed = {
                "goals": [{"id": "NEW", "requirements": []}],
                "knowledge": {"whats_meta": [{"text": "t"}]},
            }
            rk.write_proposals(proposed, ["a change"], directory=d)
            self.assertTrue(os.path.exists(os.path.join(d, "goals.proposed.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "KNOWLEDGE_CHANGELOG.md")))

            self.assertTrue(rk.promote(directory=d))
            live = json.load(open(os.path.join(d, "goals.json")))
            self.assertEqual(live["galactic_legends"][0]["id"], "NEW")
            # proposed consumed
            self.assertFalse(os.path.exists(os.path.join(d, "goals.proposed.json")))

    def test_promote_noop_when_no_proposal(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(rk.promote(directory=d))


class TestSynthesize(unittest.TestCase):
    def test_uses_injected_gemini_and_parses(self):
        def fake_gemini(prompt, key):
            self.assertIn("Supreme Leader Kylo Ren", prompt)
            return ('{"goals": [], "knowledge": {"whats_meta": []}, '
                    '"changes": ["ok"]}')

        gathered = [{"query": "q", "kind": "requirements", "gl_id": "SLKR",
                     "results": [{"title": "t", "url": "u", "content": "c"}]}]
        out = rk.synthesize(GOALS, {"whats_meta": []}, gathered, "KEY",
                            gemini_call=fake_gemini)
        self.assertEqual(out["changes"], ["ok"])


if __name__ == "__main__":
    unittest.main()


NAME_MAP_TEAMS = {"GLLEIA": "Leia Organa", "R2D2_LEGENDARY": "R2-D2",
                  "CAPTAINDROGAN": "Captain Drogan", "JYNERSO": "Jyn Erso",
                  "ADMIRALRADDUS": "Admiral Raddus", "OLDBENKENOBI": "Obi-Wan Kenobi (Old Ben)",
                  "KANANJARRUSS3": "Kanan Jarrus", "BOSSK": "Bossk",
                  "JANGOFETT": "Jango Fett", "THEMANDALORIAN": "The Mandalorian"}


class TestNameResolution(unittest.TestCase):
    def test_resolves_exact_name(self):
        self.assertEqual(rk.resolve_unit_name("Jyn Erso", NAME_MAP_TEAMS), "JYNERSO")

    def test_resolves_case_and_accent_insensitive(self):
        self.assertEqual(rk.resolve_unit_name("jyn erso", NAME_MAP_TEAMS), "JYNERSO")

    def test_passthrough_already_valid_base_id(self):
        self.assertEqual(rk.resolve_unit_name("JYNERSO", NAME_MAP_TEAMS), "JYNERSO")

    def test_unresolvable_name_returns_none(self):
        self.assertIsNone(rk.resolve_unit_name("Not A Real Unit", NAME_MAP_TEAMS))


class TestTeamQueries(unittest.TestCase):
    def test_includes_discovery_and_per_squad_queries(self):
        meta = {"squads": [{"id": "leia", "name": "GL Leia"}], "fleets": []}
        qs = rk.build_team_queries(meta)
        self.assertTrue(any(q["kind"] == "discovery" for q in qs))
        self.assertTrue(any("GL Leia" in q["query"] for q in qs))


class TestParseTeamSynthesis(unittest.TestCase):
    def test_extracts_squad_list(self):
        text = ('```json\n{"squads": [{"id": "leia", "name": "GL Leia", '
                '"members": ["Leia Organa", "R2-D2"], "min_relic": 5, '
                '"modes": ["gac"], "source": "u", "confidence": "high"}], '
                '"fleets": []}\n```')
        d = rk.parse_team_synthesis(text)
        self.assertEqual(d["squads"][0]["id"], "leia")

    def test_rejects_missing_squads_key(self):
        with self.assertRaises(ValueError):
            rk.parse_team_synthesis('{"fleets": []}')


class TestResolveProposedSquads(unittest.TestCase):
    def test_resolves_all_members_to_base_ids(self):
        squads = [{"id": "leia", "name": "GL Leia", "min_relic": 5, "modes": ["gac"],
                  "members": ["Leia Organa", "R2-D2", "Captain Drogan", "Jyn Erso",
                              "Admiral Raddus"], "source": "u", "confidence": "high"}]
        resolved, dropped = rk.resolve_proposed_squads(squads, NAME_MAP_TEAMS)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["members"],
                         ["GLLEIA", "R2D2_LEGENDARY", "CAPTAINDROGAN", "JYNERSO", "ADMIRALRADDUS"])
        self.assertEqual(dropped, [])

    def test_drops_squad_with_any_unresolvable_member(self):
        squads = [{"id": "x", "name": "Bad Squad", "min_relic": 5, "modes": [],
                  "members": ["Jyn Erso", "Totally Fake Unit"], "source": "u", "confidence": "low"}]
        resolved, dropped = rk.resolve_proposed_squads(squads, NAME_MAP_TEAMS)
        self.assertEqual(resolved, [])
        self.assertEqual(len(dropped), 1)
        self.assertIn("Totally Fake Unit", dropped[0])


class TestMergeTeamProposals(unittest.TestCase):
    CURRENT = {"squads": [{"id": "leia", "name": "GL Leia (Old Ben/Kanan)", "min_relic": 5,
                          "modes": ["gac"],
                          "members": ["GLLEIA", "R2D2_LEGENDARY", "CAPTAINDROGAN",
                                      "OLDBENKENOBI", "KANANJARRUSS3"]}],
               "fleets": []}

    def test_new_squad_id_is_added(self):
        proposed = [{"id": "bh", "name": "Bounty Hunters", "min_relic": 5, "modes": ["gac"],
                    "members": ["BOSSK", "JANGOFETT", "THEMANDALORIAN"]}]
        merged, changes = rk.merge_team_proposals(self.CURRENT, proposed)
        ids = [s["id"] for s in merged["squads"]]
        self.assertIn("leia", ids)
        self.assertIn("bh", ids)
        self.assertTrue(any("added" in c.lower() for c in changes))

    def test_near_identical_composition_updates_in_place(self):
        # differs by 1 member only -> treated as a refresh of the same team
        proposed = [{"id": "leia", "name": "GL Leia (Old Ben/Kanan)", "min_relic": 6,
                    "modes": ["gac"],
                    "members": ["GLLEIA", "R2D2_LEGENDARY", "CAPTAINDROGAN",
                                "OLDBENKENOBI", "KANANJARRUSS3"]}]
        merged, changes = rk.merge_team_proposals(self.CURRENT, proposed)
        self.assertEqual(len(merged["squads"]), 1)
        self.assertEqual(merged["squads"][0]["min_relic"], 6)

    def test_meaningfully_different_composition_becomes_a_new_variant(self):
        # same id, but 4-5 members differ -> a genuinely different team, must
        # NOT silently overwrite the existing verified entry
        proposed = [{"id": "leia", "name": "GL Leia (Jyn/Raddus)", "min_relic": 5,
                    "modes": ["gac"],
                    "members": ["GLLEIA", "R2D2_LEGENDARY", "CAPTAINDROGAN",
                                "JYNERSO", "ADMIRALRADDUS"]}]
        merged, changes = rk.merge_team_proposals(self.CURRENT, proposed)
        self.assertEqual(len(merged["squads"]), 2)
        # original untouched
        original = [s for s in merged["squads"] if s["name"] == "GL Leia (Old Ben/Kanan)"][0]
        self.assertEqual(original["members"][3], "OLDBENKENOBI")
        self.assertTrue(any("variant" in c.lower() for c in changes))


class TestAddTeam(unittest.TestCase):
    def test_appends_player_verified_squad_to_live_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "meta_teams.json")
            json.dump({"squads": [], "fleets": []}, open(path, "w"))
            squad = rk.add_team("GL Leia (Jyn/Raddus)",
                                ["GLLEIA", "R2D2_LEGENDARY", "CAPTAINDROGAN", "JYNERSO", "ADMIRALRADDUS"],
                                source="player-verified arena screenshot 2026-07-06",
                                directory=d)
            self.assertEqual(squad["confidence"], "high")
            live = json.load(open(path))
            self.assertEqual(len(live["squads"]), 1)
            self.assertEqual(live["squads"][0]["source"],
                             "player-verified arena screenshot 2026-07-06")


class TestPromoteIncludesTeams(unittest.TestCase):
    def test_promote_swaps_meta_teams_when_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            json.dump({"squads": [{"id": "old"}], "fleets": []},
                      open(os.path.join(d, "meta_teams.json"), "w"))
            json.dump({"galactic_legends": []}, open(os.path.join(d, "goals.json"), "w"))
            json.dump({}, open(os.path.join(d, "knowledge.json"), "w"))
            json.dump({"squads": [{"id": "new"}], "fleets": []},
                      open(os.path.join(d, "meta_teams.proposed.json"), "w"))
            proposed = {"goals": [], "knowledge": {}}
            rk.write_proposals(proposed, ["test"], directory=d)
            self.assertTrue(rk.promote(directory=d))
            live = json.load(open(os.path.join(d, "meta_teams.json")))
            self.assertEqual(live["squads"][0]["id"], "new")
