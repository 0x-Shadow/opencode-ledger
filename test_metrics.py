"""Tests for opencode_ledger.metrics — Prometheus text exposition."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import __version__, metrics

NOW = datetime(2026, 6, 15, 12, 0)


def sample_stats():
    return {
        "schema": 2,
        "version": __version__,
        "generated": "2026-06-15T10:00:00+00:00",
        "totals": {
            "input": 100, "output": 50, "reasoning": 25,
            "cache_read": 1000, "cache_write": 5, "all": 1180,
            "cost": 0.25, "est_cost": 3.5, "priced_tokens": 1180,
            "unpriced_tokens": 0, "messages": 4, "sessions": 2,
        },
        "by_model": [
            {"provider": "ac me", "model": 'quo"te\\slash', "variant": None,
             "tokens": 1000, "messages": 3, "cost": 0.25,
             "est_cost": 3.0, "priced": True},
            {"provider": "p2", "model": "m2", "variant": "high",
             "tokens": 180, "messages": 1, "cost": 0.0,
             "est_cost": 0.5, "priced": True},
        ],
        "timeline": [
            {"date": "2026-06-01", "tokens": 80, "messages": 1, "cost": 0.0},
            {"date": "2026-06-15", "tokens": 1100, "messages": 3, "cost": 0.25},
        ],
        "days_active": 2,
    }


BUDGET_STATUS = [
    {"id": "daily_tokens", "period": "day", "unit": "tokens",
     "limit": 2000, "used": 1100, "ratio": 0.55, "period_start": "2026-06-15"},
]


def parse(text):
    """Parse exposition text into {(metric, labels): value}."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name_part, _, value = line.rpartition(" ")
        name, _, labels = name_part.partition("{")
        labels = labels.rstrip("}") if labels else ""
        out[(name, labels)] = float(value)
    return out


class RenderTests(unittest.TestCase):
    def test_help_and_type_lines(self):
        text = metrics.render(sample_stats(), now=NOW)
        self.assertIn("# TYPE opencode_ledger_tokens_total counter", text)
        self.assertIn("# TYPE opencode_ledger_budget_used_ratio gauge", text)

    def test_core_series(self):
        s = parse(metrics.render(sample_stats(), now=NOW))
        self.assertEqual(s[("opencode_ledger_tokens", 'type="input"')], 100)
        self.assertEqual(s[("opencode_ledger_tokens", 'type="cache_read"')], 1000)
        self.assertEqual(s[("opencode_ledger_tokens_total", "")], 1180)
        self.assertEqual(s[("opencode_ledger_messages_total", "")], 4)
        self.assertEqual(s[("opencode_ledger_sessions_total", "")], 2)
        self.assertEqual(s[("opencode_ledger_cost_usd_total", "")], 0.25)
        self.assertEqual(s[("opencode_ledger_estimated_cost_usd_total", "")], 3.5)
        self.assertEqual(s[("opencode_ledger_days_active", "")], 2)
        self.assertEqual(
            s[("opencode_ledger_last_extract_timestamp_seconds", "")],
            datetime.fromisoformat("2026-06-15T10:00:00+00:00").timestamp())

    def test_today_and_month(self):
        s = parse(metrics.render(sample_stats(), now=NOW))
        self.assertEqual(s[("opencode_ledger_tokens_today", "")], 1100)
        self.assertEqual(s[("opencode_ledger_tokens_this_month", "")], 1180)

    def test_by_model_with_escaping(self):
        text = metrics.render(sample_stats(), now=NOW)
        self.assertIn('model="quo\\"te\\\\slash"', text)
        self.assertIn('provider="ac me"', text)
        s = parse(text)
        self.assertEqual(
            s[("opencode_ledger_tokens_by_model",
               'provider="ac me",model="quo\\"te\\\\slash"')], 1000)

    def test_variant_label_when_present(self):
        s = parse(metrics.render(sample_stats(), now=NOW))
        self.assertEqual(len([k for k in s if k[0] == "opencode_ledger_tokens_by_model"]), 2)

    def test_budget_series(self):
        s = parse(metrics.render(sample_stats(), budget_status=BUDGET_STATUS, now=NOW))
        self.assertEqual(
            s[("opencode_ledger_budget_used_ratio",
               'budget="daily_tokens",period="day"')], 0.55)
        self.assertEqual(
            s[("opencode_ledger_budget_limit",
               'budget="daily_tokens",period="day"')], 2000)

    def test_no_estimated_cost_when_unpriced(self):
        stats = sample_stats()
        stats["totals"]["priced_tokens"] = 0
        s = parse(metrics.render(stats, now=NOW))
        self.assertNotIn(("opencode_ledger_estimated_cost_usd_total", ""), s)

    def test_info_gauge(self):
        s = parse(metrics.render(sample_stats(), now=NOW))
        self.assertEqual(s[("opencode_ledger_info", f'version="{__version__}",schema="2"')], 1)

    def test_empty_stats_is_safe(self):
        text = metrics.render({}, now=NOW)
        s = parse(text)
        self.assertEqual(s[("opencode_ledger_tokens_total", "")], 0)
        self.assertIn("opencode_ledger_last_extract_timestamp_seconds", text)

    def test_float_formatting_no_exponent(self):
        stats = sample_stats()
        stats["totals"]["ratio_dummy"] = 1e-7
        text = metrics.render(stats, now=NOW)
        self.assertNotIn("e-", text.replace("expected", ""))

    def test_ends_with_newline(self):
        self.assertTrue(metrics.render(sample_stats(), now=NOW).endswith("\n"))


if __name__ == "__main__":
    unittest.main()
