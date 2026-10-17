"""Tests for opencode_ledger.budget — budget evaluation and one-shot alerts."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import budget

NOW = datetime(2026, 6, 15, 12, 0)   # Monday, mid-month


def stats_with(today_tokens=800, today_cost=8.0, month_extra_tokens=100,
               month_extra_cost=1.0):
    return {
        "schema": 2,
        "totals": {"all": today_tokens + month_extra_tokens},
        "timeline": [
            {"date": "2026-06-01", "tokens": month_extra_tokens,
             "cost": month_extra_cost, "messages": 1},
            {"date": "2026-06-15", "tokens": today_tokens,
             "cost": today_cost, "messages": 2},
        ],
    }


BUDGETS = {"daily_tokens": None, "monthly_tokens": None,
           "daily_cost_usd": None, "monthly_cost_usd": None}
ALERTS = {"thresholds": [0.8, 1.0], "webhook_url": None,
          "webhook_format": "generic"}


class EvaluateTests(unittest.TestCase):
    def test_no_budgets_means_empty(self):
        self.assertEqual(budget.evaluate(stats_with(), BUDGETS, now=NOW), [])

    def test_daily_tokens(self):
        b = dict(BUDGETS, daily_tokens=1000)
        status = budget.evaluate(stats_with(today_tokens=800), b, now=NOW)
        self.assertEqual(len(status), 1)
        s = status[0]
        self.assertEqual(s["id"], "daily_tokens")
        self.assertEqual(s["period"], "day")
        self.assertEqual(s["unit"], "tokens")
        self.assertEqual(s["used"], 800)
        self.assertEqual(s["limit"], 1000)
        self.assertAlmostEqual(s["ratio"], 0.8)
        self.assertEqual(s["period_start"], "2026-06-15")

    def test_daily_ignores_other_days(self):
        b = dict(BUDGETS, daily_tokens=1000)
        # today has no entry → used 0
        status = budget.evaluate(stats_with(), b,
                                 now=datetime(2026, 6, 20, 9, 0))
        self.assertEqual(status[0]["used"], 0)
        self.assertEqual(status[0]["ratio"], 0.0)

    def test_monthly_sums_current_month(self):
        b = dict(BUDGETS, monthly_tokens=10_000)
        status = budget.evaluate(stats_with(today_tokens=800,
                                            month_extra_tokens=200), b, now=NOW)
        self.assertEqual(status[0]["used"], 1000)
        self.assertEqual(status[0]["period"], "month")
        self.assertEqual(status[0]["period_start"], "2026-06")

    def test_month_rollover_resets(self):
        b = dict(BUDGETS, monthly_tokens=10_000)
        status = budget.evaluate(stats_with(), b,
                                 now=datetime(2026, 7, 1, 9, 0))
        self.assertEqual(status[0]["used"], 0)

    def test_daily_cost_uses_reported_cost(self):
        b = dict(BUDGETS, daily_cost_usd=10.0)
        status = budget.evaluate(stats_with(today_cost=8.0), b, now=NOW)
        self.assertEqual(status[0]["unit"], "usd")
        self.assertEqual(status[0]["used"], 8.0)


class DueAlertsTests(unittest.TestCase):
    def status(self, ratio_used, limit=1000):
        return [{"id": "daily_tokens", "period": "day", "unit": "tokens",
                 "limit": limit, "used": ratio_used,
                 "ratio": ratio_used / limit, "period_start": "2026-06-15"}]

    def test_below_threshold_is_silent(self):
        state = {"alerts_sent": {}}
        alerts = budget.due_alerts(self.status(790), ALERTS, state)
        self.assertEqual(alerts, [])

    def test_exactly_at_threshold_fires(self):
        state = {"alerts_sent": {}}
        alerts = budget.due_alerts(self.status(800), ALERTS, state)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["threshold"], 0.8)
        self.assertEqual(alerts[0]["key"], "daily_tokens:2026-06-15:0.8")

    def test_dedupe_same_period(self):
        state = {"alerts_sent": {"daily_tokens:2026-06-15:0.8": "2026-06-15T10:00:00"}}
        alerts = budget.due_alerts(self.status(800), ALERTS, state)
        self.assertEqual(alerts, [])

    def test_new_period_fires_again(self):
        state = {"alerts_sent": {"daily_tokens:2026-06-14:0.8": "x"}}
        alerts = budget.due_alerts(self.status(800), ALERTS, state)
        self.assertEqual(len(alerts), 1)

    def test_multiple_thresholds(self):
        state = {"alerts_sent": {}}
        alerts = budget.due_alerts(self.status(1000), ALERTS, state)
        self.assertEqual([a["threshold"] for a in alerts], [0.8, 1.0])

    def test_only_unsent_threshold_when_partially_sent(self):
        state = {"alerts_sent": {"daily_tokens:2026-06-15:0.8": "x"}}
        alerts = budget.due_alerts(self.status(1000), ALERTS, state)
        self.assertEqual([a["threshold"] for a in alerts], [1.0])

    def test_custom_thresholds(self):
        alerts_cfg = dict(ALERTS, thresholds=[0.5])
        alerts = budget.due_alerts(self.status(600), alerts_cfg,
                                   {"alerts_sent": {}})
        self.assertEqual([a["threshold"] for a in alerts], [0.5])


class PayloadTests(unittest.TestCase):
    alert = {"key": "daily_tokens:2026-06-15:0.8", "id": "daily_tokens",
             "period": "day", "unit": "tokens", "limit": 1000, "used": 800,
             "ratio": 0.8, "threshold": 0.8, "period_start": "2026-06-15"}

    def test_generic_payload(self):
        body, headers = budget.format_payload(self.alert, "generic")
        data = json.loads(body)
        self.assertEqual(data["budget"], "daily_tokens")
        self.assertEqual(data["limit"], 1000)
        self.assertIn("text", data)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_discord_and_slack_payloads(self):
        d, _ = budget.format_payload(self.alert, "discord")
        s, _ = budget.format_payload(self.alert, "slack")
        self.assertIn("content", json.loads(d))
        self.assertIn("text", json.loads(s))

    def test_ntfy_payload(self):
        body, headers = budget.format_payload(self.alert, "ntfy")
        self.assertIn("Title", headers)
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn(b"daily_tokens", body)

    def test_text_mentions_used_and_limit(self):
        text = budget.render_text(self.alert)
        self.assertIn("800", text)
        self.assertIn("1,000", text)
        self.assertIn("80%", text)


class DispatchTests(unittest.TestCase):
    alert = PayloadTests.alert

    class FakeResponse:
        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_no_webhook_configured(self):
        self.assertFalse(budget.dispatch(self.alert, ALERTS))

    def test_success_via_fake_opener(self):
        seen = {}

        def opener(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["body"] = req.data
            return self.FakeResponse(200)

        cfg = dict(ALERTS, webhook_url="https://hook.example/x")
        self.assertTrue(budget.dispatch(self.alert, cfg, opener=opener))
        self.assertEqual(seen["url"], "https://hook.example/x")
        self.assertEqual(seen["method"], "POST")
        self.assertIn(b"daily_tokens", seen["body"])

    def test_http_error_returns_false(self):
        def opener(req, timeout=None):
            return self.FakeResponse(500)

        cfg = dict(ALERTS, webhook_url="https://hook.example/x")
        self.assertFalse(budget.dispatch(self.alert, cfg, opener=opener))

    def test_transport_exception_returns_false(self):
        def opener(req, timeout=None):
            raise OSError("boom")

        cfg = dict(ALERTS, webhook_url="https://hook.example/x")
        self.assertFalse(budget.dispatch(self.alert, cfg, opener=opener))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = os.path.join(self.tmp.name, "state.json")

    def cfg(self, **kw):
        class C:
            budgets = dict(BUDGETS, daily_tokens=1000)
            alerts = dict(ALERTS)
            state_file = self.state_path
        for k, v in kw.items():
            setattr(C, k, v)
        return C

    def test_check_over_budget_exit_1_and_marks_state(self):
        result = budget.check(self.cfg(), stats=stats_with(today_tokens=1100),
                              now=NOW, send=True)
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(len(result["status"]), 1)
        # no webhook configured → nothing sent, nothing marked
        state = budget.load_state(self.state_path)
        self.assertEqual(state["alerts_sent"], {})

    def test_check_sends_and_marks_on_success(self):
        sent = []

        def opener(req, timeout=None):
            sent.append(req.full_url)
            return DispatchTests.FakeResponse(200)

        cfg = self.cfg()
        cfg.alerts = dict(ALERTS, webhook_url="https://hook.example/x")
        result = budget.check(cfg, stats=stats_with(today_tokens=1100),
                              now=NOW, send=True, opener=opener)
        self.assertEqual(len(sent), 2)  # 0.8 and 1.0 thresholds
        self.assertEqual(len(result["sent"]), 2)
        state = budget.load_state(self.state_path)
        self.assertIn("daily_tokens:2026-06-15:0.8", state["alerts_sent"])

    def test_check_does_not_mark_failed_sends(self):
        def opener(req, timeout=None):
            raise OSError("down")

        cfg = self.cfg()
        cfg.alerts = dict(ALERTS, webhook_url="https://hook.example/x")
        budget.check(cfg, stats=stats_with(today_tokens=1100), now=NOW,
                     send=True, opener=opener)
        state = budget.load_state(self.state_path)
        self.assertEqual(state["alerts_sent"], {})

    def test_check_prunes_old_periods(self):
        state = {"alerts_sent": {
            "daily_tokens:2026-06-14:0.8": "x",
            "daily_tokens:2026-06-15:0.8": "x",
            "monthly_tokens:2026-05:0.8": "x",
        }}
        budget.save_state(self.state_path, state)
        cfg = self.cfg()
        cfg.budgets = dict(BUDGETS, daily_tokens=1000, monthly_tokens=10_000)
        budget.check(cfg, stats=stats_with(today_tokens=100), now=NOW,
                     send=False)
        state = budget.load_state(self.state_path)
        self.assertNotIn("daily_tokens:2026-06-14:0.8", state["alerts_sent"])
        self.assertNotIn("monthly_tokens:2026-05:0.8", state["alerts_sent"])
        self.assertIn("daily_tokens:2026-06-15:0.8", state["alerts_sent"])

    def test_check_under_budget_exit_0(self):
        result = budget.check(self.cfg(), stats=stats_with(today_tokens=100),
                              now=NOW, send=False)
        self.assertEqual(result["exit_code"], 0)

    def test_load_state_missing_or_malformed(self):
        self.assertEqual(budget.load_state(self.state_path), {"alerts_sent": {}})
        with open(self.state_path, "w") as f:
            f.write("{not json")
        self.assertEqual(budget.load_state(self.state_path), {"alerts_sent": {}})

    def test_save_state_creates_dirs(self):
        nested = os.path.join(self.tmp.name, "a", "b", "state.json")
        budget.save_state(nested, {"alerts_sent": {"k": "v"}})
        self.assertEqual(budget.load_state(nested)["alerts_sent"], {"k": "v"})


if __name__ == "__main__":
    unittest.main()
