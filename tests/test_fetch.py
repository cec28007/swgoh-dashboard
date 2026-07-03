import unittest

import fetch_swgoh


SAMPLE = {
    "name": "Jaxen Sol",
    "allyCode": "611121817",
    "level": 85,
    "rosterUnit": [
        {"definitionId": "GLLEIA:SEVEN_STAR", "currentRarity": 7,
         "currentLevel": 85, "currentTier": 13, "relic": {"currentTier": 11},
         "combatType": 1, "skill": []},
        {"definitionId": "EXECUTOR:SEVEN_STAR", "currentRarity": 7,
         "currentLevel": 85, "currentTier": 1, "combatType": 2, "skill": []},
        # combatType absent -> infer ship (no relic, no equipment)
        {"definitionId": "HOMEONE:SEVEN_STAR", "currentRarity": 6,
         "currentLevel": 85, "currentTier": 1, "skill": []},
    ],
}


class TestNormalizeComlink(unittest.TestCase):
    def setUp(self):
        self.r = fetch_swgoh.normalize_comlink(SAMPLE)

    def test_player_fields(self):
        self.assertEqual(self.r["name"], "Jaxen Sol")
        self.assertEqual(self.r["ally_code"], "611121817")
        self.assertTrue(self.r["source"].startswith("comlink"))
        self.assertEqual(len(self.r["units"]), 3)

    def test_character_mapping(self):
        leia = next(u for u in self.r["units"] if u["base_id"] == "GLLEIA")
        self.assertEqual(leia["type"], "character")
        self.assertEqual(leia["name"], "GLLEIA")   # base_id used as name
        self.assertEqual(leia["stars"], 7)
        self.assertEqual(leia["gear_level"], 13)
        self.assertEqual(leia["relic"], 9)          # currentTier 11 -> relic 9
        self.assertEqual(leia["level"], 85)

    def test_ship_detection(self):
        types = {u["base_id"]: u["type"] for u in self.r["units"]}
        self.assertEqual(types["EXECUTOR"], "ship")      # combatType 2
        self.assertEqual(types["HOMEONE"], "ship")       # inferred (no relic)
        execu = next(u for u in self.r["units"] if u["base_id"] == "EXECUTOR")
        self.assertEqual(execu["relic"], 0)

    def test_sorted_by_power_desc(self):
        powers = [u["power"] for u in self.r["units"]]
        self.assertEqual(powers, sorted(powers, reverse=True))

    def test_gp_totals_present(self):
        self.assertGreater(self.r["galactic_power"], 0)
        self.assertEqual(
            self.r["galactic_power"],
            self.r["character_gp"] + self.r["ship_gp"],
        )


if __name__ == "__main__":
    unittest.main()
