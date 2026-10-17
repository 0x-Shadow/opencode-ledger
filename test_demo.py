"""Tests for opencode_ledger.demo — synthetic stats for demo mode and tests."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import __version__, demo

FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0)


class GenerateTests(unittest.TestCase):
    def stats(self, **kw):
        kw.setdefault("days", 30)
        kw.setdefault("seed", 3)
        kw.setdefault("now", FIXED_NOW)
        return demo.generate_stats(**kw)

    def test_deterministic(self):
        a = self.stats()
        b = self.stats()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_invariants(self):
        s = self.stats()
        t = s["totals"]
        self.assertEqual(s["schema"], 2)
        self.assertEqual(s["version"], __version__)
        self.assertEqual(s["db_path"], "demo")
        self.assertGreater(t["all"], 0)
        self.assertGreater(t["messages"], 0)
        self.assertGreater(t["sessions"], 0)
        self.assertEqual(t["all"], sum(m["tokens"] for m in s["by_model"]))
        self.assertEqual(t["all"], sum(r["tokens"] for r in s["daily_models"]))
        self.assertEqual(t["all"], sum(p["tokens"] for p in s["by_project"]))
        for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
            self.assertEqual(t[key], sum(d[key] for d in s["timeline"]),
                             f"timeline {key} mismatch")

    def test_timeline_is_contiguous(self):
        s = self.stats(days=10)
        dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in s["timeline"]]
        self.assertEqual(len(dates), 10)
        for prev, cur in zip(dates, dates[1:], strict=False):
            self.assertEqual((cur - prev).days, 1)

    def test_shape(self):
        s = self.stats()
        self.assertEqual(len(s["heatmap"]), 7)
        self.assertTrue(all(len(row) == 24 for row in s["heatmap"]))
        self.assertEqual(len(s["by_hour"]), 24)
        self.assertGreater(len(s["by_model"]), 1)
        self.assertGreater(len(s["by_provider"]), 1)
        self.assertGreater(len(s["by_project"]), 1)
        self.assertGreater(len(s["sessions_top"]), 0)
        self.assertLessEqual(len(s["sessions_top"]), 15)

    def test_pricing_annotations_present(self):
        s = self.stats()
        self.assertIn("est_cost", s["totals"])
        paid = [m for m in s["by_model"] if m["priced"] and m["est_cost"]]
        self.assertTrue(paid, "expected at least one priced model in demo data")
        self.assertGreater(s["totals"]["est_cost"], 0)

    def test_days_one(self):
        s = self.stats(days=1)
        self.assertEqual(len(s["timeline"]), 1)
        self.assertEqual(s["days_active"], 1)


class WriteDemoTests(unittest.TestCase):
    def test_write_and_reload(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "stats.json")
            stats = demo.write_demo(path, days=5, seed=1, now=FIXED_NOW)
            with open(path) as f:
                loaded = json.load(f)
        self.assertEqual(loaded["generated"], stats["generated"])
        self.assertEqual(loaded["totals"]["all"], stats["totals"]["all"])

    def test_generated_is_iso_of_now(self):
        s = demo.generate_stats(days=2, seed=2, now=FIXED_NOW)
        self.assertTrue(s["generated"].startswith("2026-06-01T12:00:00"))

    def test_first_last_message_present(self):
        s = demo.generate_stats(days=5, seed=4, now=FIXED_NOW)
        self.assertIsNotNone(s["first_message"])
        self.assertIsNotNone(s["last_message"])
        first = datetime.fromisoformat(s["first_message"])
        last = datetime.fromisoformat(s["last_message"])
        self.assertLess(first, last)
        self.assertLessEqual(last.date(), FIXED_NOW.date())
        self.assertGreater(first.date(), FIXED_NOW.date() - timedelta(days=6))


if __name__ == "__main__":
    unittest.main()
