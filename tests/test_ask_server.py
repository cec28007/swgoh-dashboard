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

    def test_model_is_gemini(self):
        self.assertTrue(ask_server.MODEL.startswith("gemini"))

    def test_text_only(self):
        p = ask_server.build_payload("who wins?", self._base(), None)
        self.assertEqual(p["generationConfig"]["maxOutputTokens"], ask_server.MAX_TOKENS)
        self.assertIn("SWGOH", p["system_instruction"]["parts"][0]["text"])
        parts = p["contents"][0]["parts"]
        self.assertTrue(all("text" in pt for pt in parts))
        self.assertIn("Rotta", parts[0]["text"])
        self.assertEqual(parts[-1]["text"], "Question: who wins?")

    def test_with_image_prepends_inline_data(self):
        img = {"media_type": "image/png", "data": "QUJD"}
        p = ask_server.build_payload("counter this", self._base(), img)
        parts = p["contents"][0]["parts"]
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(parts[0]["inline_data"]["data"], "QUJD")
        self.assertEqual(parts[-1]["text"], "Question: counter this")


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


class TestAskAi(unittest.TestCase):
    def test_requires_key(self):
        with mock.patch.dict(ask_server.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                ask_server.ask_ai("q", {"units": []})

    def test_returns_joined_text_and_passes_image(self):
        captured = {}

        def fake_post(payload, key):
            captured["payload"] = payload
            return {"candidates": [{"content": {"parts": [
                {"text": "Rey "}, {"text": "wins"}]}}]}

        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(ask_server, "post_to_gemini", fake_post):
            out = ask_server.ask_ai("beat this", {"units": []},
                                    {"media_type": "image/png", "data": "QUJD"})
        self.assertEqual(out, "Rey wins")
        self.assertIn("inline_data", captured["payload"]["contents"][0]["parts"][0])

    def test_rejects_bad_image_before_network(self):
        called = {"n": 0}

        def fake_post(payload, key):
            called["n"] += 1
            return {"candidates": []}

        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(ask_server, "post_to_gemini", fake_post):
            with self.assertRaises(ValueError):
                ask_server.ask_ai("q", {"units": []},
                                  {"media_type": "image/tiff", "data": "QQ=="})
        self.assertEqual(called["n"], 0)


class TestAuth(unittest.TestCase):
    def test_pin_ok(self):
        with mock.patch.object(ask_server, "APP_PIN", "1234"):
            self.assertTrue(ask_server.pin_ok("1234"))
            self.assertFalse(ask_server.pin_ok("9999"))
            self.assertFalse(ask_server.pin_ok(""))

    def test_token_roundtrip_and_authed(self):
        with mock.patch.object(ask_server, "AUTH_SECRET", "s3cret"), \
                mock.patch.object(ask_server, "APP_PIN", "1234"):
            tok = ask_server.expected_token("s3cret")
            self.assertTrue(ask_server.authed({"Cookie": f"auth={tok}"}))
            self.assertFalse(ask_server.authed({"Cookie": "auth=nope"}))
            self.assertFalse(ask_server.authed({}))

    def test_lock_disabled_when_no_pin(self):
        with mock.patch.object(ask_server, "APP_PIN", ""):
            self.assertTrue(ask_server.authed({}))


if __name__ == "__main__":
    unittest.main()
