import unittest

import gamedata


class TestParseLocalization(unittest.TestCase):
    def test_pipe_delimited_skips_comments_and_junk(self):
        text = (
            "# Start Category: foo\n"
            "UNIT_GLLEIA_NAME|Grand Master Leia Organa\n"
            "UNIT_R2D2_LEGENDARY_NAME|R2-D2\n"
            "no_pipe_line_ignored\n"
            "\n"
        )
        d = gamedata.parse_localization(text)
        self.assertEqual(d["UNIT_GLLEIA_NAME"], "Grand Master Leia Organa")
        self.assertEqual(d["UNIT_R2D2_LEGENDARY_NAME"], "R2-D2")
        self.assertNotIn("no_pipe_line_ignored", d)

    def test_value_may_contain_pipes(self):
        d = gamedata.parse_localization("K|a|b|c\n")
        self.assertEqual(d["K"], "a|b|c")


class TestBuildNameMap(unittest.TestCase):
    def test_maps_base_id_to_localized_name(self):
        units = [
            {"id": "GLLEIA", "nameKey": "UNIT_GLLEIA_NAME"},
            {"id": "GLLEIA", "nameKey": "UNIT_GLLEIA_NAME"},   # duplicate ok
            {"id": "MYSTERY", "nameKey": "UNIT_MISSING_NAME"},
        ]
        loc = {"UNIT_GLLEIA_NAME": "Grand Master Leia Organa"}
        names = gamedata.build_name_map(units, loc)
        self.assertEqual(names["GLLEIA"], "Grand Master Leia Organa")
        # missing localization falls back to the base id
        self.assertEqual(names["MYSTERY"], "MYSTERY")


class TestBuildIconMap(unittest.TestCase):
    def test_base_id_to_thumbnail(self):
        units = [{"baseId": "GLLEIA", "thumbnailName": "tex.charui_leiaendor"},
                 {"id": "REY", "thumbnailName": "tex.charui_rey"},
                 {"baseId": "NOICON"}]
        m = gamedata.build_icon_map(units)
        self.assertEqual(m["GLLEIA"], "tex.charui_leiaendor")
        self.assertEqual(m["REY"], "tex.charui_rey")
        self.assertNotIn("NOICON", m)   # skip units without a thumbnail


class TestBuildSkillMap(unittest.TestCase):
    def test_finds_zeta_and_omicron_tiers(self):
        skills = [
            {"id": "leader_GLLEIA", "tier": [
                {"isZetaTier": False, "isOmicronTier": False},
                {"isZetaTier": True, "isOmicronTier": False},
                {"isZetaTier": False, "isOmicronTier": True},
            ]},
            {"id": "basic_GLLEIA", "tier": [
                {"isZetaTier": False, "isOmicronTier": False},
            ]},
        ]
        m = gamedata.build_skill_map(skills)
        self.assertEqual(m["leader_GLLEIA"]["zeta_tier"], 1)     # 0-based index
        self.assertEqual(m["leader_GLLEIA"]["omicron_tier"], 2)
        self.assertIsNone(m["basic_GLLEIA"]["zeta_tier"])
        self.assertIsNone(m["basic_GLLEIA"]["omicron_tier"])


class TestCountZetaOmi(unittest.TestCase):
    def _map(self):
        # 0-based tier indices, matching /player's 0-based skill.tier
        return {"leader_GLLEIA": {"zeta_tier": 1, "omicron_tier": 2},
                "basic_GLLEIA": {"zeta_tier": None, "omicron_tier": None}}

    def test_counts_applied_zetas_and_omicrons(self):
        skills = [{"id": "leader_GLLEIA", "tier": 2}, {"id": "basic_GLLEIA", "tier": 0}]
        self.assertEqual(gamedata.count_zeta_omi(skills, self._map()), (1, 1))

    def test_not_yet_reached(self):
        skills = [{"id": "leader_GLLEIA", "tier": 0}]  # below zeta tier index 1
        self.assertEqual(gamedata.count_zeta_omi(skills, self._map()), (0, 0))

    def test_zeta_but_not_omicron(self):
        skills = [{"id": "leader_GLLEIA", "tier": 1}]  # >=1 zeta, <2 omicron
        self.assertEqual(gamedata.count_zeta_omi(skills, self._map()), (1, 0))

    def test_zeta_at_tier_zero_is_valid(self):
        m = {"x": {"zeta_tier": 0, "omicron_tier": None}}
        self.assertEqual(gamedata.count_zeta_omi([{"id": "x", "tier": 0}], m), (1, 0))


class TestGpTotals(unittest.TestCase):
    def test_pulls_three_gp_stats(self):
        profile = [
            {"nameKey": "STAT_GALACTIC_POWER_ACQUIRED_NAME", "value": "7285105"},
            {"nameKey": "STAT_CHARACTER_GALACTIC_POWER_ACQUIRED_NAME", "value": "4545686"},
            {"nameKey": "STAT_SHIP_GALACTIC_POWER_ACQUIRED_NAME", "value": "2739419"},
            {"nameKey": "STAT_OTHER", "value": "1"},
        ]
        gp = gamedata.gp_totals(profile)
        self.assertEqual(gp["galactic_power"], 7285105)
        self.assertEqual(gp["character_gp"], 4545686)
        self.assertEqual(gp["ship_gp"], 2739419)

    def test_missing_returns_none(self):
        self.assertEqual(gamedata.gp_totals([]),
                         {"galactic_power": None, "character_gp": None, "ship_gp": None})


if __name__ == "__main__":
    unittest.main()
