"""Retail price estimation for token usage.

Uses a shipped price table (``pricing.json``, approximate public list prices
per 1M tokens) merged with user overrides. Prices are estimates only; they
exist to answer "what would this usage have cost at list prices?".

Price dict keys: input, output, cache_read, cache_write, reasoning.
Missing reasoning falls back to output; missing cache prices fall back to
input. Models without a price get ``est_cost: None`` — never a guess.
"""
from __future__ import annotations

import json
import os

DEFAULT_PRICING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "pricing.json")

_PRICE_KEYS = ("input", "output", "cache_read", "cache_write", "reasoning")
_COMPONENTS = ("input", "output", "reasoning", "cache_read", "cache_write")


def _sanitize_price(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    price = {}
    for key in _PRICE_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value < 0:
            return None
        price[key] = float(value)
    if "input" not in price or "output" not in price:
        return None
    return price


class PricingTable:
    """Model price lookup: ``provider/model`` exact, then bare ``model``."""

    def __init__(self, overrides: dict | None = None, path: str | None = None):
        self.path = path or DEFAULT_PRICING_PATH
        self.models: dict[str, dict] = {}
        self.note = ""
        try:
            with open(self.path) as f:
                data = json.load(f)
            raw_models = data.get("models", {}) if isinstance(data, dict) else {}
            self.note = data.get("note", "") if isinstance(data, dict) else ""
        except (OSError, json.JSONDecodeError):
            raw_models = {}
        for key, raw in raw_models.items():
            price = _sanitize_price(raw)
            if price is not None:
                self.models[key] = price
        for key, raw in (overrides or {}).items():
            price = _sanitize_price(raw)
            if price is not None:
                self.models[key] = price

    def price_for(self, provider, model, variant=None) -> dict | None:
        if not model:
            return None
        if provider and f"{provider}/{model}" in self.models:
            return self.models[f"{provider}/{model}"]
        return self.models.get(model)

    def estimate(self, tokens: dict, price: dict) -> float:
        """USD cost for a component-token dict at the given price."""
        inp = tokens.get("input", 0) or 0
        out = tokens.get("output", 0) or 0
        reasoning = tokens.get("reasoning", 0) or 0
        cache_read = tokens.get("cache_read", 0) or 0
        cache_write = tokens.get("cache_write", 0) or 0
        p_in = price["input"]
        p_out = price["output"]
        p_reason = price.get("reasoning", p_out)
        p_cr = price.get("cache_read", p_in)
        p_cw = price.get("cache_write", p_in)
        return (inp * p_in + out * p_out + reasoning * p_reason
                + cache_read * p_cr + cache_write * p_cw) / 1_000_000


def _entry_cost(table: PricingTable, entry: dict):
    """Cost for one stats entry, or None when unpriced/components missing."""
    if not all(isinstance(entry.get(k), (int, float)) for k in _COMPONENTS):
        return None
    price = table.price_for(entry.get("provider"), entry.get("model"),
                            entry.get("variant"))
    if price is None:
        return None
    return round(table.estimate(entry, price), 6)


def apply(stats: dict, table: PricingTable) -> dict:
    """Annotate stats with est_cost / priced flags and totals. Mutates + returns."""
    totals = stats.setdefault("totals", {})
    priced_tokens = 0
    unpriced_tokens = 0
    total_est = 0.0

    for entry in stats.get("by_model", []):
        est = _entry_cost(table, entry)
        entry["est_cost"] = est
        entry["priced"] = est is not None
        tokens = entry.get("tokens", 0) or 0
        if est is None:
            unpriced_tokens += tokens
        else:
            priced_tokens += tokens
            total_est += est

    day_est: dict[str, float] = {}
    for entry in stats.get("daily_models", []):
        est = _entry_cost(table, entry)
        entry["est_cost"] = est
        entry["priced"] = est is not None
        if est is not None:
            day_est[entry.get("date")] = day_est.get(entry.get("date"), 0.0) + est

    for entry in stats.get("timeline", []):
        entry["est_cost"] = round(day_est.get(entry.get("date"), 0.0), 6)

    for entry in stats.get("sessions_top", []):
        est = _entry_cost(table, entry)
        entry["est_cost"] = est
        entry["priced"] = est is not None

    totals["est_cost"] = round(total_est, 6)
    totals["priced_tokens"] = priced_tokens
    totals["unpriced_tokens"] = unpriced_tokens
    return stats
