# OpenCode Token Ledger v1.0 — Design Spec

Date: 2026-10-17
Status: Approved (user delegated approval: "proceed without asking me")
Builds on: `2026-10-16-lifetime-tokens-dashboard-design.md` (v0 — local live dashboard)

## Intent

Turn the existing local dashboard into **OpenCode Token Ledger**: a polished,
self-hostable, open-source tool that not only *shows* your lifetime OpenCode
token usage but helps you **manage** it — cost estimates, budgets, alerts,
exports, metrics — with zero cloud dependencies and zero third-party runtime
dependencies.

Success = a stranger can clone the repo, run one command (or one `docker run`),
see their real usage, set a budget, wire it into their monitoring, and deploy it
behind their own auth. Plus: `python3 serve.py` from the v0 workflow keeps
working.

## Audience

- OpenCode users who want to see and control their token burn.
- Self-hosters (Docker / systemd / pipx / plain Python).
- Contributors on GitHub (clean structure, tests, CI, docs).

## Brainstormed feature set

### Kept from v0

Lifetime odometer, live auto-refresh, model leaderboard, provider share,
burn-rate charts, top sessions, newsprint UI, read-only DB access,
aggregate-only output, zero external requests.

### New: analytics

1. **Range filter** (Today / 7d / 30d / 90d / All). Everything re-renders from
   per-day data: the extractor emits per-day component totals and per-day
   per-model rows; the UI computes any range client-side. One source of truth
   (`timeline` + `daily_models`), no re-querying per range.
2. **Cost intelligence.** OpenCode rows carry `cost` (0 for free tiers). Ship a
   `pricing.json` table (USD per 1M tokens per model) and compute an
   **estimated retail cost** — "what this would have cost at list prices".
   User can override prices in config. Free-tier users finally see what their
   usage is worth.
3. **Project breakdown** (`by_project`, from session → project worktree).
4. **Activity heatmap** — weekday × hour matrix, "when do I burn tokens".
5. **Efficiency cards** — cache hit rate, avg tokens/message,
   tokens/session, current streak, best day.
6. **Enriched sessions** — duration, cost, start time.

### New: management

7. **Budgets** — daily/monthly token or USD limits; progress bars in the UI;
   `budget_status` embedded in `stats.json`.
8. **Alerts** — thresholds (default 80% / 100%) dispatched to a webhook
   (generic JSON, Discord, Slack, ntfy) exactly once per period per threshold
   (state file dedupe). `opencode-ledger check` for cron use, exit 1 over budget.
9. **Prometheus `/metrics`** — bounded-cardinality gauges/counters for Grafana.
10. **JSON API + CSV export** — `/api/stats`, `/api/summary`, `/api/export.csv`,
    `/api/healthz`; UI export buttons download CSV/JSON client-side too.
11. **Auth** — optional bearer token (`--token` / env). Bearer header, query
    param (sets cookie), or `X-Ledger-Token`. Constant-time compare. Enables
    LAN/VPS hosting safely.
12. **Demo mode** — `opencode-ledger demo` generates synthetic stats and serves
    them: try before you point it at a DB; also reused by tests.

### New: self-hosting

13. **Config system** — JSON config file + `OPENCODE_LEDGER_*` env vars + CLI
    flags; precedence CLI > env > file > defaults; `opencode-ledger config`
    prints effective config and its sources.
14. **Packaging** — `opencode_ledger` Python package with a console script
    (`pipx install github:0x-Shadow/opencode-ledger`), package data for the UI.
15. **Docker + Compose** — slim non-root image, healthcheck, read-only DB mount,
    `docker compose up`.
16. **systemd unit + installer** — user service with sandboxing hardening.
17. **Root shims** — `serve.py`, `extract.py`, `refresh.sh` keep v0 commands
    working.
18. **HTTP hygiene** — ETag/304 on stats (cheap polling), security headers,
    no-store for API.

### Explicitly cut (YAGNI)

- Multi-user accounts / sharing (single operator tool).
- Historical import from non-OpenCode tools.
- Editing/deleting DB data (strictly read-only).
- Alert channels beyond webhooks (email/SMS) — webhook covers it via relays.
- Per-day Prometheus series (unbounded cardinality).
- Server-side session detail pages (leaderboard is enough).

## Architecture

```
opencode.db (read-only)
   ↳ extract.py   parse + aggregate (pure functions)
   ↳ pricing.py   annotate est_cost per model (shipped table + config overrides)
   ↳ budget.py    budget_status + one-shot webhook alerts (state file)
   → stats.json (schema 2, aggregates only)
   ↳ server.py    HTTP: static UI, /stats.json, /api/*, /metrics, auth, ETag
   ↳ static/index.html   single-file UI, zero external requests
   ↳ cli.py       serve | extract | check | demo | config | version
```

### Package layout

```
opencode_ledger/
  __init__.py      VERSION
  config.py        load/merge/validate (defaults ← file ← env ← CLI)
  extract.py       parse_message, aggregate, load_session_meta, run
  pricing.py       PricingTable, apply_pricing
  budget.py        evaluate, dispatch_webhook, state dedupe
  demo.py          generate_stats()
  metrics.py       prometheus exposition
  server.py        make_handler, run
  cli.py           argparse dispatch
  pricing.json     shipped price table (package data)
  static/index.html
serve.py, extract.py, refresh.sh     root shims (backwards compat)
Dockerfile, docker-compose.yml, Makefile, pyproject.toml
deploy/opencode-ledger.service, deploy/install-systemd.sh
.github/workflows/ci.yml
docs/…, README.md, LICENSE, CONTRIBUTING.md, CHANGELOG.md, SECURITY.md
```

### stats.json schema 2 (additions)

- `schema: 2`, `version` (app version).
- `totals`: + `est_cost`, `priced_tokens`, `unpriced_tokens`.
- `by_model[]`: + `est_cost` (nullable), `priced` bool.
- `by_project[]`, `by_hour[]` (24), `heatmap` (7×24, Monday-first, local time).
- `timeline[]`: + per-component tokens, cost, est_cost.
- `daily_models[]`: flat {date, provider, model, variant, tokens, messages,
  cost, est_cost} — powers range filtering.
- `sessions_top[]`: + cost, est_cost, created, last, duration_min.
- `budget_status[]` when budgets are configured (from budget.py).

Budget and pricing annotations are applied by extract's `run()`, not baked into
`aggregate()` — aggregate stays pure and cheap to test.

### Config

```json
{
  "db_path": "~/.local/share/opencode/opencode.db",
  "host": "127.0.0.1",
  "port": 8765,
  "token": null,
  "throttle_seconds": 3,
  "poll_seconds": 4,
  "budgets": {"daily_tokens": null, "monthly_tokens": null,
               "daily_cost_usd": null, "monthly_cost_usd": null},
  "alerts": {"thresholds": [0.8, 1.0], "webhook_url": null,
              "webhook_format": "generic"},
  "pricing": {},
  "pricing_file": null,
  "state_file": "~/.local/state/opencode-ledger/state.json"
}
```

Env: `OPENCODE_LEDGER_{DB,HOST,PORT,TOKEN,THROTTLE,P0LL,...}` plus
`OPENCODE_LEDGER_CONFIG`. File search: `--config` → env → `./ledger.json` →
`$XDG_CONFIG_HOME/opencode-ledger/config.json` → `~/.config/opencode-ledger/config.json`.

### HTTP surface

| Route | Notes |
|---|---|
| `GET /` | UI (index.html) |
| `GET /stats.json` = `/api/stats` | ETag/304, no-store |
| `GET /api/summary` | compact totals + budget_status |
| `GET /api/healthz` | {ok, version, db, last_extract, live} |
| `GET /metrics` | Prometheus text 0.0.4 |
| `GET /api/export.csv` | daily CSV |

All routes require auth when `token` is configured. Security headers on every
response (nosniff, frame-deny, no-referrer, CSP self+inline). Default bind
127.0.0.1; `0.0.0.0` only when asked. Extraction stays on-demand (mtime change +
throttle), cached, and errors return 500 without killing the server.

### Alerts

Payloads by format: generic `{text, budget, unit, limit, used, ratio,
threshold, period, generated}`; discord `{content}`; slack `{text}`; ntfy plain
body + `Title` header. Fired once per (budget, period, threshold) recorded in
the state file; state pruned to current periods. Webhook failures are logged,
never fatal.

## Error handling

- Extract: DB missing → clear error; malformed rows skipped and counted; DB
  opened `mode=ro` + busy_timeout (never immutable=1, so WAL writes are seen).
- Server: extraction failure → 500 with message, cache preserved, process
  alive; unknown route → 404; auth failure → 401 + WWW-Authenticate.
- UI: no stats.json → instructions panel; fetch failure after first load →
  offline badge; zero tokens → empty state; 401 → token prompt panel.

## Testing

Stdlib `unittest` only (runnable with `python3 -m unittest`), test-per-module:

- `test_config.py` — precedence, env mapping, file search, validation.
- `test_pricing.py` — shipped table, overrides, unknown models.
- `test_extract.py` — v0 suite updated + schema-2 fields, heatmap, projects,
  pricing annotation; real-DB integration cross-check retained.
- `test_budget.py` — status math, threshold edges, dedupe, webhook formats
  (mocked transport), exit-code logic.
- `test_metrics.py` — exposition format + escaping.
- `test_serve.py` — all routes, auth modes, ETag 304, headers, missing DB.
- `test_demo.py` — synthetic stats valid for aggregate + server + UI contract.
- `test_cli.py` — version/config/extract/demo dispatch, shims.

UI: served against demo data; inline JS syntax-checked with `node --check`;
key invariants checked (route list, settings ids) by fetching the page.

## Docs & repo readiness

README (badges, pitch, 3 quickstarts, config summary, API table, security),
`docs/self-hosting.md`, `docs/configuration.md`, `docs/api.md`,
CONTRIBUTING.md, CHANGELOG.md (1.0.0), SECURITY.md, MIT LICENSE,
`.github/workflows/ci.yml` (tests 3.10–3.13 + ruff + docker build),
`.gitignore` (stats.json, state, caches), repo topics/description,
v1.0.0 tag + GitHub release.

`stats.json` is personal data and is removed from version control (gitignored);
the repo ships demo mode instead.

## Risks

- **Schema drift** in `opencode.db` — v0 already tolerates; parser skips and
  counts. Integration test cross-checks both directions.
- **Privacy** — aggregates only; `db_path` in stats.json is a local path
  (acceptable, it's a local tool; documented).
- **Alert spam** — dedupe keyed per period; thresholds configurable.
- **Backwards compat** — root shims + `python3 -m unittest` retained.
