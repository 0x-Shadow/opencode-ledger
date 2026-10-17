"""Prometheus text exposition (format 0.0.4) for the ledger.

Bounded cardinality only: no per-day or per-session series. Timestamps map
to gauges; all cumulative counters come straight from stats.json.
"""
from __future__ import annotations

from datetime import datetime

TOKEN_TYPES = ("input", "output", "reasoning", "cache_read", "cache_write")


def _esc(value) -> str:
    return (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n"))


def _num(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    try:
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return text or "0"
    except (TypeError, ValueError):
        return "0"


def _labels(**kw) -> str:
    parts = [f'{k}="{_esc(v)}"' for k, v in kw.items() if v is not None]
    return "{" + ",".join(parts) + "}" if parts else ""


def render(stats: dict, budget_status: list | None = None,
           now: datetime | None = None) -> str:
    """Render stats as Prometheus exposition text."""
    now = now or datetime.now()
    stats = stats or {}
    totals = stats.get("totals") or {}
    lines: list[str] = []

    def family(name, mtype, help_text):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")

    def sample(name, labels, value):
        lines.append(f"{name}{labels} {_num(value)}")

    generated = stats.get("generated")
    try:
        ts = datetime.fromisoformat(generated).timestamp()
    except (TypeError, ValueError):
        ts = now.timestamp()

    family("opencode_ledger_info", "gauge",
           "Build info: version and stats schema.")
    sample("opencode_ledger_info",
           _labels(version=stats.get("version", "unknown"),
                   schema=stats.get("schema", 0)), 1)

    family("opencode_ledger_tokens", "counter",
           "Tokens consumed by type, all time.")
    for token_type in TOKEN_TYPES:
        sample("opencode_ledger_tokens", _labels(type=token_type),
               totals.get(token_type, 0))

    family("opencode_ledger_tokens_total", "counter",
           "Total tokens consumed, all time.")
    sample("opencode_ledger_tokens_total", "", totals.get("all", 0))

    family("opencode_ledger_messages_total", "counter",
           "Assistant messages recorded, all time.")
    sample("opencode_ledger_messages_total", "", totals.get("messages", 0))

    family("opencode_ledger_sessions_total", "counter",
           "Sessions recorded, all time.")
    sample("opencode_ledger_sessions_total", "", totals.get("sessions", 0))

    family("opencode_ledger_cost_usd_total", "counter",
           "Cost reported by the provider, USD, all time.")
    sample("opencode_ledger_cost_usd_total", "", totals.get("cost", 0))

    if totals.get("priced_tokens", 0) > 0:
        family("opencode_ledger_estimated_cost_usd_total", "counter",
               "Estimated retail cost of priced usage, USD, all time.")
        sample("opencode_ledger_estimated_cost_usd_total", "",
               totals.get("est_cost", 0))

    family("opencode_ledger_tokens_by_model", "counter",
           "Tokens consumed per provider/model/variant.")
    for entry in stats.get("by_model") or []:
        sample("opencode_ledger_tokens_by_model",
               _labels(provider=entry.get("provider", "unknown"),
                       model=entry.get("model", "unknown"),
                       variant=entry.get("variant")),
               entry.get("tokens", 0))

    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    timeline = stats.get("timeline") or []
    today_tokens = sum(d.get("tokens", 0) for d in timeline
                       if d.get("date") == day)
    month_tokens = sum(d.get("tokens", 0) for d in timeline
                       if str(d.get("date", "")).startswith(month))

    family("opencode_ledger_tokens_today", "gauge",
           "Tokens consumed today (local time).")
    sample("opencode_ledger_tokens_today", "", today_tokens)

    family("opencode_ledger_tokens_this_month", "gauge",
           "Tokens consumed this calendar month (local time).")
    sample("opencode_ledger_tokens_this_month", "", month_tokens)

    family("opencode_ledger_budget_used_ratio", "gauge",
           "Budget usage ratio (used / limit).")
    family("opencode_ledger_budget_limit", "gauge", "Budget limit.")
    for row in budget_status or []:
        labels = _labels(budget=row["id"], period=row["period"])
        sample("opencode_ledger_budget_used_ratio", labels, row["ratio"])
        sample("opencode_ledger_budget_limit", labels, row["limit"])

    family("opencode_ledger_days_active", "gauge",
           "Distinct days with recorded usage.")
    sample("opencode_ledger_days_active", "", stats.get("days_active", 0))

    family("opencode_ledger_last_extract_timestamp_seconds", "gauge",
           "Unix time of the stats extraction.")
    sample("opencode_ledger_last_extract_timestamp_seconds", "", ts)

    return "\n".join(lines) + "\n"
