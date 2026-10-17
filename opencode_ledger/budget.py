"""Budgets and one-shot threshold alerts.

Budgets are evaluated against ``timeline`` (local-time days):

- ``daily_tokens`` / ``monthly_tokens`` count tokens;
- ``daily_cost_usd`` / ``monthly_cost_usd`` count the *reported* cost from
  OpenCode — free-tier models report 0, so token budgets are the useful
  control there.

Alerts fire once per (budget, period, threshold), tracked in a small state
file, so a cron'd ``opencode-ledger check`` never spams a webhook. Delivery
is best-effort: failures are reported and retried on the next check.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime

BUDGET_IDS = {
    "daily_tokens": ("day", "tokens"),
    "monthly_tokens": ("month", "tokens"),
    "daily_cost_usd": ("day", "usd"),
    "monthly_cost_usd": ("month", "usd"),
}

_EPSILON = 1e-9


def evaluate(stats: dict, budgets: dict, now: datetime | None = None) -> list[dict]:
    """Budget status rows for the configured (non-null) limits."""
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    timeline = stats.get("timeline") or []
    today = next((d for d in timeline if d.get("date") == day), None)
    month_entries = [d for d in timeline
                     if str(d.get("date", "")).startswith(month)]

    status = []
    for bid, (period, unit) in BUDGET_IDS.items():
        limit = (budgets or {}).get(bid)
        if limit is None:
            continue
        if bid == "daily_tokens":
            used = (today or {}).get("tokens", 0)
        elif bid == "monthly_tokens":
            used = sum(d.get("tokens", 0) for d in month_entries)
        elif bid == "daily_cost_usd":
            used = (today or {}).get("cost", 0.0)
        else:
            used = sum(d.get("cost", 0.0) for d in month_entries)
        ratio = (used / limit) if limit > 0 else 0.0
        status.append({
            "id": bid, "period": period, "unit": unit,
            "limit": limit, "used": used, "ratio": ratio,
            "period_start": day if period == "day" else month,
        })
    return status


def due_alerts(status: list[dict], alerts_cfg: dict, state: dict) -> list[dict]:
    """Thresholds crossed but not yet alerted for this period."""
    thresholds = sorted(alerts_cfg.get("thresholds") or [])
    sent = state.setdefault("alerts_sent", {})
    due = []
    for row in status:
        for threshold in thresholds:
            if row["ratio"] + _EPSILON < threshold:
                continue
            key = f"{row['id']}:{row['period_start']}:{threshold}"
            if key in sent:
                continue
            due.append({**row, "threshold": threshold, "key": key})
    return due


def render_text(alert: dict) -> str:
    used = _fmt(alert["used"], alert["unit"])
    limit = _fmt(alert["limit"], alert["unit"])
    pct = round(alert["ratio"] * 100)
    thr = round(alert["threshold"] * 100)
    return (f"OpenCode Ledger: {alert['id']} at {pct}% of budget "
            f"({used} / {limit}; threshold {thr}%)")


def _fmt(value, unit: str) -> str:
    if unit == "usd":
        return f"${float(value):,.2f}"
    return f"{int(value):,}"


def format_payload(alert: dict, fmt: str) -> tuple[bytes, dict]:
    """(body, extra headers) for a webhook delivery in the given format."""
    text = render_text(alert)
    if fmt == "discord":
        return json.dumps({"content": text}).encode(), \
            {"Content-Type": "application/json"}
    if fmt == "slack":
        return json.dumps({"text": text}).encode(), \
            {"Content-Type": "application/json"}
    if fmt == "ntfy":
        return text.encode(), {"Content-Type": "text/plain; charset=utf-8",
                               "Title": "OpenCode Ledger budget alert"}
    payload = {
        "text": text,
        "budget": alert["id"], "period": alert["period"],
        "unit": alert["unit"], "limit": alert["limit"], "used": alert["used"],
        "ratio": alert["ratio"], "threshold": alert["threshold"],
        "period_start": alert["period_start"],
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return json.dumps(payload).encode(), {"Content-Type": "application/json"}


def dispatch(alert: dict, alerts_cfg: dict, opener=None) -> bool:
    """POST one alert to the configured webhook. Best-effort: never raises."""
    url = (alerts_cfg or {}).get("webhook_url")
    if not url:
        return False
    fmt = (alerts_cfg or {}).get("webhook_format", "generic")
    body, headers = format_payload(alert, fmt)
    headers = {"User-Agent": "opencode-ledger", **headers}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = opener or urllib.request.urlopen
    try:
        with opener(req, timeout=10) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
            return 200 <= int(status) < 300
    except Exception:  # noqa: BLE001 — delivery is best-effort by design
        return False


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("alerts_sent"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"alerts_sent": {}}


def save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def _prune(state: dict, status: list[dict]) -> None:
    current = {f"{row['id']}:{row['period_start']}" for row in status}
    sent = state.setdefault("alerts_sent", {})
    for key in list(sent):
        prefix = ":".join(key.split(":")[:2])
        if prefix not in current:
            del sent[key]


def check(cfg, stats: dict | None = None, now: datetime | None = None,
          send: bool = True, opener=None) -> dict:
    """Evaluate budgets, deliver due alerts, persist dedupe state.

    Returns {"status": [...], "sent": [keys], "exit_code": 0|1}.
    """
    now = now or datetime.now()
    if stats is None:
        from .extract import run
        stats = run(cfg.db_path, write=False)

    status = evaluate(stats, cfg.budgets, now=now)
    state = load_state(cfg.state_file)
    due = due_alerts(status, cfg.alerts, state)

    sent = []
    if send:
        for alert in due:
            if dispatch(alert, cfg.alerts, opener=opener):
                state["alerts_sent"][alert["key"]] = \
                    now.astimezone().isoformat(timespec="seconds")
                sent.append(alert["key"])

    _prune(state, status)
    save_state(cfg.state_file, state)

    exit_code = 1 if any(row["ratio"] + _EPSILON >= 1.0 for row in status) else 0
    return {"status": status, "sent": sent, "exit_code": exit_code}
