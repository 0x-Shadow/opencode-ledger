# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-10-17

First stable release. A private, self-hosted token-usage ledger for OpenCode:
zero runtime dependencies, read-only SQLite access, aggregates only.

### Added

- **Dashboard** — lifetime odometer, live auto-refresh with LIVE badge, model
  leaderboard with estimated costs, provider share, burn-rate charts (daily and
  monthly), weekday × hour heatmap, projects, agents, and heaviest sessions.
- **Range chips** — Today / 7d / 30d / 90d / All time views across the whole page.
- **Cost estimates** — bundled list-price table (`pricing.json`) plus per-model
  overrides; unpriced tokens are reported separately instead of guessed.
- **Budgets** — daily/monthly limits in tokens or USD, surfaced in the UI, the
  CLI (`check`, exit 1 on breach) and Prometheus metrics.
- **Alerts** — deduplicated webhook notifications at configurable thresholds
  (default 80% / 100%) with `generic`, `discord`, `slack` and `ntfy` payloads.
- **Prometheus `/metrics`** — totals, per-type tokens, per-model tokens, budget
  ratio/limit, today/this-month gauges, last-extract timestamp.
- **HTTP API** — `/stats.json`, `/api/summary`, `/api/healthz`,
  `/api/export.csv`, `/metrics`; optional token auth via header, query or cookie.
- **Prometheus-safe HTTP** — ETag/304 revalidation and `Cache-Control: no-store`.
- **CLI** — `serve`, `extract`, `check`, `demo`, `config`, `version`; also as
  `python3 -m opencode_ledger`.
- **Demo mode** — deterministic synthetic data, no database needed
  (`opencode-ledger demo`).
- **Deployment** — Dockerfile + Compose stack (non-root, read-only DB mount,
  healthcheck), hardened systemd user unit with installer.
- **Compatibility shims** — `python3 serve.py`, `python3 extract.py` and
  `./refresh.sh` from the original layout keep working.

### Security

- SQLite opened with `mode=ro` + busy timeout; WAL-committed data is visible.
- Only aggregates are emitted — never message content. Nothing leaves the machine
  unless a webhook URL is configured (budget alerts only).
- Optional token auth on every route; binds to `127.0.0.1` by default.
