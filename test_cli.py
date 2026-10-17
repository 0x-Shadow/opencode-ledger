"""Tests for opencode_ledger.cli — command dispatch and root shims."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import __version__, cli

ROOT = os.path.dirname(os.path.abspath(__file__))


def make_db(path, n_messages=2, tokens_input=100):
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
        data = json.dumps({
            "model": {"id": "m", "providerID": "p"},
            "tokens": {"input": tokens_input, "output": 1, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
            "cost": 0, "time": {"created": 1791114176931}, "agent": "build",
        })
        conn.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                     (f"m{i}", "s1", "assistant", i, 1, 1, data))
    conn.commit()
    conn.close()


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return code, out.getvalue(), err.getvalue()


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test.db")
        make_db(self.db)

    def test_version(self):
        code, out, _ = run_cli(["version"])
        self.assertEqual(code, 0)
        self.assertIn(f"opencode-ledger {__version__}", out)

    def test_version_flag(self):
        code, out, _ = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_help_exits_zero(self):
        code, out, _ = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("serve", out)
        self.assertIn("check", out)

    def test_serve_is_default_command(self):
        with mock.patch("opencode_ledger.server.run") as server_run:
            code, _, _ = run_cli(["--port", "0", "--db", self.db])
        self.assertEqual(code, 0)
        server_run.assert_called_once()
        cfg = server_run.call_args[0][0]
        self.assertEqual(cfg.port, 0)
        self.assertEqual(cfg.db_path, self.db)

    def test_serve_demo_flag(self):
        with mock.patch("opencode_ledger.server.run") as server_run:
            code, _, _ = run_cli(["serve", "--demo"])
        self.assertEqual(code, 0)
        self.assertTrue(server_run.call_args.kwargs.get("demo"))

    def test_serve_positional_port(self):
        with mock.patch("opencode_ledger.server.run") as server_run:
            code, _, _ = run_cli(["serve", "9111", "--db", self.db])
        self.assertEqual(code, 0)
        self.assertEqual(server_run.call_args[0][0].port, 9111)

    def test_serve_missing_db_fails_fast(self):
        with mock.patch("opencode_ledger.server.run") as server_run:
            code, _, err = run_cli(["serve", "--db",
                                    os.path.join(self.tmp.name, "nope.db")])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)
        self.assertIn("--demo", err)
        server_run.assert_not_called()

    def test_extract_command(self):
        out_path = os.path.join(self.tmp.name, "out", "stats.json")
        code, out, _ = run_cli(["extract", self.db, out_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_path))
        with open(out_path) as f:
            stats = json.load(f)
        self.assertEqual(stats["totals"]["all"], 202)
        self.assertIn("wrote", out)

    def test_check_json_and_exit_code(self):
        cfg_path = os.path.join(self.tmp.name, "ledger.json")
        with open(cfg_path, "w") as f:
            json.dump({"db_path": self.db, "budgets": {"monthly_tokens": 1},
                       "state_file": os.path.join(self.tmp.name, "state.json")}, f)
        code, out, _ = run_cli(["check", "--json", "--no-alerts",
                                "--config", cfg_path])
        self.assertEqual(code, 1)  # 202 tokens > 1 token budget
        data = json.loads(out)
        self.assertEqual(data["exit_code"], 1)
        self.assertEqual(data["status"][0]["id"], "monthly_tokens")

    def test_check_under_budget(self):
        cfg_path = os.path.join(self.tmp.name, "ledger.json")
        with open(cfg_path, "w") as f:
            json.dump({"db_path": self.db,
                       "budgets": {"daily_tokens": 10_000_000},
                       "state_file": os.path.join(self.tmp.name, "state.json")}, f)
        code, out, _ = run_cli(["check", "--config", cfg_path])
        self.assertEqual(code, 0)
        self.assertIn("daily_tokens", out)

    def test_config_command_json(self):
        code, out, _ = run_cli(["config", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        keys = {row["key"] for row in data["settings"]}
        self.assertIn("port", keys)
        self.assertIn("db_path", keys)

    def test_token_via_flag_is_masked_in_config_json(self):
        code, out, _ = run_cli(["config", "--json", "--token", "sekrit"])
        self.assertEqual(code, 0)
        self.assertNotIn("sekrit", out)


class ShimTests(unittest.TestCase):
    def test_extract_shim_help(self):
        r = subprocess.run([sys.executable, "extract.py"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "HOME": "/nonexistent-home"})
        # Without a real HOME the default DB is missing → clear error, exit 1.
        self.assertIn("error", r.stderr.lower() + r.stdout.lower())

    def test_serve_shim_help(self):
        r = subprocess.run([sys.executable, "serve.py", "--help"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("serve", r.stdout)

    def test_module_entrypoint(self):
        r = subprocess.run([sys.executable, "-m", "opencode_ledger", "version"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(__version__, r.stdout)


if __name__ == "__main__":
    unittest.main()
