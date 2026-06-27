#!/usr/bin/env python3
"""Tiny server for the dashboard's "Ask" box.

Serves the static dashboard AND a POST /api/ask endpoint that forwards your
question plus your roster to the Claude API and returns the answer. The API key
lives only in the server's environment — it is never sent to the browser or
committed to git.

Run on the Oracle VM (behind Caddy, same as the Tesla setup):
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 ask_server.py            # listens on 127.0.0.1:8787

Then point Caddy at it (see docs/swgoh.md). Zero dependencies — stdlib only.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("ASK_PORT", "8787"))
MODEL = os.environ.get("ASK_MODEL", "claude-sonnet-4-6")
API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = (
    "You are a Star Wars: Galaxy of Heroes (SWGOH) strategy advisor. You are "
    "given the user's actual roster as JSON. Give concrete, prioritized advice "
    "about what to farm, gear, or upgrade next, grounded ONLY in the roster "
    "provided. Be specific and concise. If the roster lacks data needed to "
    "answer, say so."
)


def ask_claude(question, roster):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the server environment")
    # Trim the roster to the fields that matter so we send fewer tokens.
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
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": f"My roster:\n{json.dumps(slim)}\n\nQuestion: {question}",
        }],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in out.get("content", []))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.rstrip("/") != "/api/ask":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            answer = ask_claude(req.get("question", ""), req.get("roster", {}))
            self._send(200, {"answer": answer})
        except Exception as e:  # noqa: BLE001 - return the message to the UI
            self._send(500, {"error": str(e)})

    def do_GET(self):
        # Serve the dashboard + its data file; everything else 404s.
        name = "swgoh.html" if self.path in ("/", "/swgoh.html") else self.path.lstrip("/")
        path = os.path.join(HERE, os.path.basename(name))
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ctype = "text/html" if path.endswith(".html") else "application/javascript"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print(f"Serving dashboard + /api/ask on 127.0.0.1:{PORT} (model: {MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
