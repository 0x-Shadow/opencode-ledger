# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2] — 2026-10-17

### Security

- **HEAD requests now require authentication and follow the same routing as
  GET.** Previously the inherited `SimpleHTTPRequestHandler.do_HEAD` bypassed
  auth and served the server's working directory to unauthenticated clients
  (file metadata, directory listings, partial listings).
- **Configuration validation hardened** — budgets, thresholds and pricing now
  reject non-finite numbers (`NaN`, `Infinity`) and booleans; `port` must be an
  integer; `alerts.webhook_url` must be an `http(s)` URL with a hostname and
  no embedded credentials.
- **`stats.json` is written with `0600` permissions** and its path is
  configurable (`output` / `OPENCODE_LEDGER_OUTPUT`).
- The `ledger_token` cookie is marked `Secure` when the request arrives with
  `X-Forwarded-Proto: https` behind a TLS proxy.

### Added

- **`/api/ready`** — readiness endpoint (`200`/`503`) for orchestrators and
  status-code-based monitors; the Docker healthcheck now uses it.
  `/api/healthz` is documented as liveness-only.
- **Regression suite** — a minimal stdlib-only `unittest` suite (`make test`,
  no pytest) covering the parser, exact totals, incompatible databases,
  pricing edge cases, budget thresholds and dedup, config validation, and
  HTTP auth for GET *and* HEAD.

### Fixed

- **SQLite URIs are percent-encoded** — database paths containing reserved
  characters (`?`, `#`, `%`, spaces) no longer open the wrong file or silently
  lose read-only mode.
- Extraction now streams rows instead of materialising them first (~14% less
  peak memory on a 200k-message synthetic database, same runtime).
- The `Server` header identifies the project instead of exposing the stock
  `SimpleHTTP/0.6` handler.

## [1.0.1] — 2026-10-17

### Fixed

- **Clear compatibility errors** — a database missing the `session_message`
  table (or a corrupt/non-SQLite file) now fails with a single actionable
  `error:` line instead of a Python traceback, and points at the issue tracker
  for schema changes. Config errors (invalid `webhook_url`, missing config
  file) also print clean CLI errors now.

### Added

- **Webhook URL validation** — `alerts.webhook_url` must be an `http(s)` URL;
  invalid values are rejected at config load with the offending key named.
- **README screenshot** — demo-mode dashboard preview (`docs/screenshot.png`).

### Docs

- **Cost accuracy** — estimates vs provider billing spelled out, pricing key
  matching (`provider/model`, then bare `model`) and the price-as-of date
  documented in `docs/configuration.md`.
- **Remote-access hardening checklist** — TLS, firewall, token handling
  (`?token=` caveat), `/metrics` scraping and webhook guidance added to the
  self-hosting guide.

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
