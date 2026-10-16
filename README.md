# OpenCode Lifetime Token Ledger

A local, private dashboard that counts your total lifetime AI token usage
across every model you've used through OpenCode — an animated odometer of
your grand total, plus model leaderboards, provider share, burn-rate
charts, and your heaviest sessions.

## Usage

```bash
./refresh.sh                          # re-extract stats from opencode.db
python3 -m http.server 8765           # serve the dashboard
# open http://localhost:8765/
```

Must be served over HTTP (the page fetches `stats.json`; `file://` will
show an error panel with these same instructions).

## How it works

- `extract.py` reads `~/.local/share/opencode/opencode.db` **read-only**
  (opened with `immutable=1`, safe alongside a running OpenCode) and
  aggregates every `assistant` message into `stats.json`.
- `index.html` is a single static file — vanilla JS, inline SVG charts,
  zero external requests, works offline.
- Nothing ever leaves your machine: only aggregates are emitted, never
  message content.

## Tests

```bash
python3 -m unittest test_extract -v
```

Includes an integration test that cross-checks extractor totals against a
direct SQL sum over `session_v2`.
