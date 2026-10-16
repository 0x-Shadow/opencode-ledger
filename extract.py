#!/usr/bin/env python3
"""Extract lifetime token-usage aggregates from OpenCode's local database.

Reads ~/.local/share/opencode/opencode.db read-only and writes stats.json
next to this script. Only aggregates are emitted — never message content.
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

DEFAULT_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "stats.json")


def parse_message(data):
    """Parse an assistant message's data JSON. Returns None if malformed
    or missing/mistyped the keys we aggregate on."""
    try:
        d = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    tokens = d.get("tokens")
    model = d.get("model")
    if not isinstance(tokens, dict) or not isinstance(model, dict):
        return None
    for k in ("input", "output", "reasoning"):
        if not isinstance(tokens.get(k), (int, float)):
            return None
    cache = tokens.get("cache")
    if not isinstance(cache, dict) or not isinstance(cache.get("read"), (int, float)) \
            or not isinstance(cache.get("write"), (int, float)):
        return None
    return d


def _ms_to_iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _row_total(tokens):
    cache = tokens.get("cache") or {}
    return (tokens.get("input", 0) + tokens.get("output", 0)
            + tokens.get("reasoning", 0) + cache.get("read", 0)
            + cache.get("write", 0))


def aggregate(rows, session_meta):
    """Aggregate parsed assistant-message rows into the stats dict.

    rows: list of parsed message dicts, each optionally carrying
          'session_id'. session_meta: session_id -> {title, project}.
    """
    totals = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0,
              "cache_write": 0, "all": 0, "cost": 0.0, "messages": 0,
              "sessions": 0}
    by_model = defaultdict(lambda: {"messages": 0, "tokens": 0, "cost": 0.0})
    by_provider = defaultdict(lambda: {"messages": 0, "tokens": 0, "cost": 0.0})
    by_agent = defaultdict(lambda: {"messages": 0, "tokens": 0})
    by_day = defaultdict(lambda: {"tokens": 0, "messages": 0})
    by_month = defaultdict(lambda: {"tokens": 0, "messages": 0})
    by_session = defaultdict(lambda: {"tokens": 0, "messages": 0})
    first_ms = last_ms = None
    sessions = set()

    for row in rows:
        tokens = row.get("tokens") or {}
        cache = tokens.get("cache") or {}
        model = row.get("model") or {}
        m_id = model.get("id", "unknown")
        provider = model.get("providerID", "unknown")
        variant = model.get("variant")
        total = _row_total(tokens)

        totals["input"] += tokens.get("input", 0)
        totals["output"] += tokens.get("output", 0)
        totals["reasoning"] += tokens.get("reasoning", 0)
        totals["cache_read"] += cache.get("read", 0)
        totals["cache_write"] += cache.get("write", 0)
        totals["all"] += total
        totals["cost"] += row.get("cost", 0.0) or 0.0
        totals["messages"] += 1

        mk = (provider, m_id, variant)
        by_model[mk]["messages"] += 1
        by_model[mk]["tokens"] += total
        by_model[mk]["cost"] += row.get("cost", 0.0) or 0.0
        by_provider[provider]["messages"] += 1
        by_provider[provider]["tokens"] += total
        by_provider[provider]["cost"] += row.get("cost", 0.0) or 0.0

        agent = row.get("agent") or "unknown"
        by_agent[agent]["messages"] += 1
        by_agent[agent]["tokens"] += total

        ms = ((row.get("time") or {}).get("created"))
        if isinstance(ms, (int, float)):
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
            day = dt.strftime("%Y-%m-%d")
            month = dt.strftime("%Y-%m")
            by_day[day]["tokens"] += total
            by_day[day]["messages"] += 1
            by_month[month]["tokens"] += total
            by_month[month]["messages"] += 1
            first_ms = ms if first_ms is None else min(first_ms, ms)
            last_ms = ms if last_ms is None else max(last_ms, ms)

        sid = row.get("session_id")
        if sid:
            sessions.add(sid)
            by_session[sid]["tokens"] += total
            by_session[sid]["messages"] += 1

    totals["sessions"] = len(sessions)

    model_list = [
        {"provider": p, "model": m, "variant": v, **vals}
        for (p, m, v), vals in by_model.items()
    ]
    model_list.sort(key=lambda x: x["tokens"], reverse=True)
    provider_list = [{"provider": p, **vals}
                     for p, vals in by_provider.items()]
    provider_list.sort(key=lambda x: x["tokens"], reverse=True)
    agent_list = [{"agent": a, **vals} for a, vals in by_agent.items()]
    agent_list.sort(key=lambda x: x["tokens"], reverse=True)
    timeline = [{"date": d, **vals} for d, vals in sorted(by_day.items())]
    monthly = [{"month": m, **vals} for m, vals in sorted(by_month.items())]
    best_day = max(by_day.items(), key=lambda kv: kv[1]["tokens"],
                   default=None)

    sessions_top = []
    for sid, vals in sorted(by_session.items(),
                            key=lambda kv: kv[1]["tokens"], reverse=True)[:15]:
        meta = session_meta.get(sid, {})
        created = None
        sessions_top.append({
            "title": meta.get("title") or "untitled",
            "project": meta.get("project") or "unknown",
            "model": meta.get("model") or "unknown",
            "tokens": vals["tokens"],
            "messages": vals["messages"],
            "created": created,
        })

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": totals,
        "by_model": model_list,
        "by_provider": provider_list,
        "by_agent": agent_list,
        "timeline": timeline,
        "monthly": monthly,
        "sessions_top": sessions_top,
        "first_message": _ms_to_iso(first_ms) if first_ms else None,
        "last_message": _ms_to_iso(last_ms) if last_ms else None,
        "days_active": len(by_day),
        "best_day": ({"date": best_day[0], "tokens": best_day[1]["tokens"]}
                     if best_day else None),
        "skipped_rows": 0,
    }


def load_session_meta(conn):
    """session_id -> {title, project, model} via session_v2 + project."""
    meta = {}
    try:
        rows = conn.execute(
            "SELECT s.id, s.title, s.model, p.worktree FROM session_v2 s "
            "LEFT JOIN project p ON p.id = s.project_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return meta
    for sid, title, model, worktree in rows:
        project = os.path.basename(worktree) if worktree else None
        model_id = None
        if model:
            try:
                model_id = json.loads(model).get("id")
            except (json.JSONDecodeError, TypeError):
                model_id = str(model)
        meta[sid] = {"title": title, "project": project, "model": model_id}
    return meta


def main(db_path=DEFAULT_DB, out_path=DEFAULT_OUT):
    if not os.path.exists(db_path):
        sys.exit(f"error: database not found: {db_path}")
    # mode=ro (not immutable=1) so committed WAL data is visible while
    # OpenCode is running; busy_timeout guards against write locks.
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        session_meta = load_session_meta(conn)
        raw_rows = conn.execute(
            "SELECT session_id, data FROM session_message "
            "WHERE type='assistant'"
        ).fetchall()
    finally:
        conn.close()

    rows, skipped = [], 0
    for sid, data in raw_rows:
        parsed = parse_message(data)
        if parsed is None:
            skipped += 1
            continue
        parsed["session_id"] = sid
        rows.append(parsed)

    stats = aggregate(rows, session_meta)
    stats["skipped_rows"] = skipped
    stats["db_path"] = db_path

    with open(out_path, "w") as f:
        json.dump(stats, f, indent=1)

    t = stats["totals"]
    print(f"wrote {out_path}")
    print(f"total tokens: {t['all']:,}  (in {t['input']:,} / out {t['output']:,} "
          f"/ reason {t['reasoning']:,} / cache r {t['cache_read']:,} "
          f"/ w {t['cache_write']:,})")
    print(f"messages: {t['messages']:,}  sessions: {t['sessions']}  "
          f"days active: {stats['days_active']}  skipped rows: {skipped}")


if __name__ == "__main__":
    main(*(sys.argv[1:3]))
