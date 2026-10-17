"""Tests for opencode_ledger.pricing — retail cost estimation."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import pricing


def row(inp=0, out=0, reasoning=0, cache_read=0, cache_write=0):
    return {"input": inp, "output": out, "reasoning": reasoning,
            "cache_read": cache_read, "cache_write": cache_write}


def model_entry(provider, model, tokens=0, **components):
    base = {"provider": provider, "model": model, "variant": None,
            "tokens": tokens, "messages": 1, "cost": 0.0}
    base.update(row(**components))
    return base


class TableTests(unittest.TestCase):
    def test_shipped_table_loads(self):
        t = pricing.PricingTable()
        self.assertGreaterEqual(len(t.models), 10)
        for key, price in t.models.items():
            self.assertIn("input", price, key)
            self.assertIn("output", price, key)
            self.assertGreaterEqual(price["input"], 0, key)

    def test_exact_lookup(self):
        t = pricing.PricingTable(overrides={
            "prov/model-x": {"input": 3.0, "output": 15.0}})
        price = t.price_for("prov", "model-x")
        self.assertEqual(price["input"], 3.0)
        self.assertEqual(price["output"], 15.0)

    def test_overrides_win_over_shipped(self):
        shipped = pricing.PricingTable()
        some_key = next(iter(shipped.models))
        provider, model = some_key.split("/", 1)
        t = pricing.PricingTable(overrides={some_key: {"input": 99.0, "output": 99.0}})
        self.assertEqual(t.price_for(provider, model)["input"], 99.0)

    def test_custom_path_replaces_shipped(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "custom.json")
            with open(path, "w") as f:
                json.dump({"models": {"p/m": {"input": 1.0, "output": 2.0}}}, f)
            t = pricing.PricingTable(path=path)
            self.assertEqual(list(t.models), ["p/m"])
            self.assertEqual(t.price_for("p", "m")["input"], 1.0)

    def test_unknown_model_is_none(self):
        t = pricing.PricingTable()
        self.assertIsNone(t.price_for("nope", "nope-9000"))
        self.assertIsNone(t.price_for("nope", "nope-9000", variant="high"))

    def test_variant_ignored_for_lookup(self):
        t = pricing.PricingTable(overrides={"p/m": {"input": 1, "output": 1}})
        self.assertIsNotNone(t.price_for("p", "m", variant="xhigh"))

    def test_bare_model_fallback(self):
        t = pricing.PricingTable(overrides={"m": {"input": 5, "output": 5}})
        self.assertEqual(t.price_for("any-provider", "m")["input"], 5)

    def test_malformed_price_entries_dropped(self):
        t = pricing.PricingTable(overrides={
            "good/m": {"input": 1, "output": 2},
            "bad/m": {"input": "oops", "output": 2},
            "worse/m": [1, 2],
        })
        self.assertIsNotNone(t.price_for("good", "m"))
        self.assertIsNone(t.price_for("bad", "m"))
        self.assertIsNone(t.price_for("worse", "m"))


class EstimateTests(unittest.TestCase):
    def test_math_per_million(self):
        t = pricing.PricingTable()
        price = {"input": 3.0, "output": 15.0, "cache_read": 0.3,
                 "cache_write": 3.75, "reasoning": 15.0}
        cost = t.estimate(row(inp=1_000_000, out=1_000_000,
                              cache_read=1_000_000, cache_write=1_000_000,
                              reasoning=1_000_000), price)
        self.assertAlmostEqual(cost, 3.0 + 15.0 + 0.3 + 3.75 + 15.0, places=9)

    def test_fallbacks_output_for_reasoning_input_for_cache(self):
        t = pricing.PricingTable()
        price = {"input": 2.0, "output": 10.0}
        cost = t.estimate(row(inp=1_000_000, out=1_000_000,
                              reasoning=1_000_000, cache_read=1_000_000,
                              cache_write=1_000_000), price)
        self.assertAlmostEqual(cost, 2.0 + 10.0 + 10.0 + 2.0 + 2.0, places=9)

    def test_zero_tokens_zero_cost(self):
        t = pricing.PricingTable()
        self.assertEqual(t.estimate(row(), {"input": 5, "output": 5}), 0.0)


class ApplyTests(unittest.TestCase):
    def _stats(self):
        return {
            "totals": {"all": 5_000_000, "cost": 0.0, "messages": 3},
            "by_model": [
                model_entry("acme", "priced", tokens=4_000_000,
                            inp=1_000_000, out=1_000_000,
                            reasoning=1_000_000, cache_read=500_000,
                            cache_write=500_000),
                model_entry("acme", "unpriced", tokens=1_000_000, out=1_000_000),
            ],
            "timeline": [
                {"date": "2026-01-01", "tokens": 4_000_000, "messages": 2,
                 **row(inp=1_000_000, out=1_000_000, reasoning=1_000_000,
                       cache_read=500_000, cache_write=500_000)},
            ],
            "daily_models": [
                {"date": "2026-01-01", "provider": "acme", "model": "priced",
                 "variant": None, "tokens": 4_000_000, "messages": 2,
                 "cost": 0.0,
                 **row(inp=1_000_000, out=1_000_000, reasoning=1_000_000,
                       cache_read=500_000, cache_write=500_000)},
            ],
            "sessions_top": [
                {"title": "t", "project": "p", "provider": "acme",
                 "model": "priced",
                 "tokens": 4_000_000, "messages": 2, "cost": 0.0,
                 **row(inp=1_000_000, out=1_000_000, reasoning=1_000_000,
                       cache_read=500_000, cache_write=500_000)},
            ],
        }

    def test_apply_annotates_and_totals(self):
        stats = self._stats()
        t = pricing.PricingTable(overrides={
            "acme/priced": {"input": 3.0, "output": 15.0, "cache_read": 0.3,
                            "cache_write": 3.75, "reasoning": 15.0}})
        pricing.apply(stats, t)

        priced, unpriced = stats["by_model"]
        self.assertTrue(priced["priced"])
        self.assertAlmostEqual(priced["est_cost"],
                               3.0 + 15.0 + 15.0 + 0.15 + 1.875, places=6)
        self.assertFalse(unpriced["priced"])
        self.assertIsNone(unpriced["est_cost"])

        self.assertAlmostEqual(stats["totals"]["est_cost"],
                               3.0 + 15.0 + 15.0 + 0.15 + 1.875, places=6)
        self.assertEqual(stats["totals"]["priced_tokens"], 4_000_000)
        self.assertEqual(stats["totals"]["unpriced_tokens"], 1_000_000)

        self.assertAlmostEqual(stats["timeline"][0]["est_cost"],
                               3.0 + 15.0 + 15.0 + 0.15 + 1.875, places=6)
        self.assertAlmostEqual(stats["daily_models"][0]["est_cost"],
                               3.0 + 15.0 + 15.0 + 0.15 + 1.875, places=6)
        self.assertAlmostEqual(stats["sessions_top"][0]["est_cost"],
                               3.0 + 15.0 + 15.0 + 0.15 + 1.875, places=6)

    def test_apply_without_components_is_none_not_crash(self):
        stats = {"totals": {"all": 10, "cost": 0.0},
                 "by_model": [{"provider": "p", "model": "m", "variant": None,
                               "tokens": 10, "messages": 1, "cost": 0.0}],
                 "timeline": [], "daily_models": [], "sessions_top": []}
        pricing.apply(stats, pricing.PricingTable(
            overrides={"p/m": {"input": 1, "output": 1}}))
        self.assertIsNone(stats["by_model"][0]["est_cost"])

    def test_apply_on_empty_stats_is_noop(self):
        stats = {"totals": {"all": 0, "cost": 0.0}, "by_model": [],
                 "timeline": [], "daily_models": [], "sessions_top": []}
        pricing.apply(stats, pricing.PricingTable())
        self.assertEqual(stats["totals"]["priced_tokens"], 0)
        self.assertEqual(stats["totals"]["unpriced_tokens"], 0)
        self.assertEqual(stats["totals"]["est_cost"], 0.0)


if __name__ == "__main__":
    unittest.main()
