"""Synthetic demo data — try the dashboard before connecting a real database.

``generate_stats`` builds plausible sessions/messages and feeds them through
the real ``extract.aggregate``, so demo output is guaranteed to have exactly
the shipped schema (and pricing annotations). Deterministic for a given seed.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, time, timedelta

from . import __version__
from .extract import aggregate
from .pricing import PricingTable
from .pricing import apply as apply_pricing

# provider, model, variant, token profile per message
MODELS = [
    ("opencode", "mimo-v2.6-flash-free", None,
     {"input": (2_000, 9_000), "output": (200, 1_800),
      "reasoning": (0, 600), "cache_read": (20_000, 90_000)}),
    ("opencode", "step-5-preview-free", "high",
     {"input": (3_000, 12_000), "output": (400, 2_600),
      "reasoning": (200, 2_400), "cache_read": (30_000, 150_000)}),
    ("anthropic", "claude-sonnet-4-5", None,
     {"input": (4_000, 15_000), "output": (300, 2_000),
      "reasoning": (0, 400), "cache_read": (10_000, 60_000),
      "cache_write": True}),
    ("openai", "gpt-5-mini", None,
     {"input": (1_500, 8_000), "output": (150, 1_200),
      "reasoning": (0, 900), "cache_read": (5_000, 40_000)}),
]

PROJECTS = ["opencode-ledger", "acme-api", "travel-site", "data-pipeline"]
AGENTS = ["build", "build", "build", "build", "plan", "explore", "general"]
WORDS = ["refactor", "auth", "tests", "docker", "dashboard", "parser", "ci",
         "heatmap", "budgets", "exports", "docs", "release", "sqlite", "api"]


def generate_stats(days: int = 60, seed: int = 7, now: datetime | None = None) -> dict:
    """Build a full schema-2 stats dict with deterministic synthetic usage."""
    rng = random.Random(seed)
    now = now or datetime.now()
    rows: list[dict] = []
    session_meta: dict[str, dict] = {}
    sid_n = 0

    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        weekend = day.weekday() >= 5
        n_sessions = rng.choice([1, 1, 2, 2, 3] if not weekend
                                else [1, 1, 1, 2])
        for _ in range(n_sessions):
            sid_n += 1
            sid = f"demo-s{sid_n}"
            provider, model, variant, profile = MODELS[sid_n % len(MODELS)]
            project = rng.choice(PROJECTS)
            title = f"{project}: {' '.join(rng.sample(WORDS, 3))}"
            session_meta[sid] = {"title": title, "project": project,
                                 "model": model}
            start = datetime.combine(day, time(rng.randint(8, 20),
                                               rng.randint(0, 59)))
            cache_read = 0
            model_ref = {"id": model, "providerID": provider}
            if variant:
                model_ref["variant"] = variant
            for i in range(rng.randint(2, 14)):
                ts = start + timedelta(minutes=i * rng.randint(1, 6))
                cache_read = min(int(cache_read * rng.uniform(0.8, 1.5))
                                 + rng.randint(0, profile["input"][0]), 250_000)
                rows.append({
                    "model": model_ref,
                    "tokens": {
                        "input": rng.randint(*profile["input"]),
                        "output": rng.randint(*profile["output"]),
                        "reasoning": rng.randint(*profile.get("reasoning", (0, 0))),
                        "cache": {
                            "read": cache_read,
                            "write": rng.randint(*profile["input"])
                            if profile.get("cache_write") and rng.random() < 0.6
                            else 0,
                        },
                    },
                    "cost": 0.0,
                    "time": {"created": int(ts.timestamp() * 1000)},
                    "agent": rng.choice(AGENTS),
                    "session_id": sid,
                })

    stats = aggregate(rows, session_meta)
    stats["generated"] = now.replace(microsecond=0).isoformat()
    stats["version"] = __version__
    stats["db_path"] = "demo"
    apply_pricing(stats, PricingTable())
    return stats


def write_demo(out_path: str, days: int = 60, seed: int = 7,
               now: datetime | None = None) -> dict:
    """Generate demo stats and write them to out_path. Returns the stats."""
    stats = generate_stats(days=days, seed=seed, now=now)
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(stats, f, indent=1)
    os.replace(tmp, out_path)
    return stats
