import unittest
import teambuilder as tb


def _u(base_id, stars=7, relic=7, gear=13, type="character", power=20000):
    return {"base_id": base_id, "name": base_id.title(), "stars": stars,
            "relic": relic, "gear_level": gear, "type": type, "power": power}


META = {
    "squads": [
        {"id": "leia", "name": "GL Leia", "modes": ["gac"], "min_relic": 5,
         "members": ["GLLEIA", "R2D2", "DROGAN", "JYN", "RADDUS"]},
        {"id": "bh", "name": "Bounty Hunters", "modes": ["gac"], "min_relic": 5,
         "members": ["BOSSK", "JANGO", "MANDO"]},
    ],
    "fleets": [
        {"id": "gr", "name": "Galactic Republic", "capital": "ENDURANCE", "min_ships": 3,
         "ships": ["ETA2", "AHSOKASF", "PLOSF", "ARC170"]},
    ],
}


class TestSquadReadiness(unittest.TestCase):
    def test_fully_owned_and_strong_is_ready_no_weak(self):
        units = [_u(b) for b in ["GLLEIA", "R2D2", "DROGAN", "JYN", "RADDUS"]]
        r = tb.squad_readiness(units, META["squads"][0])
        self.assertTrue(r["ready"])
        self.assertEqual(r["gaps"], [])
        self.assertEqual(r["weak"], [])

    def test_missing_member_is_a_gap_not_ready(self):
        units = [_u(b) for b in ["GLLEIA", "R2D2", "DROGAN", "JYN"]]  # no RADDUS
        r = tb.squad_readiness(units, META["squads"][0])
        self.assertFalse(r["ready"])
        self.assertEqual(len(r["gaps"]), 1)
        self.assertEqual(r["gaps"][0]["base_id"], "RADDUS")
        self.assertFalse(r["gaps"][0]["owned"])

    def test_unowned_member_uses_name_map_not_raw_base_id(self):
        units = [_u(b) for b in ["GLLEIA", "R2D2", "DROGAN", "JYN"]]  # no RADDUS
        r = tb.squad_readiness(units, META["squads"][0], name_map={"RADDUS": "Admiral Raddus"})
        self.assertEqual(r["gaps"][0]["name"], "Admiral Raddus")

    def test_unowned_member_falls_back_to_base_id_without_name_map(self):
        units = [_u(b) for b in ["GLLEIA", "R2D2", "DROGAN", "JYN"]]
        r = tb.squad_readiness(units, META["squads"][0])
        self.assertEqual(r["gaps"][0]["name"], "RADDUS")

    def test_owned_but_under_relic_is_ready_but_weak(self):
        units = [_u(b, relic=7) for b in ["GLLEIA", "R2D2", "DROGAN", "JYN"]]
        units.append(_u("RADDUS", relic=2))  # 7-star (fieldable) but under min_relic 5
        r = tb.squad_readiness(units, META["squads"][0])
        self.assertTrue(r["ready"])  # fieldability = 7-star only
        self.assertEqual(len(r["weak"]), 1)
        self.assertEqual(r["weak"][0]["base_id"], "RADDUS")

    def test_under_7_star_is_a_gap_even_if_relic_high(self):
        units = [_u(b) for b in ["GLLEIA", "R2D2", "DROGAN", "JYN"]]
        units.append(_u("RADDUS", stars=6, relic=9))
        r = tb.squad_readiness(units, META["squads"][0])
        self.assertFalse(r["ready"])
        self.assertEqual(r["gaps"][0]["base_id"], "RADDUS")


class TestFleetReadiness(unittest.TestCase):
    def test_capital_plus_enough_ships_is_ready(self):
        units = [_u("ENDURANCE", type="ship"), _u("ETA2", type="ship"),
                 _u("AHSOKASF", type="ship"), _u("PLOSF", type="ship")]
        r = tb.fleet_readiness(units, META["fleets"][0])
        self.assertTrue(r["ready"])
        self.assertEqual(r["gaps"], [])

    def test_missing_capital_is_a_gap(self):
        units = [_u("ETA2", type="ship"), _u("AHSOKASF", type="ship"),
                 _u("PLOSF", type="ship")]
        r = tb.fleet_readiness(units, META["fleets"][0])
        self.assertFalse(r["ready"])
        self.assertTrue(any(g["base_id"] == "ENDURANCE" for g in r["gaps"]))

    def test_short_of_min_ships_reports_remaining_needed(self):
        units = [_u("ENDURANCE", type="ship"), _u("ETA2", type="ship")]  # only 1 of 3 needed
        r = tb.fleet_readiness(units, META["fleets"][0])
        self.assertFalse(r["ready"])
        self.assertEqual(r["ready_ship_count"], 1)
        # needs 2 more ships to hit min_ships=3
        self.assertEqual(len(r["gaps"]), 2)


class TestRankSuggestions(unittest.TestCase):
    def test_splits_ready_vs_almost_sorted_by_gap_count(self):
        readiness = {
            "squads": [
                {"id": "a", "name": "A", "ready": True, "gaps": []},
                {"id": "b", "name": "B", "ready": False, "gaps": [1, 2]},
                {"id": "c", "name": "C", "ready": False, "gaps": [1]},
            ],
            "fleets": [],
        }
        out = tb.rank_team_suggestions(readiness)
        self.assertEqual([t["id"] for t in out["ready"]], ["a"])
        self.assertEqual([t["id"] for t in out["almost"]], ["c", "b"])


class TestUpgradePriorities(unittest.TestCase):
    def test_unit_blocking_more_teams_ranks_first(self):
        readiness = {
            "squads": [
                {"id": "s1", "name": "Squad1", "ready": False,
                 "gaps": [{"base_id": "SHARED", "name": "Shared", "owned": False}]},
                {"id": "s2", "name": "Squad2", "ready": False,
                 "gaps": [{"base_id": "SHARED", "name": "Shared", "owned": False},
                          {"base_id": "OTHER", "name": "Other", "owned": False}]},
            ],
            "fleets": [],
        }
        out = tb.upgrade_priorities(readiness)
        self.assertEqual(out[0]["base_id"], "SHARED")  # blocks 2 teams
        self.assertEqual(len(out[0]["teams"]), 2)


class TestStrengthenPriorities(unittest.TestCase):
    def test_ready_team_with_fewer_weak_members_ranks_first(self):
        readiness = {"squads": [
            {"id": "s1", "name": "S1", "ready": True,
             "weak": [{"base_id": "W1", "name": "W1", "relic": 2}], "min_relic": 5},
            {"id": "s2", "name": "S2", "ready": True,
             "weak": [{"base_id": "W2", "name": "W2", "relic": 1},
                      {"base_id": "W3", "name": "W3", "relic": 3}], "min_relic": 5},
        ]}
        out = tb.strengthen_priorities(readiness)
        self.assertEqual(out[0]["team"], "S1")


class TestPromptAndGenerate(unittest.TestCase):
    def test_prompt_includes_ready_almost_and_upgrade_facts(self):
        readiness = {"squads": [{"id": "a", "name": "Ready Squad", "ready": True,
                                 "gaps": [], "weak": [], "min_relic": 5, "kind": "squad"}],
                     "fleets": []}
        suggestions = {"ready": readiness["squads"], "almost": []}
        p = tb.build_team_prompt({"name": "P", "galactic_power": 7000000}, readiness,
                                 suggestions, [], [], {})
        self.assertIn("Ready Squad", p)
        self.assertIn("READY", p.upper())

    def test_generate_teams_wires_gemini_and_returns_shape(self):
        roster = {"name": "P", "galactic_power": 7000000, "units": [
            _u("GLLEIA"), _u("R2D2"), _u("DROGAN"), _u("JYN"), _u("RADDUS")]}

        def fake_gemini(prompt, key):
            return "1. Field GL Leia now."

        out = tb.generate_teams(roster, META, {}, fake_gemini, "k")
        self.assertIn("readiness", out)
        self.assertIn("ready", out)
        self.assertIn("almost", out)
        self.assertIn("upgrade_priorities", out)
        self.assertIn("strengthen_priorities", out)
        self.assertEqual(out["plan"], "1. Field GL Leia now.")
        self.assertTrue(any(t["id"] == "leia" for t in out["ready"]))


if __name__ == "__main__":
    unittest.main()
