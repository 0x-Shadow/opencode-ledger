#!/usr/bin/env python3
"""Extract token-usage aggregates from OpenCode's local database (schema 2).

Reads OpenCode's SQLite database read-only and emits aggregate-only stats.
Never message content. Bucketing is local-time; the heatmap is Monday-first.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import quote

from . import __version__
from .pricing import PricingTable
from .pricing import apply as apply_pricing

DEFAULT_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
DEFAULT_OUT = os.path.join(os.getcwd(), "stats.json")

COMPONENTS = ("input", "output", "reasoning", "cache_read", "cache_write")
TOP_SESSIONS = 15


class DatabaseIncompatible(RuntimeError):
    """The OpenCode database is missing tables/columns this version needs."""


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
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _row_components(tokens):
    cache = tokens.get("cache") or {}
    return {
        "input": tokens.get("input", 0),
        "output": tokens.get("output", 0),
        "reasoning": tokens.get("reasoning", 0),
        "cache_read": cache.get("read", 0),
        "cache_write": cache.get("write", 0),
    }


def _row_total(tokens):
    return sum(_row_components(tokens).values())


def _blank(cost=False):
    d = {k: 0 for k in COMPONENTS}
    d["tokens"] = 0
    d["messages"] = 0
    if cost:
        d["cost"] = 0.0
    return d


def _add(bucket, comps, cost=None):
    for k in COMPONENTS:
        bucket[k] += comps[k]
    bucket["tokens"] += sum(comps.values())
    bucket["messages"] += 1
    if cost is not None and "cost" in bucket:
        bucket["cost"] += cost


def aggregate(rows, session_meta):
    """Aggregate parsed assistant-message rows into the schema-2 stats dict.

    rows: list of parsed message dicts, each optionally carrying
          'session_id'. session_meta: session_id -> {title, project, model}.
    Pure function: no I/O, no clock reads.
    """
    totals = _blank(cost=True)
    totals.update({"all": 0, "sessions": 0})
    by_model = defaultdict(lambda: _blank(cost=True))
    by_provider = defaultdict(lambda: _blank(cost=True))
    by_agent = defaultdict(lambda: _blank(cost=False))
    by_day = defaultdict(lambda: _blank(cost=True))
    by_month = defaultdict(lambda: _blank(cost=True))
    by_session = defaultdict(lambda: _blank(cost=True))
    by_project = defaultdict(lambda: _blank(cost=True))
    daily_models = {}
    heatmap = [[0] * 24 for _ in range(7)]
    by_hour = [{"hour": h, "tokens": 0, "messages": 0} for h in range(24)]
    project_sessions = defaultdict(set)
    session_models = defaultdict(lambda: defaultdict(int))
    first_ms = last_ms = None

    for row in rows:
        tokens = row.get("tokens") or {}
        model = row.get("model") or {}
        m_id = model.get("id", "unknown")
        provider = model.get("providerID", "unknown")
        variant = model.get("variant")
        comps = _row_components(tokens)
        cost = row.get("cost", 0.0) or 0.0
        total = sum(comps.values())

        _add(totals, comps, cost)
        totals["all"] += total

        _add(by_model[(provider, m_id, variant)], comps, cost)
        _add(by_provider[provider], comps, cost)
        _add(by_agent[row.get("agent") or "unknown"], comps)

        ms = (row.get("time") or {}).get("created")
        if isinstance(ms, (int, float)):
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
            day = dt.strftime("%Y-%m-%d")
            month = dt.strftime("%Y-%m")
            _add(by_day[day], comps, cost)
            _add(by_month[month], comps, cost)
            dm = daily_models.setdefault(
                (day, provider, m_id, variant),
                {"date": day, "provider": provider, "model": m_id,
                 "variant": variant, **_blank(cost=True)})
            _add(dm, comps, cost)
            heatmap[dt.weekday()][dt.hour] += total
            by_hour[dt.hour]["tokens"] += total
            by_hour[dt.hour]["messages"] += 1
            first_ms = ms if first_ms is None else min(first_ms, ms)
            last_ms = ms if last_ms is None else max(last_ms, ms)

        sid = row.get("session_id")
        if sid:
            _add(by_session[sid], comps, cost)
            session_models[sid][(provider, m_id)] += 1
            meta = session_meta.get(sid, {})
            project = meta.get("project") or "unknown"
            _add(by_project[project], comps, cost)
            project_sessions[project].add(sid)
        else:
            _add(by_project["unknown"], comps, cost)

        if sid:
            created = by_session[sid].get("created")
            if isinstance(ms, (int, float)):
                by_session[sid]["created"] = ms if created is None else min(created, ms)
                by_session[sid]["last"] = max(by_session[sid].get("last", ms), ms)

    totals["sessions"] = len(by_session)

    model_list = [{"provider": p, "model": m, "variant": v, **vals}
                  for (p, m, v), vals in by_model.items()]
    model_list.sort(key=lambda x: x["tokens"], reverse=True)
    provider_list = [{"provider": p, **vals} for p, vals in by_provider.items()]
    provider_list.sort(key=lambda x: x["tokens"], reverse=True)
    agent_list = [{"agent": a, **vals} for a, vals in by_agent.items()]
    agent_list.sort(key=lambda x: x["tokens"], reverse=True)
    timeline = [{"date": d, **vals} for d, vals in sorted(by_day.items())]
    monthly = [{"month": m, **vals} for m, vals in sorted(by_month.items())]
    project_list = []
    for proj, vals in by_project.items():
        entry = {"project": proj, **vals}
        entry["sessions"] = len(project_sessions[proj])
        project_list.append(entry)
    project_list.sort(key=lambda x: x["tokens"], reverse=True)
    best_day = max(by_day.items(), key=lambda kv: kv[1]["tokens"], default=None)

    sessions_top = []
    for sid, vals in sorted(by_session.items(),
                            key=lambda kv: kv[1]["tokens"], reverse=True)[:TOP_SESSIONS]:
        meta = session_meta.get(sid, {})
        top_model = max(session_models[sid].items(),
                        key=lambda kv: kv[1], default=((None, None), 0))[0]
        created, last = vals.get("created"), vals.get("last")
        duration = round((last - created) / 60000, 1) if created and last and last >= created else None
        sessions_top.append({
            "title": meta.get("title") or "untitled",
            "project": meta.get("project") or "unknown",
            "model": meta.get("model") or (top_model[1] if top_model[1] else "unknown"),
            "provider": top_model[0] or "unknown",
            "tokens": vals["tokens"],
            "messages": vals["messages"],
            "cost": vals["cost"],
            "input": vals["input"],
            "output": vals["output"],
            "reasoning": vals["reasoning"],
            "cache_read": vals["cache_read"],
            "cache_write": vals["cache_write"],
            "created": _ms_to_iso(created),
            "last": _ms_to_iso(last),
            "duration_min": duration,
        })

    return {
        "schema": 2,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": totals,
        "by_model": model_list,
        "by_provider": provider_list,
        "by_agent": agent_list,
        "by_project": project_list,
        "timeline": timeline,
        "monthly": monthly,
        "daily_models": [daily_models[k] for k in sorted(daily_models)],
        "by_hour": by_hour,
        "heatmap": heatmap,
        "sessions_top": sessions_top,
        "first_message": _ms_to_iso(first_ms),
        "last_message": _ms_to_iso(last_ms),
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
        project = os.path.basename(worktree.rstrip("/")) if worktree else None
        model_id = None
        if model:
            try:
                model_id = json.loads(model).get("id")
            except (json.JSONDecodeError, TypeError, AttributeError):
                model_id = str(model)
        meta[sid] = {"title": title, "project": project, "model": model_id}
    return meta


def read_rows(conn):
    """Assistant-message cursor yielding (session_id, data), streamed."""
    return conn.execute(
        "SELECT session_id, data FROM session_message WHERE type='assistant'"
    )


def run(db_path=DEFAULT_DB, out_path=DEFAULT_OUT, pricing_table=None,
        write=True):
    """Extract, annotate with pricing, optionally write stats.json, return stats."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"database not found: {db_path}")
    # mode=ro (not immutable=1) so committed WAL data is visible while
    # OpenCode is running; busy_timeout guards against write locks. The path
    # is percent-encoded so reserved characters (?, #, %) in custom database
    # filenames can't corrupt the URI.
    uri = "file:" + quote(os.path.abspath(db_path)) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA busy_timeout=3000")
    rows, skipped = [], 0
    try:
        try:
            session_meta = load_session_meta(conn)
            for sid, data in read_rows(conn):  # stream, don't materialise
                parsed = parse_message(data)
                if parsed is None:
                    skipped += 1
                    continue
                parsed["session_id"] = sid
                rows.append(parsed)
        except sqlite3.DatabaseError as e:
            raise DatabaseIncompatible(
                f"unsupported database layout: {e}; expected the OpenCode "
                f"table 'session_message' (session_id, type, data). If a new "
                f"OpenCode version changed its schema, please report it: "
                f"https://github.com/0x-Shadow/opencode-ledger/issues"
            ) from None
    finally:
        conn.close()

    stats = aggregate(rows, session_meta)
    stats["skipped_rows"] = skipped
    stats["db_path"] = db_path
    stats["version"] = __version__
    apply_pricing(stats, pricing_table or PricingTable())

    if write:
        parent = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(parent, exist_ok=True)
        tmp = out_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(stats, f, indent=1)
        os.replace(tmp, out_path)
    return stats


def main(db_path=None, out_path=None):
    db_path = db_path or DEFAULT_DB
    out_path = out_path or DEFAULT_OUT
    try:
        stats = run(db_path, out_path)
    except FileNotFoundError as e:
        sys.exit(f"error: {e}")
    except DatabaseIncompatible as e:
        sys.exit(f"error: {e}")
    t = stats["totals"]
    print(f"wrote {out_path}")
    print(f"total tokens: {t['all']:,}  (in {t['input']:,} / out {t['output']:,} "
          f"/ reason {t['reasoning']:,} / cache r {t['cache_read']:,} "
          f"/ w {t['cache_write']:,})")
    print(f"messages: {t['messages']:,}  sessions: {t['sessions']}  "
          f"days active: {stats['days_active']}  skipped rows: {stats['skipped_rows']}")


if __name__ == "__main__":
    main(*(sys.argv[1:3]))
