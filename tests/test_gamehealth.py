import unittest
import gamehealth as gh


def _u(base_id, stars=7, relic=7, gear=13, type="character", power=20000):
    return {"base_id": base_id, "name": base_id.title(), "stars": stars,
            "relic": relic, "gear_level": gear, "type": type, "power": power}


# synthetic meta_teams: 2 squads, 1 fleet
META = {
    "squads": [
        {"id": "rebels", "name": "Rebels", "faction": "Rebel", "modes": ["gac"],
         "members": ["GLLEIA", "CLS", "HAN", "CHEWIE", "THREEPIO"], "min_relic": 5},
        {"id": "empire", "name": "Empire", "faction": "Empire", "modes": ["gac"],
         "members": ["VADER", "PALP", "TARKIN", "PIETT", "STARCK"], "min_relic": 5},
    ],
    "fleets": [
        {"id": "gr", "name": "Galactic Republic", "capital": "ENDURANCE",
         "ships": ["ETA2", "AHSOKASF", "PLOSF", "ARC170"], "min_ships": 3},
    ],
}


class TestGLScore(unittest.TestCase):
    def test_owned_and_progress_drive_score(self):
        goals = [
            {"id": "GLLEIA", "name": "Leia", "requirements": []},          # owned
            {"id": "SLKR", "name": "SLKR", "requirements": [
                {"base_id": "KYLO", "name": "Kylo", "relic": 7}]},          # missing
        ]
        units = [_u("GLLEIA")]  # owns Leia, not Kylo
        r = gh.score_galactic_legends(units, goals)
        self.assertEqual(r["owned"], 1)
        self.assertGreater(r["score"], 0)
        self.assertLess(r["score"], 100)


class TestSquadDepth(unittest.TestCase):
    def test_counts_only_fully_fieldable_teams(self):
        # own all of Rebels at R6 (>=5 ok), but Empire missing two members
        units = [_u(b, relic=6) for b in
                 ["GLLEIA", "CLS", "HAN", "CHEWIE", "THREEPIO", "VADER", "PALP", "TARKIN"]]
        r = gh.score_squad_depth(units, META)
        self.assertIn("rebels", r["fieldable"])
        self.assertNotIn("empire", r["fieldable"])       # missing PIETT, STARCK
        self.assertEqual(len(r["fieldable"]), 1)

    def test_under_7_stars_member_disqualifies_team(self):
        units = [_u(b) for b in ["GLLEIA", "CLS", "HAN", "CHEWIE"]]
        units.append(_u("THREEPIO", stars=6))            # below 7 stars
        r = gh.score_squad_depth(units, META)
        self.assertNotIn("rebels", r["fieldable"])

    def test_owned_at_7_stars_low_relic_still_fieldable(self):
        units = [_u(b, relic=0) for b in ["GLLEIA", "CLS", "HAN", "CHEWIE", "THREEPIO"]]
        r = gh.score_squad_depth(units, META)
        self.assertIn("rebels", r["fieldable"])          # R0 but 7-star = fieldable


class TestFleet(unittest.TestCase):
    def test_needs_capital_plus_min_ships(self):
        units = [_u("ENDURANCE", type="ship"),
                 _u("ETA2", type="ship"), _u("AHSOKASF", type="ship"),
                 _u("PLOSF", type="ship")]                # capital + 3 ships
        r = gh.score_fleet(units, META)
        self.assertTrue(r["best_fleet"])
        self.assertGreater(r["score"], 0)

    def test_no_capital_means_weak_fleet(self):
        units = [_u("ETA2", type="ship"), _u("AHSOKASF", type="ship"),
                 _u("PLOSF", type="ship")]                # ships but no capital
        r = gh.score_fleet(units, META)
        self.assertEqual(r["capitals_owned"], 0)
        self.assertLess(r["score"], 50)


class TestOpportunities(unittest.TestCase):
    def test_ranks_low_score_high_weight_first(self):
        health = {"domains": {
            "galactic_legends": {"score": 30},   # low + highest weight -> top
            "fleet": {"score": 90},
            "gac_depth": {"score": 80},
            "roster_depth": {"score": 85},
        }}
        opps = gh.rank_opportunities(health)
        self.assertEqual(opps[0]["domain"], "galactic_legends")
        # descending by roi
        rois = [o["roi"] for o in opps]
        self.assertEqual(rois, sorted(rois, reverse=True))


class TestAssembleAndPrompt(unittest.TestCase):
    def test_assess_health_scores_all_domains(self):
        goals = [{"id": "GLLEIA", "name": "Leia", "requirements": []}]
        units = [_u("GLLEIA")]
        h = gh.assess_health({"units": units, "name": "P", "galactic_power": 5000000},
                             goals, META)
        for d in ("galactic_legends", "gac_depth", "fleet", "roster_depth"):
            self.assertIn(d, h["domains"])
            self.assertIn("score", h["domains"][d])
        self.assertIn("overall", h)

    def test_prompt_includes_econ_inputs_and_focus_ask(self):
        h = {"domains": {"fleet": {"score": 40, "state": "weak"}}, "overall": 55}
        opps = [{"domain": "fleet", "roi": 60, "why": "unsynergized"}]
        p = gh.build_health_prompt({"name": "P", "galactic_power": 5000000},
                                   h, opps, {"Fleet arena rank": "120"}, {})
        self.assertIn("Fleet arena rank", p)
        self.assertIn("120", p)
        # asks for a weekly + daily focus
        self.assertIn("week", p.lower())


if __name__ == "__main__":
    unittest.main()
