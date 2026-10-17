"""Minimal stdlib-only regression suite.

Run with:  make test   (or: python3 -m unittest discover -s tests -v)

Everything runs against synthetic databases created in a temporary
directory — no real OpenCode database or user data is ever touched.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from opencode_ledger import (  # noqa: E402
    __version__,
    budget,
    config,
    extract,  # noqa: E402
)
from opencode_ledger.pricing import PricingTable  # noqa: E402


def message(inp=0, out=0, reason=0, cr=0, cw=0, model="gpt-5-mini",
            provider="openai", ts=1700000000000):
    """One well-formed assistant message payload."""
    return json.dumps({
        "tokens": {"input": inp, "output": out, "reasoning": reason,
                   "cache": {"read": cr, "write": cw}},
        "model": {"id": model, "providerID": provider},
        "time": {"created": ts},
    })


def make_db(path, rows, with_session_meta=True):
    """Isolated synthetic database with only the columns we read."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session_message (id TEXT PRIMARY KEY, "
                 "session_id TEXT, type TEXT, data TEXT)")
    if with_session_meta:
        conn.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, "
                     "title TEXT, model TEXT, project_id TEXT)")
        conn.execute("CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT)")
        conn.execute("INSERT INTO project VALUES ('p1', '/home/dev/myproj')")
        conn.execute("INSERT INTO session_v2 VALUES ('s1', 'Fix the bug', ?, 'p1')",
                     (json.dumps({"id": "gpt-5-mini"}),))
    for i, (sid, data) in enumerate(rows):
        conn.execute("INSERT INTO session_message VALUES (?, ?, 'assistant', ?)",
                     (f"m{i}", sid, data))
    conn.commit()
    conn.close()


class ParseMessageTests(unittest.TestCase):
    def test_valid_payload(self):
        parsed = extract.parse_message(message(inp=1, out=2, reason=3, cr=4, cw=5))
        self.assertIsInstance(parsed, dict)

    def test_malformed_payloads(self):
        bad = [
            '{"broken": ',                       # invalid JSON
            '"just a string"',                   # not an object
            json.dumps({}),                      # no tokens/model
            json.dumps({"tokens": {"input": 1}}),  # no model
            json.dumps({"tokens": {"input": "x", "output": 1, "reasoning": 1,
                                   "cache": {"read": 0, "write": 0}},
                        "model": {"id": "m"}}),  # mistyped token
            json.dumps({"tokens": {"input": 1, "output": 1, "reasoning": 1},
                        "model": {"id": "m"}}),  # no cache object
        ]
        for payload in bad:
            with self.subTest(payload=payload[:40]):
                self.assertIsNone(extract.parse_message(payload))


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "synthetic.db")
        self.out = os.path.join(self.tmp.name, "stats.json")
        rows = [
            ("s1", message(100, 10, 5, 40, 7)),
            ("s1", message(0, 0, 0, 0, 0)),
            ("s2", message(1_000_000, 2, 3, 4, 5)),
            ("s1", '{"broken": '),
            ("s2", json.dumps({"tokens": {"input": 1}})),
        ]
        make_db(self.db, rows)

    def test_totals_are_exact(self):
        stats = extract.run(self.db, self.out)
        t = stats["totals"]
        self.assertEqual(t["input"], 1_000_100)
        self.assertEqual(t["output"], 12)
        self.assertEqual(t["reasoning"], 8)
        self.assertEqual(t["cache_read"], 44)
        self.assertEqual(t["cache_write"], 12)
        self.assertEqual(t["all"], 1_000_176)
        self.assertEqual(
            t["all"], t["input"] + t["output"] + t["reasoning"]
            + t["cache_read"] + t["cache_write"])
        self.assertEqual(t["messages"], 3)
        self.assertEqual(t["sessions"], 2)
        self.assertEqual(stats["skipped_rows"], 2)
        self.assertEqual(stats["schema"], 2)
        self.assertEqual(stats["version"], __version__)

    def test_written_file_is_private(self):
        extract.run(self.db, self.out)
        with open(self.out) as f:
            self.assertEqual(json.load(f)["totals"]["messages"], 3)
        if os.name != "nt":
            self.assertEqual(os.stat(self.out).st_mode & 0o777, 0o600)

    def test_database_without_session_meta_still_works(self):
        db = os.path.join(self.tmp.name, "old.db")
        make_db(db, [("s1", message(5, 5))], with_session_meta=False)
        stats = extract.run(db, self.out)
        self.assertEqual(stats["totals"]["all"], 10)
        self.assertEqual(stats["totals"]["sessions"], 1)

    def test_incompatible_databases_error_clearly(self):
        empty = os.path.join(self.tmp.name, "empty.db")
        open(empty, "w").close()
        with self.assertRaises(extract.DatabaseIncompatible):
            extract.run(empty, self.out)
        junk = os.path.join(self.tmp.name, "junk.db")
        with open(junk, "w") as f:
            f.write("definitely not sqlite")
        with self.assertRaises(extract.DatabaseIncompatible):
            extract.run(junk, self.out)
        with self.assertRaises(FileNotFoundError):
            extract.run(os.path.join(self.tmp.name, "missing.db"), self.out)

    def test_locked_database_reports_busy_not_incompatible(self):
        db = os.path.join(self.tmp.name, "locked.db")
        make_db(db, [("s1", message(5, 5))])
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sqlite3, sys, time\n"
             "conn = sqlite3.connect(sys.argv[1])\n"
             "conn.execute('BEGIN EXCLUSIVE')\n"
             "print('holding', flush=True)\n"
             "time.sleep(15)\n", db],
            stdout=subprocess.PIPE, text=True)

        def _stop():
            holder.kill()
            holder.wait(timeout=5)

        self.addCleanup(_stop)
        self.assertEqual(holder.stdout.readline().strip(), "holding")
        with self.assertRaises(extract.DatabaseBusy):
            extract.run(db, self.out)

    def test_reserved_characters_in_filename(self):
        sub = os.path.join(self.tmp.name, "sub #1?%")
        os.makedirs(sub)
        db = os.path.join(sub, "oc #1?%.db")
        make_db(db, [("s1", message(100, 10, 5, 40, 7))])
        stats = extract.run(db, self.out)
        self.assertEqual(stats["totals"]["all"], 162)


class PricingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.table = PricingTable(
            overrides={
                "openai/gpt-5": {"input": 10, "output": 20},
                "gpt-5-mini": {"input": 1, "output": 2},
                "free/model": {"input": 0, "output": 0},
            },
            path=os.path.join(tmp.name, "no-such-pricing.json"))

    def test_provider_qualified_key_wins(self):
        self.assertEqual(self.table.price_for("openai", "gpt-5"),
                         {"input": 10, "output": 20})

    def test_bare_model_fallback(self):
        self.assertEqual(self.table.price_for("someone-else", "gpt-5-mini"),
                         {"input": 1, "output": 2})

    def test_unknown_model_is_unpriced(self):
        self.assertIsNone(self.table.price_for("openai", "unknown-model"))
        self.assertIsNone(self.table.price_for("openai", None))

    def test_missing_components_default_to_input_price(self):
        # cache_read/cache_write fall back to the input price; reasoning to output.
        cost = self.table.estimate(
            {"input": 1_000_000, "output": 0, "reasoning": 0,
             "cache_read": 1_000_000, "cache_write": 0},
            {"input": 1, "output": 2})
        self.assertAlmostEqual(cost, 2.0)

    def test_zero_cost_model(self):
        price = self.table.price_for("opencode", "free/model")
        self.assertEqual(self.table.estimate(
            {"input": 9_999_999, "output": 9_999_999, "reasoning": 0,
             "cache_read": 0, "cache_write": 0}, price), 0.0)


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BudgetTests(unittest.TestCase):
    def _cfg(self, budgets, alerts=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return config.Config(
            db_path=os.path.join(tmp.name, "db"),
            host="127.0.0.1", port=8765, token=None,
            throttle_seconds=3.0, poll_seconds=4, stale_seconds=30,
            budgets=budgets,
            alerts=alerts or {"thresholds": [0.8, 1.0],
                              "webhook_url": "https://example.com/hook",
                              "webhook_format": "generic"},
            pricing={}, pricing_file=None,
            state_file=os.path.join(tmp.name, "state.json"),
            output=os.path.join(tmp.name, "stats.json"))

    def _stats(self, tokens, cost=0.0):
        today = datetime.now().strftime("%Y-%m-%d")
        return {"timeline": [{"date": today, "tokens": tokens, "cost": cost}]}

    def test_over_budget_alerts_once_and_exits_1(self):
        cfg = self._cfg({"daily_tokens": 100})
        sent = []
        opener = lambda req, timeout=None: sent.append(req.full_url) or _FakeResponse()  # noqa: E731

        first = budget.check(cfg, stats=self._stats(150), send=True, opener=opener)
        self.assertEqual(first["exit_code"], 1)
        self.assertEqual(len(first["sent"]), 2)  # crossed 80% and 100%
        self.assertEqual(len(sent), 2)

        second = budget.check(cfg, stats=self._stats(150), send=True, opener=opener)
        self.assertEqual(second["sent"], [])     # deduplicated via state file
        self.assertEqual(len(sent), 2)

    def test_under_budget_exits_0(self):
        cfg = self._cfg({"daily_tokens": 10_000})
        result = budget.check(cfg, stats=self._stats(150),
                              send=True, opener=lambda req, timeout=None: _FakeResponse())
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["sent"], [])


class ConfigValidationTests(unittest.TestCase):
    def _load(self, cli=None, env=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return config.load(cli=cli or {}, env=env or {}, cwd=tmp.name)

    def test_rejects_non_finite_and_boolean_numbers(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError):
                    self._load(cli={"budgets": {"daily_tokens": value}})

    def test_rejects_bad_thresholds(self):
        for value in (float("nan"), 0, -1, "x"):
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError):
                    self._load(cli={"alerts": {"thresholds": [value]}})

    def test_rejects_bad_webhook_urls(self):
        for url in ("ntfy.sh/x", "ftp://example.com", "https://",
                    "https://user:pw@example.com/hook", 123):
            with self.subTest(url=url):
                with self.assertRaises(config.ConfigError):
                    self._load(cli={"alerts": {"webhook_url": url}})

    def test_rejects_bad_port(self):
        with self.assertRaises(config.ConfigError):
            self._load(env={"OPENCODE_LEDGER_PORT": "abc"})
        with self.assertRaises(config.ConfigError):
            self._load(cli={"port": 70000})

    def test_valid_config_and_describe(self):
        cfg = self._load(cli={"alerts": {"webhook_url": "https://ntfy.sh/ledger"},
                              "output": "out/stats.json"})
        self.assertEqual(cfg.alerts["webhook_url"], "https://ntfy.sh/ledger")
        self.assertEqual(cfg.output, "out/stats.json")
        keys = {row["key"] for row in config.describe(cfg)}
        self.assertIn("output", keys)


class ServerAuthTests(unittest.TestCase):
    """End-to-end: real process, synthetic demo data, no database touched."""

    TOKEN = "test-token-123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A file that must never be reachable over HTTP (static-file probe).
        with open(os.path.join(self.tmp.name, "secret-marker.txt"), "w") as f:
            f.write("top secret")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "opencode_ledger", "serve", "--demo",
             "--port", str(self.port), "--token", self.TOKEN],
            cwd=self.tmp.name, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop)
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with self._get("/api/healthz", token=True):
                    return
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.25)
        self.fail("server did not start")

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def _get(self, path, token=False, method="GET"):
        req = urllib.request.Request(self.base + path, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {self.TOKEN}")
        return urllib.request.urlopen(req, timeout=5)

    def _status(self, path, token=False, method="GET"):
        try:
            with self._get(path, token=token, method=method) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            return code

    def test_get_requires_token(self):
        self.assertEqual(self._status("/api/healthz"), 401)

    def test_head_requires_token(self):
        # Regression: HEAD used to fall through to the inherited static-file
        # handler, bypassing auth entirely.
        self.assertEqual(self._status("/api/healthz", method="HEAD"), 401)

    def test_head_cannot_serve_files(self):
        self.assertEqual(self._status("/secret-marker.txt", method="HEAD"), 401)
        # Even authenticated, arbitrary files are not routes.
        self.assertEqual(
            self._status("/secret-marker.txt", token=True, method="HEAD"), 404)

    def test_other_methods_cannot_reach_files(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                # Unauthenticated: 401, same as GET/HEAD.
                self.assertEqual(
                    self._status("/secret-marker.txt", method=method), 401)
                # Authenticated: 405 + Allow; the path is never resolved.
                req = urllib.request.Request(
                    self.base + "/secret-marker.txt", method=method)
                req.add_header("Authorization", f"Bearer {self.TOKEN}")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(ctx.exception.code, 405)
                self.assertEqual(
                    ctx.exception.headers.get("Allow"), "GET, HEAD")
                self.assertNotIn(b"top secret", ctx.exception.read())
                ctx.exception.close()
        # Unknown methods fall back to the stock 501 — still no file access.
        self.assertEqual(
            self._status("/secret-marker.txt", method="FOOBAR"), 501)

    def test_authorized_requests_work(self):
        with self._get("/api/ready", token=True) as resp:
            payload = json.loads(resp.read())
        self.assertIs(payload["ready"], True)
        with self._get("/", token=True) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers["Content-Type"])
        self.assertEqual(self._status("/api/healthz", token=True, method="HEAD"), 200)


if __name__ == "__main__":
    unittest.main()
