#!/usr/bin/env python3
"""Live dashboard server for the OpenCode Lifetime Token Ledger.

Serves index.html + stats.json, re-extracting stats from the OpenCode
database whenever it changes (throttled). Stdlib only.

Usage: python3 serve.py [port]   (default 8765)
"""
import os
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import extract

DEFAULT_DB = extract.DEFAULT_DB


def make_handler(db_path, out_path, doc_root, throttle=3.0):
    """Build a request handler that live-refreshes stats.json.

    throttle: minimum seconds between DB extractions (rate limit while
    OpenCode is actively writing).
    """
    state = {"mtime": None, "stats": None, "last_run": 0.0}

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet: this server polls itself every few seconds

        def _maybe_extract(self):
            if not os.path.exists(db_path):
                raise RuntimeError(f"database not found: {db_path}")
            try:
                mtime = os.path.getmtime(db_path)
            except OSError:
                mtime = None
            now = time.monotonic()
            if (state["stats"] is not None and mtime == state["mtime"]
                    and now - state["last_run"] < 30):
                return  # db unchanged; reuse cached stats for up to 30s
            if now - state["last_run"] < self.throttle:
                return  # rate-limited: keep serving the previous stats
            state["last_run"] = now
            state["mtime"] = mtime
            extract.main(db_path, out_path)
            with open(out_path) as f:
                state["stats"] = f.read()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/stats.json":
                try:
                    self._maybe_extract()
                    body = (state["stats"] or "").encode()
                    if not body:
                        self.send_error(404, "stats not generated yet")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:  # keep the server alive on DB errors
                    self.send_error(500, f"extraction failed: {e}")
                return
            # everything else: static files from doc_root
            self.directory = doc_root
            self.path = "/" if path == "/index.html" else self.path
            super().do_GET()

    Handler.throttle = throttle  # overridable class attr (tests adjust this)
    return Handler


def main(port=8765, db_path=DEFAULT_DB):
    doc_root = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(doc_root, "stats.json")
    if not os.path.exists(db_path):
        sys.exit(f"error: database not found: {db_path}")
    handler = make_handler(db_path, out_path, doc_root)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"LIVE ledger serving on http://localhost:{server.server_address[1]}/")
    print(f"  db: {db_path}")
    print("  stats auto-refresh while this runs — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:2]))
