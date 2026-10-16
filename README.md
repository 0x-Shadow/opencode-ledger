# OpenCode Lifetime Token Ledger

A local, private dashboard that counts your total lifetime AI token usage
across every model you've used through OpenCode — an animated odometer of
your grand total, plus model leaderboards, provider share, burn-rate
charts, and your heaviest sessions.

## Usage

**Live mode (recommended)** — the counter ticks up in real time as you use OpenCode:

```bash
python3 serve.py                      # serves + auto-refreshes stats from the DB
# open http://localhost:8765/  — a pulsing ● LIVE badge confirms the feed
```

The server re-extracts totals whenever `opencode.db` changes (throttled), and
the page polls every 4 seconds, rolling the odometer up to the new number.

**Static mode** — no server process beyond a file server:

```bash
./refresh.sh                          # re-extract stats from opencode.db
python3 -m http.server 8765           # serve the dashboard
# open http://localhost:8765/
```

Must be served over HTTP (the page fetches `stats.json`; `file://` will
show an error panel with these same instructions).

## How it works

- `extract.py` reads `~/.local/share/opencode/opencode.db` **read-only**
  (opened with `mode=ro` + busy_timeout, safe alongside a running OpenCode;
  WAL-committed data is visible) and aggregates every `assistant` message
  into `stats.json`.
- `serve.py` wraps that extraction in a tiny stdlib HTTP server: it serves
  the dashboard, re-extracts when the DB's mtime changes (throttled to 3s),
  and always sends `Cache-Control: no-store`.
- `index.html` is a single static file — vanilla JS, inline SVG charts,
  zero external requests. It polls `stats.json` every 4s and smoothly rolls
  the odometer to new totals; a ● LIVE badge reflects the feed state.
- Nothing ever leaves your machine: only aggregates are emitted, never
  message content.

## Tests

```bash
python3 -m unittest -v
```

- `test_extract.py` — extractor unit tests plus an integration test that
  cross-checks totals against SQLite's own JSON aggregation (two-sided).
- `test_serve.py` — spins up the live server against a throwaway fake DB,
  verifies live re-extraction, throttling, and graceful 500s.
