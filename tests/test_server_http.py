import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import ask_server


class TestHTTP(unittest.TestCase):
    def setUp(self):
        ask_server.APP_PIN = "1234"
        ask_server.AUTH_SECRET = "s3cret"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), ask_server.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _post(self, path, body, cookie=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def test_ask_locked_then_unlocked(self):
        code, _, _ = self._post("/api/ask", {"question": "q", "roster": {"units": []}})
        self.assertEqual(code, 401)

        code, _, hdrs = self._post("/api/login", {"pin": "1234"})
        self.assertEqual(code, 200)
        cookie = hdrs["Set-Cookie"].split(";")[0]

        with mock.patch.object(ask_server, "post_to_gemini",
                               lambda p, k: {"candidates": [{"content": {"parts": [
                                   {"text": "ok"}]}}]}), \
                mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "x"}):
            code, body, _ = self._post(
                "/api/ask", {"question": "q", "roster": {"units": []}}, cookie=cookie)
        self.assertEqual(code, 200)
        self.assertEqual(body["answer"], "ok")

    def test_bad_pin(self):
        code, _, _ = self._post("/api/login", {"pin": "0000"})
        self.assertEqual(code, 401)

    def test_summarize_gated_then_works(self):
        code, _, _ = self._post("/api/summarize", {"url": "https://youtu.be/x"})
        self.assertEqual(code, 401)  # locked without cookie
        _, _, hdrs = self._post("/api/login", {"pin": "1234"})
        cookie = hdrs["Set-Cookie"].split(";")[0]
        with mock.patch.object(ask_server, "post_to_gemini",
                               lambda p, k: {"candidates": [{"content": {"parts": [
                                   {"text": "• summary"}]}}]}), \
                mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "x"}):
            code, body, _ = self._post(
                "/api/summarize", {"url": "https://www.youtube.com/watch?v=abc"}, cookie=cookie)
        self.assertEqual(code, 200)
        self.assertEqual(body["summary"], "• summary")

    def test_summarize_rejects_non_youtube(self):
        _, _, hdrs = self._post("/api/login", {"pin": "1234"})
        cookie = hdrs["Set-Cookie"].split(";")[0]
        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "x"}):
            code, _, _ = self._post(
                "/api/summarize", {"url": "https://example.com/x"}, cookie=cookie)
        self.assertEqual(code, 400)

    def test_bad_image_returns_400(self):
        code, _, hdrs = self._post("/api/login", {"pin": "1234"})
        cookie = hdrs["Set-Cookie"].split(";")[0]
        with mock.patch.dict(ask_server.os.environ, {"GEMINI_API_KEY": "x"}):
            code, body, _ = self._post(
                "/api/ask",
                {"question": "q", "roster": {"units": []},
                 "image": {"media_type": "image/tiff", "data": "QQ=="}},
                cookie=cookie)
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
