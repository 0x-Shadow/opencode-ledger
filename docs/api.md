# API reference

All routes are served by `opencode-ledger serve` (default `http://127.0.0.1:8765`).
Responses are aggregates only; message content is never served.

## Authentication

When `token` is configured, **every route** requires it. Accepted forms:

| Transport | Example |
| --- | --- |
| Header | `Authorization: Bearer <token>` |
| Header | `X-Ledger-Token: <token>` |
| Query | `?token=<token>` |
| Cookie | `ledger_token=<token>` |

Without the token the server answers `401` with
`WWW-Authenticate: Bearer realm="opencode-ledger"`. Comparison is constant-time.

## Routes

### `GET /`

The dashboard: one self-contained HTML file. No external requests. The page
fetches `./stats.json` and polls it while the LIVE feed is enabled.

### `GET /stats.json` (alias `GET /api/stats`)

The full aggregate document (schema 2). Extracts are throttled
(`throttle_seconds`, default 3 s) and triggered on demand.

Supports `If-None-Match` → `304 Not Modified`; responses carry `ETag` and
`Cache-Control: no-store`.

Top-level keys:

| Key | Description |
| --- | --- |
| `schema` | `2` |
| `version` | opencode-ledger version that produced it |
| `generated` | ISO timestamp of the extract |
| `db_path`, `first_message`, `last_message`, `days_active`, `skipped_rows` | provenance |
| `totals` | grand totals: `tokens`, `input`, `output`, `reasoning`, `cache_read`, `cache_write`, `messages`, `sessions`, `cost`, `est_cost`, `priced_tokens`, `unpriced_tokens` |
| `by_model[]` / `by_provider[]` / `by_agent[]` / `by_project[]` | ranked breakdowns |
| `timeline[]` | per-day: components, `tokens`, `messages`, `cost`, `est_cost` |
| `daily_models[]` | per-day per-model rows, used for exact range filtering |
| `monthly[]` | per-month totals |
| `heatmap` | 7 × 24 matrix, Monday-first, local time |
| `by_hour[]` | 24 hourly totals |
| `sessions_top[]` | heaviest sessions (title, project, model, cost, duration) |
| `budget_status[]` | evaluated budgets (see below) |

### `GET /api/summary`

Compact snapshot for scripts and status bars:

```json
{
  "version": "1.0.0", "schema": 2, "generated": "2026-10-17T12:00:00+00:00",
  "db_path": "~/.local/share/opencode/opencode.db", "days_active": 42,
  "totals": { "tokens": 16972873, "messages": 3120, "...": "..." },
  "budget_status": [ { "id": "daily_cost_usd", "used": 3.1, "limit": 5,
                       "ratio": 0.62, "unit": "usd", "period": "day" } ]
}
```

### `GET /api/healthz`

Liveness endpoint — always `200` while the process is up; `ok` reports
whether a stats document is available yet.

```json
{ "ok": true, "version": "1.0.2", "schema": 2, "demo": false,
  "db": "~/.local/share/opencode/opencode.db",
  "last_extract": "2026-10-17T12:00:00+00:00" }
```

When extraction fails, `ok` is `false` and an `error` string is included.
Because the status code stays `200`, use `/api/ready` (below) if your monitor
only looks at HTTP status codes.

### `GET /api/ready`

Readiness endpoint: `200` when a stats document is available, `503` when
extraction is failing (the body carries the error). The Docker healthcheck
uses this route.

```json
{ "ready": true, "version": "1.0.2", "schema": 2,
  "last_extract": "2026-10-17T12:00:00+00:00" }
```

### `GET /api/export.csv`

The daily timeline as CSV (`Content-Disposition: attachment`):

```csv
date,tokens,input,output,reasoning,cache_read,cache_write,messages,cost,est_cost
2026-10-01,178234,12000,34000,5000,127234,0,420,0.42,0.51
```

### `GET /metrics`

Prometheus text exposition (`text/plain; version=0.0.4`). Metric families:

| Metric | Type | Labels |
| --- | --- | --- |
| `opencode_ledger_info` | gauge | `version`, `schema` |
| `opencode_ledger_tokens` | counter | `type` = input, output, reasoning, cache_read, cache_write |
| `opencode_ledger_tokens_total` | counter | — |
| `opencode_ledger_tokens_by_model` | counter | `model`, `provider` (bounded to the top 20) |
| `opencode_ledger_messages_total` | counter | — |
| `opencode_ledger_sessions_total` | counter | — |
| `opencode_ledger_cost_usd_total` | counter | — |
| `opencode_ledger_estimated_cost_usd_total` | counter | — |
| `opencode_ledger_tokens_today`, `..._tokens_this_month` | gauge | — |
| `opencode_ledger_budget_used_ratio`, `..._budget_limit` | gauge | `budget`, `period`, `unit` |
| `opencode_ledger_days_active` | gauge | — |
| `opencode_ledger_last_extract_timestamp_seconds` | gauge | — |

## Errors

JSON errors: `{"error": "<message>"}`.

| Status | Meaning |
| --- | --- |
| `401` | token required / wrong token |
| `404` | unknown route |
| `500` | extraction failed or `index.html` missing |
