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


ENRICHED = {
    "name": "Jaxen Sol", "allyCode": "611121817",
    "profileStat": [
        {"nameKey": "STAT_GALACTIC_POWER_ACQUIRED_NAME", "value": "7285105"},
        {"nameKey": "STAT_CHARACTER_GALACTIC_POWER_ACQUIRED_NAME", "value": "4545686"},
        {"nameKey": "STAT_SHIP_GALACTIC_POWER_ACQUIRED_NAME", "value": "2739419"},
    ],
    "rosterUnit": [
        {"id": "U_LEIA", "definitionId": "GLLEIA:SEVEN_STAR", "currentRarity": 7,
         "currentLevel": 85, "currentTier": 13, "relic": {"currentTier": 11},
         "combatType": 1,
         "skill": [{"id": "leader_GLLEIA", "tier": 3}, {"id": "basic_GLLEIA", "tier": 1}]},
    ],
}
MAPS = {
    "version": "x",
    "names": {"GLLEIA": "Grand Master Leia Organa"},
    "skills": {"leader_GLLEIA": {"zeta_tier": 2, "omicron_tier": 3},
               "basic_GLLEIA": {"zeta_tier": None, "omicron_tier": None}},
}


class TestNormalizeComlinkEnriched(unittest.TestCase):
    def setUp(self):
        self.r = fetch_swgoh.normalize_comlink(ENRICHED, MAPS)
        self.leia = self.r["units"][0]

    def test_real_name(self):
        self.assertEqual(self.leia["name"], "Grand Master Leia Organa")

    def test_real_zeta_omicron_counts(self):
        self.assertEqual(self.leia["zetas"], 1)
        self.assertEqual(self.leia["omicrons"], 1)

    def test_real_gp_totals_from_profile_stat(self):
        self.assertEqual(self.r["galactic_power"], 7285105)
        self.assertEqual(self.r["character_gp"], 4545686)
        self.assertEqual(self.r["ship_gp"], 2739419)


class TestNormalizeComlinkStats(unittest.TestCase):
    def setUp(self):
        stats_map = {"U_LEIA": {"gp": 57429, "stats": {"Speed": 528, "Health": 161559}}}
        self.leia = fetch_swgoh.normalize_comlink(ENRICHED, MAPS, stats_map)["units"][0]

    def test_real_per_unit_gp_becomes_power(self):
        self.assertEqual(self.leia["power"], 57429)

    def test_speed_surfaced(self):
        self.assertEqual(self.leia["speed"], 528)

    def test_full_stats_attached(self):
        self.assertEqual(self.leia["stats"]["Health"], 161559)


if __name__ == "__main__":
    unittest.main()
