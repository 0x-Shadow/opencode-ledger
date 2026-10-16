"""Tests for extract.py — the OpenCode lifetime token extractor."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")


def make_row(model_id="m", provider="p", variant=None, inp=0, out=0,
             reasoning=0, cache_read=0, cache_write=0, ts=1791114176931,
             agent="build"):
    return {
        "model": {"id": model_id, "providerID": provider,
                  **({"variant": variant} if variant else {})},
        "tokens": {"input": inp, "output": out, "reasoning": reasoning,
                   "cache": {"read": cache_read, "write": cache_write}},
        "cost": 0.0,
        "time": {"created": ts},
        "agent": agent,
    }


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


class AggregateTests(unittest.TestCase):
    def test_aggregate_totals(self):
        rows = [
            make_row(model_id="a", provider="prov1", inp=100, out=50,
                     reasoning=10, cache_read=5, cache_write=2,
                     ts=1791114176931),  # 2026-10-03-ish
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
        # by_model sorted desc by tokens; b has 1001, a has 200+167=367
        self.assertEqual(len(stats["by_model"]), 2)
        self.assertEqual(stats["by_model"][0]["model"], "b")
        self.assertEqual(stats["by_model"][0]["variant"], "high")
        self.assertEqual(stats["by_model"][0]["tokens"], 1001)
        self.assertEqual(stats["by_model"][1]["tokens"], 367)
        # by_provider aggregated
        provs = {p["provider"]: p for p in stats["by_provider"]}
        self.assertEqual(provs["prov1"]["messages"], 2)
        self.assertEqual(provs["prov1"]["tokens"], 367)
        self.assertEqual(provs["prov2"]["tokens"], 1001)
        # timeline: two distinct days (timestamps 24h+ apart)
        self.assertEqual(len(stats["timeline"]), 2)
        self.assertEqual(sum(d["messages"] for d in stats["timeline"]), 3)
        self.assertEqual(len(stats["monthly"]), 1)
        self.assertEqual(stats["monthly"][0]["messages"], 3)
        # best_day = the day with b's 1001 tokens
        self.assertEqual(stats["best_day"]["tokens"], 1001)
        self.assertEqual(stats["days_active"], 2)
        self.assertEqual(stats["skipped_rows"], 0)
        self.assertIsNotNone(stats["first_message"])
        self.assertIsNotNone(stats["last_message"])

    def test_aggregate_session_meta_and_top_sessions(self):
        rows = [
            make_row(model_id="a", provider="p", inp=100, ts=1791114176931),
            make_row(model_id="a", provider="p", inp=50, ts=1791114176931),
        ]
        # rows carry session ids via extract's row convention
        rows[0]["session_id"] = "s1"
        rows[1]["session_id"] = "s1"
        meta = {"s1": {"title": "My Session", "project": "proj"}}
        stats = extract.aggregate(rows, session_meta=meta)
        top = stats["sessions_top"]
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["title"], "My Session")
        self.assertEqual(top[0]["project"], "proj")
        self.assertEqual(top[0]["tokens"], 150)
        self.assertEqual(top[0]["messages"], 2)

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


class IntegrationTests(unittest.TestCase):
    def test_extract_real_db(self):
        self.assertTrue(os.path.exists(DB_PATH), f"DB not found: {DB_PATH}")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "stats.json")
            extract.main(DB_PATH, out)
            with open(out) as f:
                stats = json.load(f)
        t = stats["totals"]
        self.assertGreater(t["all"], 0)
        self.assertGreater(t["messages"], 0)
        self.assertGreaterEqual(stats["skipped_rows"], 0)
        self.assertGreaterEqual(stats["days_active"], 1)
        # internal consistency: totals.all == sum of by_model tokens
        self.assertEqual(t["all"], sum(m["tokens"] for m in stats["by_model"]))
        # cross-check against SQL over session_v2 (session-level totals)
        conn = sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True)
        row = conn.execute(
            "SELECT SUM(tokens_input+tokens_output+tokens_reasoning"
            "+tokens_cache_read+tokens_cache_write) FROM session_v2"
        ).fetchone()
        conn.close()
        sql_total = row[0] or 0
        # message-level total should be >= session-level rollups and within 5%
        # (session totals may lag or miss subagent messages)
        self.assertGreaterEqual(t["all"], sql_total * 0.95,
                                f"messages {t['all']} vs session_v2 {sql_total}")
        print(f"cross-check: messages_total={t['all']} "
              f"session_v2_total={sql_total} skipped={stats['skipped_rows']}")


if __name__ == "__main__":
    unittest.main()
