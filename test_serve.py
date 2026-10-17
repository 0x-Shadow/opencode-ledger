"""Tests for opencode_ledger.server — routes, auth, ETag, demo mode."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import config, demo, server

MSG_ID = 0


def make_db(path, n_messages=2, tokens_input=100):
    """Create a minimal opencode-like DB with n assistant messages."""
    global MSG_ID
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS session_message (
          id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL,
          seq integer, time_created integer, time_updated integer, data text);
        CREATE TABLE IF NOT EXISTS session_v2 (
          id text PRIMARY KEY, project_id text, title text, model text);
        CREATE TABLE IF NOT EXISTS project (id text PRIMARY KEY, worktree text);
    """)
    for i in range(n_messages):
        MSG_ID += 1
        data = json.dumps({
            "model": {"id": "test-model", "providerID": "testprov"},
            "tokens": {"input": tokens_input, "output": 1, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
            "cost": 0,
            "time": {"created": 1791114176931 + i * 1000},
            "agent": "build",
        })
        conn.execute(
            "INSERT OR IGNORE INTO session_message VALUES (?,?,?,?,?,?,?)",
            (f"m{MSG_ID}", "s1", "assistant", MSG_ID,
             1791114176931, 1791114176931, data))
    conn.commit()
    conn.close()


def build_cfg(tmp, db_path, **cli):
    cli.setdefault("db_path", db_path)
    cli.setdefault("throttle_seconds", 0.0)
    cli.setdefault("state_file", os.path.join(tmp, "state.json"))
    env = {"HOME": tmp, "XDG_CONFIG_HOME": os.path.join(tmp, ".config")}
    return config.load(cli=cli, env=env, cwd=tmp)


class ServerTestCase(unittest.TestCase):
    """Base: fake DB + doc root with a tiny index.html."""

    demo_stats = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test.db")
        self.out = os.path.join(self.tmp.name, "stats.json")
        self.doc_root = os.path.join(self.tmp.name, "www")
        os.makedirs(self.doc_root)
        with open(os.path.join(self.doc_root, "index.html"), "w") as f:
            f.write("<!DOCTYPE html><title>ledger test</title><p>odometer</p>")
        if self.demo_stats is None:
            make_db(self.db, n_messages=2, tokens_input=100)
        self.cfg = build_cfg(self.tmp.name, self.db)

    def start(self, cfg=None):
        handler = server.make_handler(cfg or self.cfg, self.out,
                                      self.doc_root,
                                      demo_stats=self.demo_stats)
        self.server = server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()


class CoreRouteTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_stats_endpoint(self):
        status, headers, body = self.get("/stats.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Cache-Control"], "no-store")
        stats = json.loads(body)
        self.assertEqual(stats["schema"], 2)
        self.assertEqual(stats["totals"]["all"], 202)
        self.assertEqual(stats["totals"]["messages"], 2)
        self.assertIn("budget_status", stats)
        self.assertIn("est_cost", stats["totals"])

    def test_api_stats_matches_stats_json(self):
        _, _, a = self.get("/stats.json")
        _, _, b = self.get("/api/stats")
        self.assertEqual(json.loads(a)["totals"], json.loads(b)["totals"])

    def test_summary_endpoint(self):
        status, _, body = self.get("/api/summary")
        self.assertEqual(status, 200)
        summary = json.loads(body)
        self.assertEqual(summary["totals"]["all"], 202)
        self.assertIn("version", summary)
        self.assertIn("budget_status", summary)

    def test_healthz(self):
        status, _, body = self.get("/api/healthz")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertTrue(health["ok"])
        self.assertFalse(health["demo"])
        self.assertEqual(health["version"], server.__version__)

    def test_metrics_endpoint(self):
        status, headers, body = self.get("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers["Content-Type"])
        self.assertIn("opencode_ledger_tokens_total 202", body)

    def test_export_csv(self):
        status, headers, body = self.get("/api/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn("attachment", headers["Content-Disposition"])
        lines = body.strip().splitlines()
        self.assertEqual(lines[0], "date,tokens,input,output,reasoning,"
                                   "cache_read,cache_write,messages,cost,est_cost")
        self.assertEqual(len(lines), 2)  # header + one active day
        self.assertIn("202", lines[1])

    def test_index_served_with_security_headers(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("odometer", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

    def test_unknown_route_404(self):
        status, _, _ = self.get("/nope")
        self.assertEqual(status, 404)

    def test_stats_reflects_new_messages_live(self):
        _, _, body = self.get("/stats.json")
        before = json.loads(body)["totals"]["all"]
        make_db(self.db, n_messages=3, tokens_input=100)
        os.utime(self.db, (time.time() + 2, time.time() + 2))
        _, _, body2 = self.get("/stats.json")
        self.assertEqual(json.loads(body2)["totals"]["all"], before + 3 * 101)

    def test_etag_and_304(self):
        status, headers, _ = self.get("/stats.json")
        self.assertEqual(status, 200)
        etag = headers["ETag"]
        self.assertTrue(etag.startswith('"'))
        status2, _, _ = self.get("/stats.json", headers={"If-None-Match": etag})
        self.assertEqual(status2, 304)

    def test_etag_changes_when_data_changes(self):
        _, headers, _ = self.get("/stats.json")
        make_db(self.db, n_messages=3, tokens_input=100)
        os.utime(self.db, (time.time() + 2, time.time() + 2))
        _, headers2, _ = self.get("/stats.json")
        self.assertNotEqual(headers["ETag"], headers2["ETag"])

    def test_budget_status_populated(self):
        cfg = build_cfg(self.tmp.name, self.db, budgets={"daily_tokens": 100})
        self.stop()
        self.start(cfg)
        _, _, body = self.get("/stats.json")
        status = json.loads(body)["budget_status"]
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["id"], "daily_tokens")
        _, _, metrics_body = self.get("/metrics")
        self.assertIn('opencode_ledger_budget_used_ratio{budget="daily_tokens"',
                      metrics_body)


class ThrottleTests(ServerTestCase):
    def test_throttle_serves_cached_between_extractions(self):
        self.start()
        _, _, warm = self.get("/stats.json")
        self.assertEqual(json.loads(warm)["totals"]["all"], 202)
        self.cfg.throttle_seconds = 60.0
        make_db(self.db, n_messages=4, tokens_input=100)
        os.utime(self.db, (time.time() + 2, time.time() + 2))
        _, _, body = self.get("/stats.json")
        self.assertEqual(json.loads(body)["totals"]["all"], 202)


class ErrorTests(ServerTestCase):
    def test_missing_db_returns_500_json_not_crash(self):
        cfg = build_cfg(self.tmp.name, os.path.join(self.tmp.name, "nope.db"))
        self.start(cfg)
        status, _, body = self.get("/stats.json")
        self.assertEqual(status, 500)
        self.assertIn("error", json.loads(body))
        status2, _, body2 = self.get("/api/healthz")
        self.assertEqual(status2, 200)
        self.assertFalse(json.loads(body2)["ok"])


class AuthTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = build_cfg(self.tmp.name, self.db, token="sekrit")
        self.start()

    def test_all_routes_require_auth(self):
        for path in ("/", "/stats.json", "/api/stats", "/api/summary",
                     "/api/healthz", "/metrics", "/api/export.csv"):
            status, _, _ = self.get(path)
            self.assertEqual(status, 401, path)

    def test_wrong_token_rejected(self):
        status, _, _ = self.get("/stats.json",
                                headers={"Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)

    def test_bearer_accepted(self):
        status, _, body = self.get("/stats.json",
                                   headers={"Authorization": "Bearer sekrit"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["totals"]["all"], 202)

    def test_header_token_accepted(self):
        status, _, _ = self.get("/api/summary",
                                headers={"X-Ledger-Token": "sekrit"})
        self.assertEqual(status, 200)

    def test_query_token_accepted_and_sets_cookie(self):
        status, headers, _ = self.get("/api/summary?token=sekrit")
        self.assertEqual(status, 200)
        self.assertIn("ledger_token=sekrit", headers.get("Set-Cookie", ""))

    def test_cookie_accepted(self):
        status, _, _ = self.get("/api/summary",
                                headers={"Cookie": "ledger_token=sekrit"})
        self.assertEqual(status, 200)

    def test_page_reachable_with_cookie(self):
        status, _, body = self.get("/", headers={"Cookie": "ledger_token=sekrit"})
        self.assertEqual(status, 200)
        self.assertIn("odometer", body)

    def test_www_authenticate_header(self):
        _, headers, _ = self.get("/stats.json")
        self.assertIn("Bearer", headers.get("WWW-Authenticate", ""))


class DemoModeTests(ServerTestCase):
    demo_stats = demo.generate_stats(days=10, seed=5)

    def setUp(self):
        super().setUp()
        self.start()

    def test_demo_serves_without_db(self):
        self.assertFalse(os.path.exists(self.db))
        status, _, body = self.get("/stats.json")
        self.assertEqual(status, 200)
        self.assertGreater(json.loads(body)["totals"]["all"], 0)

    def test_healthz_flags_demo(self):
        _, _, body = self.get("/api/healthz")
        health = json.loads(body)
        self.assertTrue(health["demo"])
        self.assertTrue(health["ok"])

    def test_demo_metrics(self):
        _, _, body = self.get("/metrics")
        self.assertIn("opencode_ledger_tokens_total", body)


if __name__ == "__main__":
    unittest.main()
