"""Tests for serve.py — the live dashboard server."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve

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


class FakeDBLiveServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self.stats_path = os.path.join(self.tmp.name, "stats.json")
        make_db(self.db, n_messages=2, tokens_input=100)
        self.handler = serve.make_handler(
            db_path=self.db, out_path=self.stats_path,
            doc_root=os.path.dirname(os.path.abspath(__file__)),
            throttle=0.0)
        self.server = serve.ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()

    def test_stats_endpoint_serves_aggregates(self):
        status, body = self.get("/stats.json")
        self.assertEqual(status, 200)
        stats = json.loads(body)
        # 2 messages × (100 in + 1 out) = 202
        self.assertEqual(stats["totals"]["all"], 202)
        self.assertEqual(stats["totals"]["messages"], 2)

    def test_stats_reflects_new_messages_live(self):
        _, body = self.get("/stats.json")
        before = json.loads(body)["totals"]["all"]
        make_db(self.db, n_messages=3, tokens_input=100)  # +3 messages
        # mtime granularity: force a newer mtime
        os.utime(self.db, (time.time() + 2, time.time() + 2))
        _, body2 = self.get("/stats.json")
        after = json.loads(body2)["totals"]["all"]
        self.assertEqual(after, before + 3 * 101)

    def test_throttle_serves_cached_between_extractions(self):
        _, warm = self.get("/stats.json")  # warm the cache first
        self.assertEqual(json.loads(warm)["totals"]["all"], 202)
        self.handler.throttle = 60.0  # effectively disable re-extraction
        make_db(self.db, n_messages=4, tokens_input=100)
        os.utime(self.db, (time.time() + 2, time.time() + 2))
        _, body = self.get("/stats.json")
        # still the cached 202 (2 msgs), not 606 (6 msgs)
        self.assertEqual(json.loads(body)["totals"]["all"], 202)

    def test_index_served(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("odometer", body)

    def test_missing_db_returns_500_not_crash(self):
        bad = serve.make_handler(
            db_path=os.path.join(self.tmp.name, "nope.db"),
            out_path=self.stats_path, doc_root=self.tmp.name, throttle=0.0)
        server = serve.ThreadingHTTPServer(("127.0.0.1", 0), bad)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/stats.json"
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url, timeout=10)
            self.assertEqual(ctx.exception.code, 500)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
