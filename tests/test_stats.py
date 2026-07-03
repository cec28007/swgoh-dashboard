import unittest

import stats


STATS_RESP = [
    {"id": "UID1", "definitionId": "GLLEIA:SEVEN_STAR", "gp": 57429,
     "stats": {"final": {"Speed": 528, "Health": 161559, "Physical Damage": 6351}}},
    {"id": "UID2", "definitionId": "EXECUTOR:SEVEN_STAR", "gp": 40000,
     "stats": {"final": {"Speed": 0, "Health": 90000}}},
]


class TestBuildStatsMap(unittest.TestCase):
    def test_keys_by_unit_id_with_gp_and_final_stats(self):
        m = stats.build_stats_map(STATS_RESP)
        self.assertEqual(m["UID1"]["gp"], 57429)
        self.assertEqual(m["UID1"]["stats"]["Speed"], 528)
        self.assertEqual(m["UID1"]["stats"]["Health"], 161559)
        self.assertEqual(m["UID2"]["gp"], 40000)

    def test_tolerates_flat_stats_without_final_wrapper(self):
        m = stats.build_stats_map([{"id": "X", "gp": 1, "stats": {"Speed": 300}}])
        self.assertEqual(m["X"]["stats"]["Speed"], 300)

    def test_empty(self):
        self.assertEqual(stats.build_stats_map([]), {})


if __name__ == "__main__":
    unittest.main()
