import unittest

import gameplan


GOALS = [
    {"id": "GLREY", "name": "Supreme Leader Kylo Ren", "requirements": [
        {"base_id": "REYJEDITRAINING", "name": "Rey (Jedi Training)", "relic": 5},
        {"base_id": "FINN", "name": "Finn", "relic": 5}]},
    {"id": "GLLEIA", "name": "Grand Master Leia", "requirements": [
        {"base_id": "HERASYNDULLAS3", "name": "Hera", "relic": 5}]},
]


def _u(base_id, name, stars=7, relic=7):
    return {"base_id": base_id, "name": name, "type": "character",
            "stars": stars, "relic": relic}


class TestGlReadiness(unittest.TestCase):
    def setUp(self):
        units = [
            _u("GLLEIA", "Leia", relic=10),        # owns GL Leia
            _u("REYJEDITRAINING", "Rey JT", relic=7),
            _u("HERASYNDULLAS3", "Hera", relic=3),  # under the R5 target
            # FINN not owned
        ]
        self.r = {x["id"]: x for x in gameplan.gl_readiness(units, GOALS)}
        self.order = [x["id"] for x in gameplan.gl_readiness(units, GOALS)]

    def test_unlocked_flag(self):
        self.assertTrue(self.r["GLLEIA"]["unlocked"])
        self.assertFalse(self.r["GLREY"]["unlocked"])

    def test_missing_and_under_relic(self):
        rey = self.r["GLREY"]
        self.assertEqual(rey["met"], 1)                 # Rey JT R7 meets R5
        self.assertEqual(rey["missing"], ["Finn"])      # Finn not owned
        leia = self.r["GLLEIA"]
        self.assertEqual(leia["under_relic"], [{"name": "Hera", "have": 3, "need": 5}])

    def test_ranks_unstarted_before_unlocked(self):
        self.assertEqual(self.order[0], "GLREY")   # not unlocked comes first
        self.assertEqual(self.order[-1], "GLLEIA")  # owned GL last

    def test_empty_goals(self):
        self.assertEqual(gameplan.gl_readiness([], []), [])


class TestBuildPlanPrompt(unittest.TestCase):
    def test_includes_facts_tokens_meta(self):
        readiness = gameplan.gl_readiness([_u("REYJEDITRAINING", "Rey JT")], GOALS)
        p = gameplan.build_plan_prompt(
            "roster summary here", readiness,
            {"Lightspeed Tokens": "14000"}, ["New Rotta guide"])
        self.assertIn("roster summary here", p)
        self.assertIn("Lightspeed Tokens", p)
        self.assertIn("14000", p)
        self.assertIn("New Rotta guide", p)
        self.assertIn("Supreme Leader Kylo Ren", p)   # a GL from readiness

    def test_omits_token_section_when_blank(self):
        p = gameplan.build_plan_prompt("s", [], {}, [])
        self.assertNotIn("Current token balances", p)


class TestGeneratePlan(unittest.TestCase):
    def test_returns_plan_text(self):
        roster = {"name": "Jaxen", "galactic_power": 7000000,
                  "units": [_u("REYJEDITRAINING", "Rey JT")]}
        out = gameplan.generate_plan(
            roster, GOALS, {"Crystals": "5000"}, ["meta title"],
            gemini_call=lambda prompt, key="": "1. Do this.",
            key="k")
        self.assertEqual(out, "1. Do this.")


class TestRealGoalsFile(unittest.TestCase):
    def test_loads_and_is_sane(self):
        goals = gameplan.load_goals()
        ids = {g["id"] for g in goals}
        self.assertIn("SITHPALPATINE", ids)
        self.assertIn("SUPREMELEADERKYLOREN", ids)
        for g in goals:
            self.assertTrue(g.get("id") and g.get("name"))
            for r in g.get("requirements", []):
                self.assertTrue(r.get("base_id") and r.get("name"))


if __name__ == "__main__":
    unittest.main()
