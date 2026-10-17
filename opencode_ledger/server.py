"""HTTP server: dashboard UI, JSON API, CSV export, Prometheus metrics.

Live extraction is on-demand: each stats request re-extracts when the
database's mtime changed (throttled), keeps the previous result on error,
and never lets one bad extraction kill the process.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import threading
import time
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, extract
from . import budget as budget_mod
from . import demo as demo_mod
from .metrics import render as render_metrics

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy":
        "default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'",
}


def csv_export(stats: dict) -> str:
    """Daily aggregates as CSV (the timeline, one row per active day)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["date", "tokens", "input", "output", "reasoning",
                     "cache_read", "cache_write", "messages", "cost",
                     "est_cost"])
    for day in stats.get("timeline") or []:
        writer.writerow([
            day.get("date"), day.get("tokens", 0), day.get("input", 0),
            day.get("output", 0), day.get("reasoning", 0),
            day.get("cache_read", 0), day.get("cache_write", 0),
            day.get("messages", 0), day.get("cost", 0.0),
            day.get("est_cost", 0.0),
        ])
    return buf.getvalue()


def make_handler(cfg, out_path: str, doc_root: str, demo_stats: dict | None = None):
    """Build a request handler for the given config.

    demo_stats: when set, serve these synthetic stats with no database.
    """
    state = {
        "stats": None, "stats_json": None, "etag": None,
        "mtime": None, "last_run": 0.0, "error": None,
    }
    lock = threading.Lock()
    index_path = os.path.join(doc_root, "index.html")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet: the page polls every few seconds

        # ---- extraction ------------------------------------------------
        def _refresh(self):
            with lock:
                if demo_stats is not None:
                    if state["stats"] is None:
                        stats = json.loads(json.dumps(demo_stats))
                        stats["budget_status"] = budget_mod.evaluate(
                            stats, cfg.budgets)
                        state["stats"] = stats
                        state["stats_json"] = json.dumps(stats)
                        state["etag"] = hashlib.sha256(
                            state["stats_json"].encode()).hexdigest()[:16]
                        state["error"] = None
                    return
                if not os.path.exists(cfg.db_path):
                    state["error"] = f"database not found: {cfg.db_path}"
                    raise RuntimeError(state["error"])
                now = time.monotonic()
                try:
                    mtime = os.path.getmtime(cfg.db_path)
                except OSError:
                    mtime = None
                if (state["stats"] is not None and mtime == state["mtime"]
                        and now - state["last_run"] < cfg.stale_seconds):
                    return
                if now - state["last_run"] < cfg.throttle_seconds:
                    return
                state["last_run"] = now
                state["mtime"] = mtime
                stats = extract.run(cfg.db_path, out_path, write=True)
                result = budget_mod.check(cfg, stats=stats, send=True)
                stats["budget_status"] = result["status"]
                state["stats"] = stats
                state["stats_json"] = json.dumps(stats)
                state["etag"] = hashlib.sha256(
                    state["stats_json"].encode()).hexdigest()[:16]
                state["error"] = None

        # ---- auth ------------------------------------------------------
        def _authenticated(self) -> bool:
            token = cfg.token
            if not token:
                return True
            auth = self.headers.get("Authorization", "")
            candidates = []
            if auth.startswith("Bearer "):
                candidates.append(auth[7:])
            candidates.append(self.headers.get("X-Ledger-Token", ""))
            query = parse_qs(urlparse(self.path).query)
            if query.get("token"):
                self._token_from_query = True
                candidates.append(query["token"][0])
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if "ledger_token" in cookie:
                candidates.append(cookie["ledger_token"].value)
            return any(c and hmac.compare_digest(c, token) for c in candidates)

        # ---- responses -------------------------------------------------
        def _send(self, status, body: bytes, content_type: str,
                  extra_headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            if getattr(self, "_token_from_query", False):
                proto = self.headers.get("X-Forwarded-Proto", "")
                secure = "; Secure" if proto.split(",")[0].strip().lower() == "https" else ""
                self.send_header(
                    "Set-Cookie",
                    f"ledger_token={cfg.token}; Path=/; HttpOnly; SameSite=Lax{secure}")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if status != 304 and self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, status, payload, extra_headers=None):
            body = json.dumps(payload).encode()
            self._send(status, body, "application/json", extra_headers)

        def _send_error_json(self, status, message, extra_headers=None):
            self._send_json(status, {"error": message}, extra_headers)

        def _refresh_or_500(self):
            try:
                self._refresh()
                return True
            except Exception as e:  # keep the server alive on DB errors
                self._send_error_json(500, f"extraction failed: {e}")
                return False

        # ---- routing ---------------------------------------------------
        def do_GET(self):
            self._token_from_query = False
            path = urlparse(self.path).path
            if not self._authenticated():
                self._send_error_json(
                    401, "unauthorized",
                    {"WWW-Authenticate": 'Bearer realm="opencode-ledger"'})
                return

            if path in ("/stats.json", "/api/stats"):
                if not self._refresh_or_500():
                    return
                body = (state["stats_json"] or "").encode()
                if not body:
                    self._send_error_json(404, "stats not generated yet")
                    return
                etag = f'"{state["etag"]}"'
                if self.headers.get("If-None-Match") == etag:
                    self._send(304, b"", "application/json", {"ETag": etag})
                    return
                self._send(200, body, "application/json", {"ETag": etag})
                return

            if path == "/api/summary":
                if not self._refresh_or_500():
                    return
                stats = state["stats"] or {}
                totals = stats.get("totals") or {}
                self._send_json(200, {
                    "version": stats.get("version", __version__),
                    "schema": stats.get("schema", 2),
                    "generated": stats.get("generated"),
                    "db_path": stats.get("db_path", cfg.db_path),
                    "days_active": stats.get("days_active", 0),
                    "totals": totals,
                    "budget_status": stats.get("budget_status", []),
                })
                return

            if path == "/api/healthz":
                try:
                    self._refresh()
                except Exception as e:
                    self._send_json(200, {
                        "ok": False, "version": __version__,
                        "demo": demo_stats is not None,
                        "db": cfg.db_path, "error": str(e),
                    })
                    return
                stats = state["stats"] or {}
                self._send_json(200, {
                    "ok": state["stats_json"] is not None,
                    "version": __version__,
                    "schema": stats.get("schema"),
                    "demo": demo_stats is not None,
                    "db": cfg.db_path,
                    "last_extract": stats.get("generated"),
                })
                return

            if path == "/api/ready":
                try:
                    self._refresh()
                except Exception as e:
                    self._send_json(503, {
                        "ready": False, "version": __version__,
                        "db": cfg.db_path, "error": str(e),
                    })
                    return
                stats = state["stats"] or {}
                ready = state["stats_json"] is not None
                self._send_json(200 if ready else 503, {
                    "ready": ready, "version": __version__,
                    "schema": stats.get("schema"),
                    "last_extract": stats.get("generated"),
                })
                return

            if path == "/metrics":
                if not self._refresh_or_500():
                    return
                stats = state["stats"] or {}
                body = render_metrics(
                    stats, budget_status=stats.get("budget_status")).encode()
                self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
                return

            if path == "/api/export.csv":
                if not self._refresh_or_500():
                    return
                body = csv_export(state["stats"] or {}).encode()
                self._send(200, body, "text/csv; charset=utf-8", {
                    "Content-Disposition":
                        'attachment; filename="opencode-ledger-daily.csv"',
                })
                return

            if path in ("/", "/index.html"):
                try:
                    with open(index_path, "rb") as f:
                        body = f.read()
                except OSError:
                    self._send_error_json(500, f"index.html not found in {doc_root}")
                    return
                self._send(200, body, "text/html; charset=utf-8")
                return

            self._send_error_json(404, "not found")

        def do_HEAD(self):
            # HEAD must obey the same auth and routing as GET. Without this,
            # the inherited SimpleHTTPRequestHandler.do_HEAD() would serve
            # static files from the process CWD without any authentication.
            self.do_GET()

        def version_string(self):
            return f"opencode-ledger/{__version__}"

    return Handler


def run(cfg, demo: bool = False) -> None:
    """Serve until interrupted. demo=True serves synthetic data, no database."""
    doc_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    out_path = os.path.abspath(os.path.expanduser(cfg.output))
    demo_stats = None
    if demo:
        demo_stats = demo_mod.generate_stats()
        print("DEMO MODE — serving synthetic data; no database is read.")
    server = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(
        cfg, out_path, doc_root, demo_stats=demo_stats))
    host, port = server.server_address[0], server.server_address[1]
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"OpenCode Token Ledger v{__version__} serving on http://{shown}:{port}/")
    if not demo:
        print(f"  db: {cfg.db_path}")
        print(f"  stats auto-refresh on change — stats.json is also written to {out_path}")
    if cfg.token:
        print("  auth: enabled (Bearer / X-Ledger-Token / ?token=…)")
    if cfg.alerts.get("webhook_url"):
        print("  alerts: webhook configured — budget crossings will be posted")
    print("  Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
