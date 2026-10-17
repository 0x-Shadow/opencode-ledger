"""Tests for opencode_ledger.extract — the token usage extractor (schema 2)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import extract

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")


def make_row(model_id="m", provider="p", variant=None, inp=0, out=0,
             reasoning=0, cache_read=0, cache_write=0, ts=1791114176931,
             agent="build", cost=0.0):
    return {
        "model": {"id": model_id, "providerID": provider,
                  **({"variant": variant} if variant else {})},
        "tokens": {"input": inp, "output": out, "reasoning": reasoning,
                   "cache": {"read": cache_read, "write": cache_write}},
        "cost": cost,
        "time": {"created": ts},
        "agent": agent,
    }


def local_ms(year, month, day, hour=12, minute=0):
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


class ParseMessageTests(unittest.TestCase):
    def test_parse_message_valid(self):
        row = make_row(inp=10, out=5, reasoning=2, cache_read=7, cache_write=3)
        parsed = extract.parse_message(json.dumps(row))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["model"]["id"], "m")
        self.assertEqual(parsed["tokens"]["cache"]["read"], 7)

    def test_parse_message_missing_tokens(self):
        row = make_row()
        del row["tokens"]
        self.assertIsNone(extract.parse_message(json.dumps(row)))

    def test_parse_message_missing_model(self):
        row = make_row()
        del row["model"]
        self.assertIsNone(extract.parse_message(json.dumps(row)))

    def test_parse_message_malformed(self):
        self.assertIsNone(extract.parse_message("not json{"))

    def test_parse_message_nested_null_tokens(self):
        row = make_row(inp=10)
        row["tokens"]["reasoning"] = None
        row["tokens"]["cache"] = None
        self.assertIsNone(extract.parse_message(json.dumps(row)))

    def test_parse_message_model_not_dict(self):
        row = make_row()
        row["model"] = "just-a-string"
        self.assertIsNone(extract.parse_message(json.dumps(row)))

    def test_parse_message_cache_not_dict(self):
        row = make_row()
        row["tokens"]["cache"] = [1, 2]
        self.assertIsNone(extract.parse_message(json.dumps(row)))

    def test_aggregate_skips_bad_nested_rows(self):
        bad = make_row(inp=100)
        bad["tokens"]["reasoning"] = None
        rows = [make_row(inp=10), bad, make_row(inp=20)]
        rows[2]["model"] = "oops"
        good = [r for r in rows if extract.parse_message(json.dumps(r))]
        stats = extract.aggregate(good, session_meta={})
        self.assertEqual(stats["totals"]["messages"], 1)
        self.assertEqual(stats["totals"]["input"], 10)


class AggregateTests(unittest.TestCase):
    def test_aggregate_totals(self):
        rows = [
            make_row(model_id="a", provider="prov1", inp=100, out=50,
                     reasoning=10, cache_read=5, cache_write=2,
                     ts=1791114176931),
            make_row(model_id="a", provider="prov1", inp=200, out=0,
                     reasoning=0, cache_read=0, cache_write=0,
                     ts=1791114176931),
            make_row(model_id="b", provider="prov2", variant="high",
                     inp=1000, out=1, reasoning=0, cache_read=0,
                     cache_write=0, ts=1791200576931, agent="general"),
        ]
        stats = extract.aggregate(rows, session_meta={})
        t = stats["totals"]
        self.assertEqual(t["input"], 1300)
        self.assertEqual(t["output"], 51)
        self.assertEqual(t["reasoning"], 10)
        self.assertEqual(t["cache_read"], 5)
        self.assertEqual(t["cache_write"], 2)
        self.assertEqual(t["all"], 1300 + 51 + 10 + 5 + 2)
        self.assertEqual(t["messages"], 3)
        self.assertEqual(stats["schema"], 2)
        self.assertEqual(len(stats["by_model"]), 2)
        self.assertEqual(stats["by_model"][0]["model"], "b")
        self.assertEqual(stats["by_model"][0]["variant"], "high")
        self.assertEqual(stats["by_model"][0]["tokens"], 1001)
        self.assertEqual(stats["by_model"][1]["tokens"], 367)
        # by_model carries components for cost estimation
        m_a = next(m for m in stats["by_model"] if m["model"] == "a")
        self.assertEqual(m_a["input"], 300)
        self.assertEqual(m_a["reasoning"], 10)
        self.assertEqual(m_a["cache_read"], 5)
        provs = {p["provider"]: p for p in stats["by_provider"]}
        self.assertEqual(provs["prov1"]["messages"], 2)
        self.assertEqual(provs["prov1"]["tokens"], 367)
        self.assertEqual(provs["prov2"]["tokens"], 1001)
        self.assertEqual(len(stats["timeline"]), 2)
        self.assertEqual(sum(d["messages"] for d in stats["timeline"]), 3)
        self.assertEqual(len(stats["monthly"]), 1)
        self.assertEqual(stats["monthly"][0]["messages"], 3)
        self.assertEqual(stats["best_day"]["tokens"], 1001)
        self.assertEqual(stats["days_active"], 2)
        self.assertEqual(stats["skipped_rows"], 0)
        self.assertIsNotNone(stats["first_message"])
        self.assertIsNotNone(stats["last_message"])

    def test_timeline_components_and_daily_models(self):
        rows = [
            make_row(model_id="a", provider="p1", inp=100, out=10,
                     reasoning=1, cache_read=5, cache_write=2,
                     ts=local_ms(2026, 3, 2, 10, 0), cost=0.5),
            make_row(model_id="a", provider="p1", inp=200, out=20,
                     ts=local_ms(2026, 3, 2, 11, 0), cost=1.5),
            make_row(model_id="b", provider="p2", inp=7,
                     ts=local_ms(2026, 3, 3, 9, 0)),
        ]
        stats = extract.aggregate(rows, session_meta={})
        by_date = {d["date"]: d for d in stats["timeline"]}
        d2 = by_date["2026-03-02"]
        self.assertEqual(d2["input"], 300)
        self.assertEqual(d2["output"], 30)
        self.assertEqual(d2["reasoning"], 1)
        self.assertEqual(d2["cache_read"], 5)
        self.assertEqual(d2["cache_write"], 2)
        self.assertEqual(d2["tokens"], 338)
        self.assertAlmostEqual(d2["cost"], 2.0)

        dm = stats["daily_models"]
        self.assertEqual(len(dm), 2)
        first = [r for r in dm if r["model"] == "a" and r["date"] == "2026-03-02"][0]
        self.assertEqual(first["provider"], "p1")
        self.assertEqual(first["tokens"], 338)
        self.assertEqual(first["messages"], 2)
        self.assertEqual(first["input"], 300)
        self.assertAlmostEqual(first["cost"], 2.0)

    def test_by_project_and_project_sessions(self):
        rows = [
            make_row(inp=100, ts=local_ms(2026, 3, 2)),
            make_row(inp=50, ts=local_ms(2026, 3, 2)),
            make_row(inp=7, ts=local_ms(2026, 3, 2)),
        ]
        rows[0]["session_id"] = "s1"
        rows[1]["session_id"] = "s2"
        rows[2]["session_id"] = "s3"
        meta = {"s1": {"title": "A", "project": "alpha"},
                "s2": {"title": "B", "project": "alpha"},
                "s3": {"title": "C", "project": "beta"}}
        stats = extract.aggregate(rows, session_meta=meta)
        projects = {p["project"]: p for p in stats["by_project"]}
        self.assertEqual(projects["alpha"]["tokens"], 150)
        self.assertEqual(projects["alpha"]["messages"], 2)
        self.assertEqual(projects["alpha"]["sessions"], 2)
        self.assertEqual(projects["beta"]["sessions"], 1)
        self.assertEqual(sum(p["tokens"] for p in stats["by_project"]), 157)

    def test_project_unknown_bucket_includes_sessionless_rows(self):
        rows = [make_row(inp=10, ts=local_ms(2026, 3, 2))]
        stats = extract.aggregate(rows, session_meta={})
        projects = {p["project"]: p for p in stats["by_project"]}
        self.assertEqual(projects["unknown"]["tokens"], 10)

    def test_heatmap_and_by_hour(self):
        # 2026-03-02 is a Monday → heatmap row 0; 14:00 local
        rows = [
            make_row(inp=100, ts=local_ms(2026, 3, 2, 14, 0)),
            make_row(inp=50, ts=local_ms(2026, 3, 3, 9, 0)),   # Tuesday row 1, 9h
        ]
        stats = extract.aggregate(rows, session_meta={})
        hm = stats["heatmap"]
        self.assertEqual(len(hm), 7)
        self.assertTrue(all(len(row) == 24 for row in hm))
        self.assertEqual(hm[0][14], 100)
        self.assertEqual(hm[1][9], 50)
        self.assertEqual(stats["by_hour"][14]["tokens"], 100)
        self.assertEqual(stats["by_hour"][9]["messages"], 1)
        self.assertEqual(sum(h["tokens"] for h in stats["by_hour"]), 150)

    def test_sessions_enriched(self):
        rows = [
            make_row(model_id="a", provider="p1", inp=100,
                     ts=local_ms(2026, 3, 2, 10, 0), cost=0.25),
            make_row(model_id="a", provider="p1", inp=50,
                     ts=local_ms(2026, 3, 2, 10, 15), cost=0.25),
            make_row(model_id="b", provider="p2", inp=5,
                     ts=local_ms(2026, 3, 2, 10, 30)),
        ]
        for i, r in enumerate(rows[:2]):
            r["session_id"] = "s1"
        meta = {"s1": {"title": "My Session", "project": "proj"}}
        stats = extract.aggregate(rows, session_meta=meta)
        top = stats["sessions_top"]
        self.assertEqual(len(top), 1)
        s = top[0]
        self.assertEqual(s["title"], "My Session")
        self.assertEqual(s["project"], "proj")
        self.assertEqual(s["model"], "a")
        self.assertEqual(s["provider"], "p1")
        self.assertEqual(s["tokens"], 150)
        self.assertEqual(s["messages"], 2)
        self.assertAlmostEqual(s["cost"], 0.5)
        self.assertEqual(s["input"], 150)
        self.assertEqual(s["duration_min"], 15.0)
        self.assertIsNotNone(s["created"])
        self.assertIsNotNone(s["last"])

    def test_aggregate_empty(self):
        stats = extract.aggregate([], session_meta={})
        self.assertEqual(stats["totals"]["all"], 0)
        self.assertEqual(stats["totals"]["messages"], 0)
        self.assertIsNone(stats["first_message"])
        self.assertIsNone(stats["last_message"])
        self.assertIsNone(stats["best_day"])
        self.assertEqual(stats["days_active"], 0)
        self.assertEqual(stats["by_model"], [])
        self.assertEqual(stats["timeline"], [])
        self.assertEqual(stats["by_project"], [])
        self.assertEqual(stats["daily_models"], [])
        self.assertEqual(stats["sessions_top"], [])
        self.assertEqual(stats["by_hour"], [
            {"hour": h, "tokens": 0, "messages": 0} for h in range(24)])
        self.assertEqual(stats["heatmap"], [[0] * 24 for _ in range(7)])


class IntegrationTests(unittest.TestCase):
    def test_extract_real_db(self):
        self.assertTrue(os.path.exists(DB_PATH), f"DB not found: {DB_PATH}")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "stats.json")
            stats = extract.run(DB_PATH, out)
            with open(out) as f:
                loaded = json.load(f)
        self.assertEqual(loaded["schema"], 2)
        t = stats["totals"]
        self.assertGreater(t["all"], 0)
        self.assertGreater(t["messages"], 0)
        self.assertGreaterEqual(stats["days_active"], 1)

        # internal consistency: model totals == grand total
        self.assertEqual(t["all"], sum(m["tokens"] for m in stats["by_model"]))
        # timeline components sum to totals
        for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
            self.assertEqual(t[key], sum(d[key] for d in stats["timeline"]),
                             f"timeline {key} mismatch")
        # daily_models sum to grand total
        self.assertEqual(t["all"], sum(r["tokens"] for r in stats["daily_models"]))
        # by_project covers everything
        self.assertEqual(t["all"], sum(p["tokens"] for p in stats["by_project"]))

        # cross-check against JSON1 over session_message (message-level truth)
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(json_extract(data,'$.tokens.input')"
            "+json_extract(data,'$.tokens.output')"
            "+json_extract(data,'$.tokens.reasoning')"
            "+json_extract(data,'$.tokens.cache.read')"
            "+json_extract(data,'$.tokens.cache.write')),0)"
            " FROM session_message WHERE type='assistant'"
            " AND json_valid(data) AND json_extract(data,'$.tokens.input') IS NOT NULL"
        ).fetchone()
        conn.close()
        sql_total, sql_count = row[1], row[0]
        self.assertAlmostEqual(t["all"], sql_total, delta=sql_total * 0.01,
                               msg=f"messages {t['all']} vs json_extract {sql_total}")
        self.assertAlmostEqual(t["messages"], sql_count, delta=max(2, sql_count * 0.01),
                               msg=f"parsed {t['messages']} vs json_extract {sql_count}")
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        srow = conn.execute(
            "SELECT SUM(tokens_input+tokens_output+tokens_reasoning"
            "+tokens_cache_read+tokens_cache_write) FROM session_v2"
        ).fetchone()
        conn.close()
        sql_session_total = srow[0] or 0
        self.assertAlmostEqual(t["all"], sql_session_total, delta=sql_session_total * 0.05,
                               msg=f"messages {t['all']} vs session_v2 {sql_session_total}")
        print(f"cross-check: messages_total={t['all']} json_extract={sql_total} "
              f"session_v2={sql_session_total} skipped={stats['skipped_rows']}")

    def test_run_applies_pricing(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "stats.json")
            stats = extract.run(DB_PATH, out)
        self.assertIn("est_cost", stats["totals"])
        self.assertIsInstance(stats["totals"]["est_cost"], float)
        self.assertEqual(stats["totals"]["all"],
                         stats["totals"]["priced_tokens"] + stats["totals"]["unpriced_tokens"])
        for m in stats["by_model"]:
            self.assertIn("priced", m)
            self.assertIn("est_cost", m)


if __name__ == "__main__":
    unittest.main()
