# Screenshot-aware Ask box — Implementation Plan (Phase 1)

> **For agentic workers:** implement task-by-task, TDD, commit per task. Steps use `- [ ]`.

**Goal:** Add game-screenshot (vision) input + a PIN lock + Opus to the existing `swgoh-dashboard` Ask box, keeping it zero-dependency.

**Architecture:** Refactor `ask_server.py`'s inline logic into small pure helpers (payload builder, image guard, auth) that are unit-testable without a running server; wire them into the existing `BaseHTTPRequestHandler`. Extend `swgoh.html` with an attach/paste control and a PIN gate. Tests use stdlib `unittest` + `mock`.

**Tech Stack:** Python 3.12 standard library only (`http.server`, `urllib`, `hmac`, `hashlib`, `base64`, `json`, `unittest`). Vanilla HTML/JS. Anthropic Messages API over raw `urllib`.

## Global Constraints

- **Zero dependencies — Python standard library only.** No pip installs (no `anthropic` SDK, no `pytest`, no web framework). Tests run via `python3 -m unittest`.
- Secrets live only in the server environment; never committed. `.gitignore` already covers `.env`, `*.key`, `*.pem`.
- Ally code / roster: single-user; the app only ever holds ally `611121817`'s own roster.
- Model default: `claude-opus-4-8` (overridable via `ASK_MODEL`).
- Anthropic API: `POST https://api.anthropic.com/v1/messages`, headers `x-api-key`, `anthropic-version: 2023-06-01`.
- Roster unit fields available: `name, type, stars, gear_level, relic, zetas, omicrons, power`.

---

### Task 1: Test scaffolding + roster slimming helper

**Files:**
- Create: `tests/__init__.py` (empty), `tests/test_ask_server.py`
- Modify: `ask_server.py` (extract `slim_roster`)

**Interfaces:**
- Produces: `slim_roster(roster: dict) -> dict` — top-level `name, galactic_power, character_gp, ship_gp, last_updated` plus `units` list slimmed to `name, type, stars, gear_level, relic, zetas, omicrons, power`.

- [ ] **Step 1: Write failing test** — `tests/test_ask_server.py`:
```python
import unittest
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
        self.assertEqual(set(u), {"name","type","stars","gear_level","relic","zetas","omicrons","power"})
        self.assertEqual(u["name"], "Rotta")

if __name__ == "__main__":
    unittest.main()
```
- [ ] **Step 2: Run, verify fail** — `cd ~/Dev/swgoh-dashboard && python3 -m unittest tests.test_ask_server -v` → FAIL (`slim_roster` missing).
- [ ] **Step 3: Implement** — in `ask_server.py`, extract the slimming currently inside `ask_claude` into module-level `slim_roster(roster)` returning the dict above.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git add -A && git commit -m "refactor: extract slim_roster helper + test scaffolding"`

---

### Task 2: Payload builder (model, max_tokens, roster cache, optional vision block)

**Files:**
- Modify: `ask_server.py` (add `build_payload`, raise `MODEL` default, `MAX_TOKENS`)
- Test: `tests/test_ask_server.py`

**Interfaces:**
- Consumes: `slim_roster`.
- Produces: `build_payload(question: str, roster: dict, image: dict | None) -> dict`.
  `image` = `{"media_type": str, "data": <base64 str>}`. Returns the Anthropic request dict:
  `model=MODEL`, `max_tokens=MAX_TOKENS` (2048), `system=SYSTEM`, and a single user message
  whose `content` is a list: `[image_block?, roster_block(with cache_control), question_block]`.
  The roster block is `{"type":"text","text": "My roster:\\n<json>", "cache_control":{"type":"ephemeral"}}`.
  The question block is `{"type":"text","text":"Question: <q>"}`. Image block (when present) is
  `{"type":"image","source":{"type":"base64","media_type":<mt>,"data":<data>}}` placed FIRST.

- [ ] **Step 1: Write failing tests:**
```python
class TestBuildPayload(unittest.TestCase):
    def _base(self):
        return {"name":"C","units":[{"name":"Rotta","type":"character","stars":7,
                "gear_level":13,"relic":5,"zetas":0,"omicrons":0,"power":1}]}
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
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — add `MAX_TOKENS = int(os.environ.get("ASK_MAX_TOKENS", "2048"))`; change `MODEL` default to `claude-opus-4-8`; write `build_payload` per interface; extend `SYSTEM` to mention an attached game screenshot (enemy team / mod / character screen) to read and answer from the roster.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: vision-capable payload builder, Opus default, roster caching"`

---

### Task 3: Image validation guard

**Files:** Modify `ask_server.py` (`validate_image`); Test `tests/test_ask_server.py`

**Interfaces:**
- Produces: `validate_image(image: dict | None) -> None`. `None` → returns (no-op). Else requires
  `media_type` in `{image/png,image/jpeg,image/gif,image/webp}` and decoded `data` ≤ `MAX_IMAGE_BYTES`
  (4 MB). Violations raise `ValueError` with a clear message.

- [ ] **Step 1: Write failing tests:**
```python
import base64
class TestValidateImage(unittest.TestCase):
    def test_none_ok(self):
        self.assertIsNone(ask_server.validate_image(None))
    def test_bad_media_type(self):
        with self.assertRaises(ValueError):
            ask_server.validate_image({"media_type":"image/tiff","data":"QQ=="})
    def test_oversize(self):
        big = base64.b64encode(b"x" * (4*1024*1024 + 1)).decode()
        with self.assertRaises(ValueError):
            ask_server.validate_image({"media_type":"image/png","data":big})
    def test_ok(self):
        ok = base64.b64encode(b"hello").decode()
        self.assertIsNone(ask_server.validate_image({"media_type":"image/png","data":ok}))
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `validate_image` + `MAX_IMAGE_BYTES = 4*1024*1024`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: image validation guard"`

---

### Task 4: ask_claude wiring (patchable network)

**Files:** Modify `ask_server.py` (`post_to_anthropic`, rework `ask_claude`); Test `tests/test_ask_server.py`

**Interfaces:**
- Produces: `post_to_anthropic(payload: dict, key: str) -> dict` (does the urllib call; the patch point).
  `ask_claude(question: str, roster: dict, image: dict | None = None) -> str`:
  requires `ANTHROPIC_API_KEY` (raise `RuntimeError` if missing), calls `validate_image`,
  `build_payload`, `post_to_anthropic`, returns joined `text` from the response `content`.

- [ ] **Step 1: Write failing tests (patch network + env):**
```python
from unittest import mock
class TestAskClaude(unittest.TestCase):
    def test_requires_key(self):
        with mock.patch.dict(ask_server.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                ask_server.ask_claude("q", {"units": []})
    def test_returns_joined_text_and_passes_image(self):
        captured = {}
        def fake_post(payload, key):
            captured["payload"] = payload
            return {"content": [{"type":"text","text":"Rey "},{"type":"text","text":"wins"}]}
        with mock.patch.dict(ask_server.os.environ, {"ANTHROPIC_API_KEY":"sk-x"}), \
             mock.patch.object(ask_server, "post_to_anthropic", fake_post):
            out = ask_server.ask_claude("beat this", {"units":[]},
                                        {"media_type":"image/png","data":"QUJD"})
        self.assertEqual(out, "Rey wins")
        self.assertEqual(captured["payload"]["messages"][0]["content"][0]["type"], "image")
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — move the urllib call into `post_to_anthropic`; rewrite `ask_claude` to compose the helpers.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "refactor: ask_claude composes helpers; network isolated for tests"`

---

### Task 5: PIN auth helpers

**Files:** Modify `ask_server.py` (auth helpers + `PIN_PROMPT_HTML`); Test `tests/test_ask_server.py`

**Interfaces:**
- Produces:
  - `expected_token(secret: str) -> str` = `hmac_sha256(secret, b"authed").hexdigest()`.
  - `pin_ok(pin: str) -> bool` — constant-time compare against `os.environ["APP_PIN"]`; False if `APP_PIN` unset/empty.
  - `cookie_token(headers) -> str | None` — parse `auth=<v>` from the `Cookie` header (uses `http.cookies`).
  - `authed(headers) -> bool` — True iff `cookie_token` matches `expected_token(AUTH_SECRET)` (constant-time). If `APP_PIN` unset (lock disabled) → always True (local-dev convenience).
  - Constants `AUTH_SECRET = os.environ.get("AUTH_SECRET","")`, `APP_PIN = os.environ.get("APP_PIN","")`.

- [ ] **Step 1: Write failing tests:**
```python
class TestAuth(unittest.TestCase):
    def test_pin_ok(self):
        with mock.patch.object(ask_server, "APP_PIN", "1234"):
            self.assertTrue(ask_server.pin_ok("1234"))
            self.assertFalse(ask_server.pin_ok("9999"))
    def test_token_roundtrip_and_authed(self):
        with mock.patch.object(ask_server, "AUTH_SECRET", "s3cret"), \
             mock.patch.object(ask_server, "APP_PIN", "1234"):
            tok = ask_server.expected_token("s3cret")
            good = {"Cookie": f"auth={tok}"}
            bad = {"Cookie": "auth=nope"}
            self.assertTrue(ask_server.authed(good))
            self.assertFalse(ask_server.authed(bad))
            self.assertFalse(ask_server.authed({}))
    def test_lock_disabled_when_no_pin(self):
        with mock.patch.object(ask_server, "APP_PIN", ""):
            self.assertTrue(ask_server.authed({}))
```
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** helpers; add a minimal `PIN_PROMPT_HTML` string (a styled form POSTing JSON `{pin}` to `/api/login`, then `location.reload()`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: PIN auth helpers (hmac cookie)"`

---

### Task 6: Wire auth + image into the HTTP handler

**Files:** Modify `ask_server.py` (`Handler.do_POST`, `do_GET`)

**Interfaces:** Consumes all helpers above. No new testable pure functions; covered by a live in-process server test in Task 7.

- [ ] **Step 1: Implement handler changes:**
  - `do_POST`: route `/api/login` → read `{pin}`; if `pin_ok`, respond 200 `{ok:true}` with
    `Set-Cookie: auth=<expected_token(AUTH_SECRET)>; HttpOnly; Secure; SameSite=Lax; Path=/`; else 401.
  - `do_POST /api/ask`: if not `authed(self.headers)` → 401 `{error:"locked"}`. Else read
    `{question, roster, image}`; on `ValueError` from `validate_image` → 400 with the message;
    else call `ask_claude(question, roster, image)` and return `{answer}`.
  - `do_GET /` or `/swgoh.html`: if `APP_PIN` set and not `authed` → serve `PIN_PROMPT_HTML` (200, text/html).
    Otherwise serve the page as today. Static assets (`swgoh_data.js`) may remain ungated (roster is
    non-secret public data; the paid endpoint is what's protected) — keep them served as today.
- [ ] **Step 2: Manual smoke** — `APP_PIN=1234 AUTH_SECRET=s ANTHROPIC_API_KEY=x python3 ask_server.py` then
  `curl -s -X POST localhost:8787/api/ask -d '{}'` → expect `{"error": "locked"}` (401).
- [ ] **Step 3: Commit** — `git commit -am "feat: gate page + /api/ask behind PIN; accept image in /api/ask"`

---

### Task 7: Live in-process server integration test

**Files:** Test `tests/test_server_http.py`

**Interfaces:** Starts `ask_server.ThreadingHTTPServer` on an ephemeral port in a thread; patches `post_to_anthropic`.

- [ ] **Step 1: Write test:**
```python
import json, threading, unittest, urllib.request
from unittest import mock
import ask_server
from http.server import ThreadingHTTPServer

class TestHTTP(unittest.TestCase):
    def setUp(self):
        ask_server.APP_PIN = "1234"; ask_server.AUTH_SECRET = "s3cret"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), ask_server.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def tearDown(self):
        self.srv.shutdown()
    def _post(self, path, body, cookie=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
        if cookie: req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers
    def test_ask_locked_then_unlocked(self):
        code, body, _ = self._post("/api/ask", {"question":"q","roster":{"units":[]}})
        self.assertEqual(code, 401)
        code, body, hdrs = self._post("/api/login", {"pin":"1234"})
        self.assertEqual(code, 200)
        cookie = hdrs["Set-Cookie"].split(";")[0]
        with mock.patch.object(ask_server, "post_to_anthropic",
                               lambda p,k: {"content":[{"type":"text","text":"ok"}]}), \
             mock.patch.dict(ask_server.os.environ, {"ANTHROPIC_API_KEY":"x"}):
            code, body, _ = self._post("/api/ask",
                {"question":"q","roster":{"units":[]}}, cookie=cookie)
        self.assertEqual(code, 200); self.assertEqual(body["answer"], "ok")
    def test_bad_pin(self):
        code, _, _ = self._post("/api/login", {"pin":"0000"})
        self.assertEqual(code, 401)
```
- [ ] **Step 2: Run** `python3 -m unittest tests.test_server_http -v` → PASS.
- [ ] **Step 3: Commit** — `git commit -am "test: live server gate + ask integration"`

---

### Task 8: Frontend — attach/paste + preview + PIN gate in `swgoh.html`

**Files:** Modify `swgoh.html`

**Interfaces:** Posts `{question, roster, image?}` to `/api/ask`; posts `{pin}` to `/api/login`.

- [ ] **Step 1: Implement UI:**
  - In `.ask`: add a small **📎 Attach screenshot** button (label wrapping a hidden
    `<input type="file" accept="image/*">`), a hidden `<img>` **thumbnail preview** (max-height ~120px)
    with a **×** remove button. Store the selected image as `{media_type, data}` (base64 from a
    `FileReader.readAsDataURL`, stripping the `data:...base64,` prefix) in a module var `attached`.
  - Add a **paste** listener on the textarea: if `clipboardData` has an image, load it the same way.
  - On **Ask**: include `image: attached` in the POST body when set. Clear `attached` + preview after send.
  - On a **401** from `/api/ask`: show the PIN prompt (a small inline overlay with a password input +
    Unlock button that POSTs `{pin}` to `/api/login`, then retries the ask on success).
- [ ] **Step 2: Verify in a phone-sized browser** (preview tools): load the page at 375px, confirm the
  attach button + textarea + preview render without horizontal scroll; simulate a locked ask → PIN
  prompt appears; unlock → ask works (against a locally-running server with a fake key is not possible,
  so verify the request payload shape via the network panel / a stubbed endpoint).
- [ ] **Step 3: Commit** — `git commit -am "feat: screenshot attach/paste + PIN gate in dashboard UI"`

---

### Task 9: Deploy artifacts + docs

**Files:** Create `.env.example`; Modify `deploy/swgoh-ask.service`, `README.md`, `docs/swgoh.md`

- [ ] **Step 1:** Create `.env.example` listing `ANTHROPIC_API_KEY`, `ASK_MODEL=claude-opus-4-8`,
  `APP_PIN`, `AUTH_SECRET`, `ASK_PORT=8787` (values blank/example, real values never committed).
- [ ] **Step 2:** Update `deploy/swgoh-ask.service`: set `ASK_MODEL=claude-opus-4-8`; add
  `Environment=APP_PIN=` and `Environment=AUTH_SECRET=` (placeholders, filled on the server).
- [ ] **Step 3:** Update `README.md` + `docs/swgoh.md`: document the screenshot feature, the PIN
  (`APP_PIN`/`AUTH_SECRET`), Opus default, and a **Deploy note** that on the Oracle box the dashboard
  attaches to the existing `personal-cloud` Caddy front door (new `$DASH_DOMAIN` block →
  `host.docker.internal:8787` or a dockerized service) rather than starting its own Caddy — to be
  finalized against live server state. HTTPS/domain required for the Secure cookie + clipboard paste.
- [ ] **Step 4:** Run full suite `python3 -m unittest discover -s tests -v` → all PASS.
- [ ] **Step 5: Commit** — `git commit -am "docs: deploy env + screenshot/PIN documentation"`

---

## Self-review notes
- **Spec coverage:** vision (T2,T4,T8) ✓; Opus default (T2) ✓; max_tokens 2048 (T2) ✓; roster caching (T2) ✓; image guard (T3) ✓; PIN + signed cookie gate on page & /api/ask (T5,T6,T7) ✓; single-roster note (structural, no code) ✓; config/env + deploy wiring + domain note (T9) ✓; tests incl. fake-Claude + auth-block (T4,T7) ✓; manual phone verify (T8) ✓; zero-dependency (global constraint, unittest) ✓.
- **Deferred by spec:** follow-up threads; mod grounding; Phase 2 digest — not in this plan (correct).
- **Deploy execution** (running on the Oracle box, real secrets, domain) is the one step left to the user; code is deploy-ready after T9.
