# OpenCode Token Ledger v1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (method supplied by user: "proceed without asking me") to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the v0 local dashboard as `opencode-ledger` v1.0 — a self-hostable GitHub-ready tool with range filtering, cost estimates, budgets, alerts, Prometheus metrics, exports, auth, Docker/systemd, packaging, CI, and docs — while keeping `python3 serve.py` working.

**Architecture:** A stdlib-only Python package (`opencode_ledger/`) whose pure extractor emits aggregate-only `stats.json` (schema 2); pricing/budget modules annotate it; a threaded stdlib HTTP server serves the single-file UI plus JSON/CSV/Prometheus endpoints with optional token auth. Root shims preserve v0 commands.

**Tech Stack:** Python ≥3.10 stdlib (sqlite3, http.server, urllib, unittest), vanilla JS/SVG single-file UI, Docker, systemd, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-17-opencode-ledger-v1-design.md`

## Global Constraints

- Runtime deps: Python stdlib only. Test runner: `python3 -m unittest`.
- DB opened read-only `file:<path>?mode=ro` + `busy_timeout`; never written.
- Aggregate-only output; never message content.
- UI: single `opencode_ledger/static/index.html`, zero external requests, accessible, `prefers-reduced-motion` honored, works with no settings defined.
- Version string `1.0.0` lives in `opencode_ledger/__init__.py` as `__version__`.
- Backwards compat: root `serve.py`, `extract.py`, `./refresh.sh` keep working.
- Personal `stats.json` is gitignored, not committed.

## Review Focus

- New/empty install: `serve` with no `stats.json` and no DB must not crash; UI must show instructions (tested in Tasks 6, 9).
- Auth: every route (including `/metrics`, static, CSV) must require the token when set; wrong token must not leak stats (Task 8).
- Budget edge math: usage exactly 0, exactly at threshold, first-of-month rollover must not double-fire or misfire alerts (Task 5).
- Pricing: unknown models must yield `est_cost: null`, never a crash or a wrong number (Task 3).
- Range filter: empty range (e.g. "Today" before any message today) must render zero states, not blanks or errors (Task 9).

---

### Task 1: Package skeleton + config

**Files:**
- Create: `opencode_ledger/__init__.py` (`__version__ = "1.0.0"`), `opencode_ledger/config.py`
- Test: `test_config.py`
- Keep: root `extract.py`, `serve.py` unchanged for now (shims come in Task 10)

**Interfaces:**
- Produces: `Config` dataclass with fields `db_path, host, port, token, throttle_seconds, poll_seconds, stale_seconds, budgets (dict), alerts (dict), pricing (dict), pricing_file, state_file, config_file`; `DEFAULTS`, `ENV_MAP`, `load(cli: dict | None = None, env: dict | None = None, cwd: str | None = None) -> Config`, `describe(cfg) -> list[dict]` (key/value/source rows), `ConfigError`.

- [ ] Step 1: failing tests — defaults; file merges; env overrides file; cli overrides env; `~` expansion; unknown keys rejected; invalid port rejected; `load` never crashes on missing/malformed file.
- [ ] Step 2: run `python3 -m unittest test_config -v` → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit.

### Task 2: Pricing table

**Files:**
- Create: `opencode_ledger/pricing.py`, `opencode_ledger/pricing.json`
- Test: `test_pricing.py`

**Interfaces:**
- Produces: `PricingTable(overrides: dict | None = None, path: str | None = None)` with `.price_for(provider, model, variant) -> dict | None` (keys `input, output, reasoning, cache_read, cache_write` USD-per-1M); `PricingTable.estimate(row_tokens, price) -> float`; `apply(stats, table) -> None` annotating `by_model[].est_cost/.priced`, `timeline[].est_cost`, `daily_models[].est_cost`, `sessions_top[].est_cost`, `totals.est_cost/.priced_tokens/.unpriced_tokens`. Key format `provider/model`, variant-agnostic; `*` glob for output price when only input known is NOT supported (YAGNI). `reasoning` defaults to output price when absent.

- [ ] Step 1: failing tests — shipped table has ≥10 models; exact lookup; override wins; unknown → None; estimate math (1M input @ $1 = $1); annotation totals; `est_cost` null when unpriced.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement + seed `pricing.json` (opencode zen models + common paid models, marked clearly as editable estimates).
- [ ] Step 4: run → pass.
- [ ] Step 5: commit.

### Task 3: Extractor v2

**Files:**
- Create: `opencode_ledger/extract.py` (move logic from root `extract.py`, add schema-2 fields)
- Test: `test_extract.py` (extend existing; import package)
- Modify: root `extract.py` → temporarily keep working via package import re-export (final shim in Task 10)

**Interfaces:**
- Produces: `parse_message(data) -> dict | None` (unchanged contract); `aggregate(rows, session_meta) -> dict` pure, schema 2 (adds per-day components, `daily_models`, `by_project`, `by_hour` (24), `heatmap` (7×24 Monday-first local), enriched `sessions_top` with cost/created/last/duration_min); `load_session_meta(conn) -> dict` (adds project); `run(db_path, out_path, pricing=None, budgets=None, ...) -> dict` (calls `apply` + budget evaluate, writes JSON); `main()` CLI-compatible.

- [ ] Step 1: failing tests — new fields shape, heatmap hour/day bucketing, project aggregation, session duration math, empty-db zeros, malformed rows still skipped, real-DB integration cross-check (retained).
- [ ] Step 2: run → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass (`python3 -m unittest test_extract -v`).
- [ ] Step 5: commit.

### Task 4: Demo generator

**Files:**
- Create: `opencode_ledger/demo.py`
- Test: `test_demo.py`

**Interfaces:**
- Produces: `generate_stats(days: int = 60, seed: int = 7, now: datetime | None = None) -> dict` producing a full schema-2 stats dict (multiple models/providers/projects/agents, cache-heavy, budget_status when budgets passed via `generate_stats(..., budgets=cfg.budgets, alerts=...)` — no: keep pure; budgets applied by caller). `write_demo(out_path, ...) -> dict`.

- [ ] Step 1: failing tests — deterministic for seed; totals equal sum of by_model; timeline contiguous; heatmap size 7×24; sessions non-empty; passes `extract.aggregate`-shape contract (keys subset check).
- [ ] Step 2: run → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit.

### Task 5: Budgets + alerts

**Files:**
- Create: `opencode_ledger/budget.py`
- Test: `test_budget.py`

**Interfaces:**
- Consumes: stats dict from Task 3 (`timeline[].tokens/cost`, `totals`).
- Produces: `evaluate(stats, budgets, now=None) -> list[dict]` with `{id, period: "day"|"month", unit: "tokens"|"usd", limit, used, ratio, period_start}`; `due_alerts(status, alerts_cfg, state, now) -> list[dict]`; `load_state(path)/save_state(path, state)`; `dispatch(alert, cfg, opener=urllib...) -> bool` formats `generic|discord|slack|ntfy`; `run_check(cfg, stats=None) -> (status, sent, exit_code)` used by CLI; dedupe keys `"{id}:{period_start}:{threshold}"`, prune others.

- [ ] Step 1: failing tests — zero usage; exact threshold (>= fires at 0.8 exactly); below threshold silent; dedupe same period; new period fires again; all 4 webhook formats (fake opener); webhook failure swallowed; exit code 1 only at ratio ≥ 1.0; month rollover.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit.

### Task 6: Prometheus metrics

**Files:**
- Create: `opencode_ledger/metrics.py`
- Test: `test_metrics.py`

**Interfaces:**
- Produces: `render(stats, budget_status=None) -> str` with series: `opencode_ledger_tokens{type=...}`, `_tokens_total`, `_messages_total`, `_sessions_total`, `_cost_usd_total`, `_estimated_cost_usd_total` (when priced), `_tokens_by_model{provider,model,variant}`, `_tokens_today`, `_tokens_this_month`, `_budget_used_ratio{budget,period}` + `_budget_limit{...}` (when budgets), `_days_active`, `_last_extract_timestamp_seconds`. Label escaping (`\`, `"`, newline).

- [ ] Step 1: failing tests — required series present; escaping; empty stats safe; float formatting.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit.

### Task 7: HTTP server v1

**Files:**
- Create: `opencode_ledger/server.py` (evolve root `serve.py`)
- Test: `test_serve.py` (update imports, add route/auth/ETag tests)

**Interfaces:**
- Consumes: `Config`, `extract.run`, `metrics.render`, `budget.evaluate`.
- Produces: `make_handler(cfg: Config, out_path: str, doc_root: str, demo_stats: dict | None = None) -> Handler`; `run(cfg, open_browser=False) -> None`; routes `/`, `/stats.json`=`/api/stats`, `/api/summary`, `/api/healthz`, `/metrics`, `/api/export.csv`; ETag+304 on stats; auth (Bearer / `X-Ledger-Token` / `?token=` → cookie `ledger_token`, HttpOnly, SameSite=Lax); security headers; 500s keep server alive; demo mode serves generated stats without a DB.

- [ ] Step 1: failing tests — v0 tests updated (v2 schema, keyword `cfg`); auth required on all routes incl. `/metrics` and `/`; wrong token 401; bearer/cookie/query all work; ETag 304; summary/healthz/metrics/csv contents; security headers; missing DB → 500 JSON body; demo mode works with no DB.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass.
- [ ] Step 5: commit.

### Task 8: CLI + root shims

**Files:**
- Create: `opencode_ledger/cli.py`
- Modify: root `extract.py`, `serve.py` (thin shims), `refresh.sh` (message update), `pyproject.toml` (create with console script)
- Test: `test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`; commands `serve` (default; `[--host --port --db --token --config --demo]`), `extract [db] [out] [--config]`, `check [--json] [--no-alerts] [--config]`, `demo [--port --host --days]`, `config [--json]`, `version`. `python3 serve.py 9000` and `python3 extract.py [db] [out]` still work.

- [ ] Step 1: failing tests — dispatch of each command; `version` prints 1.0.0; `config` shows sources; bare `python3 serve.py --help` exit 0; shims import.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement `pyproject.toml` (setuptools, package-data static+pricing.json, `requires-python >=3.10`, console script `opencode-ledger = opencode_ledger.cli:main`).
- [ ] Step 4: run → pass; `python3 -m venv /tmp/ledger-venv && pip install -e .` then `opencode-ledger version`.
- [ ] Step 5: commit.

### Task 9: UI v2 (single-file)

**Files:**
- Replace: `opencode_ledger/static/index.html`; root `index.html` becomes a copy-free shim? No — root `index.html` must remain the file served by root `serve.py`? Shims point doc_root at package static. Keep root `index.html` as a redirect-free duplicate? Decide: root `serve.py` sets doc_root to package static dir; delete root `index.html` and root `stats.json` from git (stats.json gitignored, still on disk for the user). Root `index.html` removed (docs updated). Demo assets unaffected.
- Test: `test_ui.py` (contract checks + `node --check` on extracted script)

**Interfaces:**
- Consumes: schema-2 stats.
- Produces: UI with settings drawer (theme auto/light/dark, motion, poll on/off + interval, range chips Today/7d/30d/90d/All, export CSV/JSON buttons, token field for 401), sections 01–09 (split, budgets, models+est cost, burn rate, heatmap, projects, agents, sessions), keyboard/AT accessible tables, zero external requests, empty/zero/error states.

- [ ] Step 1: failing contract tests — page contains ids/settings hooks; `node --check` passes on inline script (skip cleanly if node missing); no `http://`/`https://` external refs.
- [ ] Step 2: run → fail.
- [ ] Step 3: implement.
- [ ] Step 4: run → pass; serve demo and spot-check with `curl` (200, ids present).
- [ ] Step 5: commit.

### Task 10: Docker + Compose

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`
- Test: manual `docker build` + `docker run --rm -p 0:8765` demo healthcheck (skip gracefully if daemon unavailable)

- [ ] Step 1: write Dockerfile (`python:3.12-slim`, non-root `ledger` uid 1000, pip install package from source, `ENV OPENCODE_LEDGER_HOST=0.0.0.0`, HEALTHCHECK via stdlib urllib one-liner hitting `/api/healthz`, EXPOSE 8765, default CMD `opencode-ledger serve`).
- [ ] Step 2: compose file: `ledger` service, port 8765, `~/.local/share/opencode:/data/opencode:ro`, `OPENCODE_LEDGER_DB=/data/opencode/opencode.db`, restart unless-stopped, healthcheck.
- [ ] Step 3: build + run demo, verify `/api/healthz` returns ok; commit.

### Task 11: systemd deploy

**Files:**
- Create: `deploy/opencode-ledger.service`, `deploy/install-systemd.sh`, `deploy/README.md`
- Test: shell syntax check (`bash -n`), unit file lint via `systemd-analyze verify` if present; no install.

- [ ] Step 1: user unit with hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`, `ReadWritePaths` for state dir, `Restart=on-failure`, `WantedBy=default.target`), env file support.
- [ ] Step 2: installer script (checks python, pipx/venv path arg, creates config dir + env sample, enables+starts service, `bash -n` clean).
- [ ] Step 3: commit.

### Task 12: Repo docs + CI + license

**Files:**
- Rewrite: `README.md` (badges, pitch, features, quickstarts local/docker/systemd/pipx, config table, API table, alerts, security, development, license, star history? no)
- Create: `LICENSE` (MIT, Nick / 0x-Shadow, 2026), `CONTRIBUTING.md`, `CHANGELOG.md` (1.0.0), `SECURITY.md`, `docs/self-hosting.md`, `docs/configuration.md`, `docs/api.md`, `Makefile`, `.github/workflows/ci.yml`, `.github/dependabot.yml`
- Modify: `.gitignore` (stats.json, ledger_state.json, ledger.json, venvs, dist, __pycache__), scrub absolute path from old plan doc
- Test: `python3 -m unittest` full suite; yaml sanity (python yaml not available → `node -e` no; use `python3 -c "import tomllib"` for pyproject; CI yaml reviewed by eye + `gh workflow` not possible pre-push → accept)

- [ ] Step 1: docs + license + changelog + contributing + security.
- [ ] Step 2: CI: test matrix 3.10–3.13 + ruff + docker build job; dependabot for pip + actions.
- [ ] Step 3: `git rm --cached stats.json`; gitignore; Makefile targets `test lint serve demo docker`.
- [ ] Step 4: full suite + wheel build sanity in venv; commit.

### Task 13: Final verification + GitHub upload

- [ ] Step 1: fresh-clone simulation: `git clone . /tmp/ledger-clone && cd /tmp/ledger-clone && python3 -m unittest` + `python3 serve.py --demo` + curl checks.
- [ ] Step 2: `docker build` + demo run (if daemon).
- [ ] Step 3: `gh repo create 0x-Shadow/opencode-ledger --public --source . --push` (description + topics), tag `v1.0.0`, `gh release create`.
- [ ] Step 4: verify workflow file counts as valid YAML via `python3 -c "import json,sys"`? No — install nothing; use `gh api` to confirm repo, then report evidence.

## Self-Review Notes

- Spec coverage: every numbered feature (1–18) maps to Tasks 1–12; upload/demo to 13.
- Type consistency: `Config.budgets`/`alerts` dict names reused in Tasks 5–8; `budget_status` shape defined once in Task 5 and consumed by 6/7/9.
- Review Focus lines each have a named test owner (Tasks 6/9, 8, 5, 3, 9).
