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
                       "power": 30000, "speed": 250, "stats": {"Health": 90000},
                       "url": "x", "junk": 1}],
        }
        slim = ask_server.slim_roster(roster)
        self.assertNotIn("extra", slim)
        self.assertEqual(slim["name"], "Clint")
        u = slim["units"][0]
        self.assertEqual(
            set(u),
            {"name", "type", "stars", "gear_level", "relic", "zetas", "omicrons",
             "power", "speed", "stats"},
        )
        self.assertEqual(u["name"], "Rotta")
        self.assertEqual(u["speed"], 250)
        self.assertEqual(u["stats"]["Health"], 90000)


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


class TestConversation(unittest.TestCase):
    def _base(self):
        return {"units": [{"name": "Rey", "type": "character"}]}

    def test_first_turn_carries_roster_and_image(self):
        hist = [{"role": "user", "text": "who wins?"}]
        img = {"media_type": "image/png", "data": "QUJD"}
        p = ask_server.build_conversation_payload(hist, self._base(), img)
        parts = p["contents"][0]["parts"]
        self.assertEqual(parts[0]["inline_data"]["data"], "QUJD")   # image on turn 0
        self.assertIn("Rey", parts[-1]["text"])                     # roster on turn 0
        self.assertIn("who wins?", parts[-1]["text"])

    def test_follow_up_turns_are_plain_text(self):
        hist = [{"role": "user", "text": "who wins?"},
                {"role": "model", "text": "Rey wins."},
                {"role": "user", "text": "and the ships?"}]
        p = ask_server.build_conversation_payload(hist, self._base(), None)
        self.assertEqual(len(p["contents"]), 3)
        self.assertEqual(p["contents"][1]["role"], "model")
        self.assertEqual(p["contents"][1]["parts"][0]["text"], "Rey wins.")
        self.assertEqual(p["contents"][2]["parts"][0]["text"], "and the ships?")
        self.assertNotIn("Rey wins.", p["contents"][2]["parts"][0]["text"])

    def test_ask_conversation_returns_text(self):
        hist = [{"role": "user", "text": "hi"}]
        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(ask_server, "post_to_gemini",
                                  lambda p, k: {"candidates": [{"content": {"parts": [
                                      {"text": "hello"}]}}]}):
            self.assertEqual(ask_server.ask_conversation(hist, self._base()), "hello")


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


class TestSummarizeVideo(unittest.TestCase):
    def test_is_youtube_url(self):
        self.assertTrue(ask_server.is_youtube_url("https://www.youtube.com/watch?v=abc123"))
        self.assertTrue(ask_server.is_youtube_url("https://youtu.be/abc123"))
        self.assertFalse(ask_server.is_youtube_url("https://example.com/evil"))
        self.assertFalse(ask_server.is_youtube_url(""))

    def test_build_summary_payload_has_video_and_prompt(self):
        p = ask_server.build_summary_payload("https://www.youtube.com/watch?v=abc123")
        parts = p["contents"][0]["parts"]
        self.assertEqual(parts[0]["file_data"]["file_uri"],
                         "https://www.youtube.com/watch?v=abc123")
        self.assertTrue(parts[-1]["text"])

    def test_rejects_non_youtube(self):
        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "k"}):
            with self.assertRaises(ValueError):
                ask_server.summarize_video("https://example.com/x")

    def test_requires_key(self):
        with mock.patch.dict(ask_server.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                ask_server.summarize_video("https://youtu.be/abc")

    def test_returns_summary_text(self):
        def fake_post(payload, key):
            return {"candidates": [{"content": {"parts": [{"text": "• tip one"}]}}]}
        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(ask_server, "post_to_gemini", fake_post):
            out = ask_server.summarize_video("https://www.youtube.com/watch?v=abc")
        self.assertEqual(out, "• tip one")


class TestBuildStorePayload(unittest.TestCase):
    def test_images_then_prompt(self):
        imgs = [{"media_type": "image/png", "data": "AA"},
                {"media_type": "image/jpeg", "data": "BB"}]
        p = ask_server.build_store_payload(imgs, "PROMPT")
        parts = p["contents"][0]["parts"]
        self.assertEqual(len(parts), 3)                      # 2 images + prompt
        self.assertEqual(parts[0]["inline_data"]["data"], "AA")
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/jpeg")
        self.assertEqual(parts[-1]["text"], "PROMPT")


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
