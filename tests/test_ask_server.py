import base64
import unittest
from unittest import mock

import ask_server


class TestSlimRoster(unittest.TestCase):
    def test_keeps_key_fields_and_slims_units(self):
        roster = {
            "name": "Clint", "galactic_power": 9, "character_gp": 5,
            "ship_gp": 4, "last_updated": "2026-07-03", "extra": "drop me",
            "units": [{"name": "Rotta", "type": "character", "stars": 7,
                       "gear_level": 13, "relic": 5, "zetas": 2, "omicrons": 1,
                       "power": 30000, "url": "x", "junk": 1}],
        }
        slim = ask_server.slim_roster(roster)
        self.assertNotIn("extra", slim)
        self.assertEqual(slim["name"], "Clint")
        u = slim["units"][0]
        self.assertEqual(
            set(u),
            {"name", "type", "stars", "gear_level", "relic", "zetas", "omicrons", "power"},
        )
        self.assertEqual(u["name"], "Rotta")


class TestBuildPayload(unittest.TestCase):
    def _base(self):
        return {"name": "C", "units": [{"name": "Rotta", "type": "character", "stars": 7,
                "gear_level": 13, "relic": 5, "zetas": 0, "omicrons": 0, "power": 1}]}

    def test_text_only(self):
        p = ask_server.build_payload("who wins?", self._base(), None)
        self.assertEqual(p["model"], "claude-opus-4-8")
        self.assertEqual(p["max_tokens"], 2048)
        content = p["messages"][0]["content"]
        self.assertTrue(all(b["type"] == "text" for b in content))
        roster_block = content[0]
        self.assertEqual(roster_block["cache_control"], {"type": "ephemeral"})
        self.assertIn("Rotta", roster_block["text"])
        self.assertIn("who wins?", content[-1]["text"])

    def test_with_image_prepends_image_block(self):
        img = {"media_type": "image/png", "data": "QUJD"}
        p = ask_server.build_payload("counter this", self._base(), img)
        content = p["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertEqual(content[0]["source"]["data"], "QUJD")
        self.assertEqual(content[-1]["text"], "Question: counter this")


class TestValidateImage(unittest.TestCase):
    def test_none_ok(self):
        self.assertIsNone(ask_server.validate_image(None))

    def test_bad_media_type(self):
        with self.assertRaises(ValueError):
            ask_server.validate_image({"media_type": "image/tiff", "data": "QQ=="})

    def test_oversize(self):
        big = base64.b64encode(b"x" * (4 * 1024 * 1024 + 1)).decode()
        with self.assertRaises(ValueError):
            ask_server.validate_image({"media_type": "image/png", "data": big})

    def test_ok(self):
        ok = base64.b64encode(b"hello").decode()
        self.assertIsNone(ask_server.validate_image({"media_type": "image/png", "data": ok}))


if __name__ == "__main__":
    unittest.main()
