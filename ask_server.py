#!/usr/bin/env python3
"""Tiny server for the dashboard's "Ask" box.

Serves the static dashboard AND a POST /api/ask endpoint that forwards your
question, your roster, and an optional game screenshot to the Gemini API and
returns the answer. The API key lives only in the server's environment — it is
never sent to the browser or committed to git. An optional PIN (APP_PIN) gates
the page and the endpoint.

Run on the Oracle VM (behind Caddy, same as the Tesla setup):
    export GEMINI_API_KEY=AIza...
    export APP_PIN=1234 AUTH_SECRET=long-random-string   # optional lock
    python3 ask_server.py            # listens on 127.0.0.1:8787

Then point Caddy at it (see docs/swgoh.md). Zero dependencies — stdlib only.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("ASK_PORT", "8787"))
# Bind host: 127.0.0.1 for local runs; set ASK_HOST=0.0.0.0 in a container so
# the Caddy front door (a separate container) can reach it.
HOST = os.environ.get("ASK_HOST", "127.0.0.1")
MODEL = os.environ.get("ASK_MODEL", "gemini-2.5-flash")
# Gemini 2.5 spends "thinking" tokens against this budget, so keep it generous.
MAX_TOKENS = int(os.environ.get("ASK_MAX_TOKENS", "4096"))
MAX_IMAGE_BYTES = 4 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Optional PIN lock. When APP_PIN is empty the lock is disabled (local dev).
APP_PIN = os.environ.get("APP_PIN", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")

SYSTEM = (
    "You are a Star Wars: Galaxy of Heroes (SWGOH) strategy advisor. You are "
    "given the user's actual roster as JSON. The user may ALSO attach a "
    "screenshot from the game — an enemy team on a defense, a mod's stats, or a "
    "character screen. When an image is present, read it carefully and answer "
    "the question about that situation, recommending ONLY from characters/units "
    "the user actually owns in the roster. Give concrete, prioritized, specific "
    "advice (who to field, leads, gear/upgrade priorities). Be concise. If the "
    "roster or image lacks data needed to answer, say so."
)


def slim_roster(roster):
    """Trim the roster to the fields that matter so we send fewer tokens."""
    slim = {
        k: roster.get(k)
        for k in ("name", "galactic_power", "character_gp", "ship_gp", "last_updated")
    }
    slim["units"] = [
        {
            "name": u.get("name"), "type": u.get("type"), "stars": u.get("stars"),
            "gear_level": u.get("gear_level"), "relic": u.get("relic"),
            "zetas": u.get("zetas"), "omicrons": u.get("omicrons"), "power": u.get("power"),
        }
        for u in roster.get("units", [])
    ]
    return slim


def expected_token(secret):
    """Deterministic signed token for the auth cookie (one user, one PIN)."""
    return hmac.new(secret.encode(), b"authed", hashlib.sha256).hexdigest()


def pin_ok(pin):
    """Constant-time check of a submitted PIN against APP_PIN."""
    if not APP_PIN:
        return False
    return hmac.compare_digest(str(pin), APP_PIN)


def cookie_token(headers):
    """Pull the `auth` cookie value from the request headers, or None."""
    raw = headers.get("Cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:  # noqa: BLE001 - malformed cookie header
        return None
    morsel = jar.get("auth")
    return morsel.value if morsel else None


def authed(headers):
    """True if the request carries a valid auth cookie, or the lock is disabled."""
    if not APP_PIN:
        return True
    tok = cookie_token(headers)
    if not tok:
        return False
    return hmac.compare_digest(tok, expected_token(AUTH_SECRET))


PIN_PROMPT_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SWGOH — Unlock</title><style>
  body{margin:0;background:#0b0e14;color:#e6edf3;font:16px/1.5 -apple-system,
       BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:grid;
       place-items:center;min-height:100vh}
  form{background:#151a23;border:1px solid #222b39;border-radius:12px;
       padding:24px;width:min(320px,88vw);text-align:center}
  h1{font-size:18px;margin:0 0 14px}
  input{width:100%;padding:12px;font-size:16px;background:#0b0e14;color:#e6edf3;
        border:1px solid #222b39;border-radius:8px;box-sizing:border-box}
  button{margin-top:12px;width:100%;padding:12px;font-size:16px;cursor:pointer;
         background:#ffd54a;color:#111;border:0;border-radius:8px;font-weight:600}
  .err{color:#f87171;font-size:13px;min-height:18px;margin-top:8px}
</style></head><body>
<form id="f"><h1>&#9917; SWGOH Roster</h1>
<input id="pin" type="password" inputmode="numeric" placeholder="Enter PIN" autofocus/>
<button type="submit">Unlock</button><div class="err" id="e"></div></form>
<script>
document.getElementById("f").addEventListener("submit", async (ev)=>{
  ev.preventDefault();
  const pin=document.getElementById("pin").value;
  const r=await fetch("/api/login",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({pin})});
  if(r.ok){location.reload();}
  else{document.getElementById("e").textContent="Wrong PIN.";}
});
</script></body></html>"""


def validate_image(image):
    """Raise ValueError if the attached image is unusable; no-op when None."""
    if image is None:
        return
    if image.get("media_type") not in ALLOWED_MEDIA_TYPES:
        raise ValueError(
            "Unsupported image type — use a PNG, JPEG, GIF, or WebP screenshot."
        )
    try:
        raw = base64.b64decode(image.get("data", ""), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Attached image is not valid base64 data.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Screenshot is too large — keep it under 4 MB.")


def build_payload(question, roster, image):
    """Build the Gemini generateContent request dict.

    image, when present, is {"media_type": str, "data": <base64 str>} and is
    placed as the first part so the model reads the screenshot before the text.
    """
    parts = []
    if image:
        parts.append({"inline_data": {
            "mime_type": image["media_type"], "data": image["data"]}})
    parts.append({"text": f"My roster:\n{json.dumps(slim_roster(roster))}"})
    parts.append({"text": f"Question: {question}"})
    return {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": MAX_TOKENS},
    }


def post_to_gemini(payload, key):
    """Send the request to the Gemini API and return the parsed JSON."""
    url = f"{API_BASE}/{MODEL}:generateContent"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "x-goog-api-key": key,
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask_ai(question, roster, image=None):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in the server environment")
    validate_image(image)
    payload = build_payload(question, roster, image)
    out = post_to_gemini(payload, key)
    candidates = out.get("candidates", [])
    if not candidates:
        raise RuntimeError(out.get("error", {}).get("message", "No answer returned."))
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json", extra_headers=None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        route = self.path.rstrip("/")
        if route == "/api/login":
            try:
                req = self._read_json()
            except Exception:  # noqa: BLE001 - bad JSON body
                return self._send(400, {"error": "bad request"})
            if pin_ok(req.get("pin", "")):
                cookie = (f"auth={expected_token(AUTH_SECRET)}; HttpOnly; Secure; "
                          "SameSite=Lax; Path=/")
                return self._send(200, {"ok": True}, extra_headers={"Set-Cookie": cookie})
            return self._send(401, {"error": "wrong pin"})

        if route == "/api/ask":
            if not authed(self.headers):
                return self._send(401, {"error": "locked"})
            try:
                req = self._read_json()
                answer = ask_ai(
                    req.get("question", ""), req.get("roster", {}), req.get("image"))
                return self._send(200, {"answer": answer})
            except ValueError as e:  # image guard / bad input
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - return the message to the UI
                return self._send(500, {"error": str(e)})

        return self._send(404, {"error": "not found"})

    def do_GET(self):
        # The dashboard page is gated behind the PIN; static data is not.
        if self.path in ("/", "/swgoh.html"):
            if not authed(self.headers):
                return self._send(200, PIN_PROMPT_HTML.encode(), "text/html")
            path = os.path.join(HERE, "swgoh.html")
        else:
            path = os.path.join(HERE, os.path.basename(self.path.lstrip("/")))
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = "text/html" if path.endswith(".html") else "application/javascript"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print(f"Serving dashboard + /api/ask on {HOST}:{PORT} (model: {MODEL})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
