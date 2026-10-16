# Lifetime Token Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local static website that visualizes the user's total lifetime AI token usage across all models used through OpenCode.

**Architecture:** `extract.py` reads OpenCode's SQLite DB read-only and emits an aggregate-only `stats.json`; a single-file static `index.html` fetches that JSON and renders an animated dark "mission control" dashboard. `refresh.sh` reruns extraction and prints a serve URL.

**Tech Stack:** Python 3 stdlib (sqlite3, json, urllib); vanilla HTML/CSS/JS, SVG charts, no dependencies, no network at view time.

**Spec:** `docs/superpowers/specs/2026-10-16-lifetime-tokens-dashboard-design.md`

## Global Constraints

- DB path: `~/.local/share/opencode/opencode.db`, opened read-only via URI `file:<path>?immutable=1`.
- Python: stdlib only; no third-party packages.
- UI: single `index.html`, inline CSS/JS, zero external requests; dark theme; responsive; respects `prefers-reduced-motion`; semantic headings + table markup.
- Only aggregates in `stats.json` — never message text/content.
- Project root: `/home/nick/Documents/projects/opencode-lifetime/`.
- Git repo initialized at project root; commit after each task.

## Review Focus

- **Malformed/partial assistant rows** (missing `tokens` or `model` keys): extractor must skip and count them, not crash — tested in Task 1.
- **DB locked by a running OpenCode instance**: immutable=1 URI must prevent `sqlite3.OperationalError` — verified in Task 1 by running extraction while OpenCode is live (it is, always).
- **Totals mismatch vs SQL SUM()**: cross-check test in Task 1 compares extractor totals to direct SQL aggregation.
- **`fetch()` of stats.json over `file://` blocked**: Task 3 must show a friendly error state with instructions, not a blank page — tested by opening via file:// (manual step).
- **Huge numbers breaking layout / odometer**: number formatting uses compact (e.g. `1.2B`) for large figures with exact value in `title`/aria — checked in Task 3.
- **Zero-data / empty stats.json**: UI renders an empty-state message — Task 3 step.

---

### Task 1: Extractor (`extract.py`)

**Files:**
- Create: `extract.py`
- Test: `test_extract.py` (same directory, stdlib `unittest`)

**Interfaces:**
- Produces: `stats.json` in project root with schema:
  ```json
  {
    "generated": "ISO-8601 UTC",
    "db_path": "…", "db_mtime": "ISO-8601",
    "totals": {"input": int, "output": int, "reasoning": int, "cache_read": int, "cache_write": int, "all": int, "cost": float, "messages": int, "sessions": int},
    "by_model": [{"provider": str, "model": str, "variant": str|null, "messages": int, "tokens": int, "cost": float}],
    "by_provider": [{"provider": str, "messages": int, "tokens": int, "cost": float}],
    "by_agent": [{"agent": str, "messages": int, "tokens": int}],
    "timeline": [{"date": "YYYY-MM-DD", "tokens": int, "messages": int}],
    "monthly": [{"month": "YYYY-MM", "tokens": int, "messages": int}],
    "sessions_top": [{"title": str, "project": str, "model": str, "tokens": int, "messages": int, "created": "ISO-8601"}],
    "first_message": "ISO-8601"|null, "last_message": "ISO-8601"|null,
    "days_active": int, "best_day": {"date": str, "tokens": int}|null,
    "skipped_rows": int
  }
  ```
- Functions (importable for tests): `aggregate(rows: list[dict], session_meta: dict[str, dict]) -> dict` (pure; rows are parsed assistant-message dicts), `parse_message(data: str) -> dict|None`, `main(db_path: str, out_path: str) -> None`.

- [ ] **Step 1: Write failing tests**

`test_extract.py` with unittest cases:
- `test_parse_message_valid`: minimal valid JSON returns dict with model/tokens/time/agent.
- `test_parse_message_missing_tokens`: JSON without `tokens` → None.
- `test_parse_message_malformed`: `"not json{"` → None.
- `test_aggregate_totals`: 3 synthetic rows (two models, one with cache tokens) → assert exact `totals.all == input+output+reasoning+cache_read+cache_write` summed, `messages == 3`, `by_model` length 2 sorted desc by tokens, `by_provider` aggregated, `timeline` daily grouping, `monthly` grouping, `best_day` correct.
- `test_aggregate_empty`: empty rows → totals zeros, `first_message is None`, `best_day is None`, no crash.
- `test_extract_real_db` (integration): `main()` on the real DB into a temp file; assert `totals.all > 0`, `totals.all == sum of by_model tokens`, `skipped_rows >= 0`, `days_active >= 1`, and `totals.all` within 1% of SQL `SUM(tokens_input+tokens_output+tokens_reasoning+tokens_cache_read+tokens_cache_write)` over `session_message` joined JSON… (SQL cross-check done via a second connection reading the same table with `json_extract` — SQLite JSON1 is available; if json1 missing, fall back to comparing against `session_v2` SUM with 5% tolerance and print both numbers).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nick/Documents/projects/opencode-lifetime && python3 -m unittest test_extract -v`
Expected: FAIL/ERROR (ImportError — extract.py doesn't exist).

- [ ] **Step 3: Implement `extract.py`**

Stdlib only. Open DB with `sqlite3.connect(f"file:{path}?immutable=1", uri=True)`. Select `id, session_id, data` from `session_message` where `type='assistant'`. Parse each `data` with `parse_message`; skip+count malformed. Session titles/project from `session_v2` joined `project` (map session_id → {title, directory}). Timestamps: ms epoch → local ISO via `datetime.fromtimestamp(ms/1000)`. `sessions_top`: aggregate per session_id, take top 15 by tokens. Write `stats.json` (indent=1, sort_keys=False). CLI: `python3 extract.py [db_path] [out_path]` defaulting to spec paths; on success print totals summary.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_extract -v`
Expected: all PASS (integration test prints cross-check numbers).

- [ ] **Step 5: Commit**

```bash
git add extract.py test_extract.py && git commit -m "feat: add token usage extractor with tests"
```

---

### Task 2: Refresh script + generated stats

**Files:**
- Create: `refresh.sh` (chmod +x)

**Interfaces:**
- Consumes: `extract.py` CLI from Task 1.
- Produces: `stats.json` in project root.

- [ ] **Step 1: Write `refresh.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 extract.py
echo "Open: python3 -m http.server 8765  →  http://localhost:8765/"
```

- [ ] **Step 2: Run it and inspect stats.json**

Run: `./refresh.sh`
Expected: prints totals; `python3 -c "import json;d=json.load(open('stats.json'));print(d['totals'])"` shows nonzero numbers.

- [ ] **Step 3: Commit**

```bash
git add refresh.sh stats.json && git commit -m "feat: add refresh script, generate initial stats"
```

---

### Task 3: Dashboard (`index.html`)

**Files:**
- Create: `index.html`

**Interfaces:**
- Consumes: `stats.json` schema from Task 1 (fetch relative `./stats.json`).

- [ ] **Step 1: Build the page structure + styling**

Single file. Sections: header ("OpenCode Lifetime" + generated timestamp); hero odometer (`#odometer`, count-up over ~1.6s, cubic ease-out, skipped when `prefers-reduced-motion`); equivalences line under odometer (tokens×4 chars → "≈ N× the entire Harry Potter series" using fixed multipliers, e.g. series ≈ 1.08M tokens — computed in JS, purely display copy); stat cards row (messages, sessions, days active, cost `$x.xx`, best day); token-type stacked bar with legend; model leaderboard `<table>` (provider/model/messages/tokens/share% with inline bar); provider share bar; SVG daily timeline (last 90 days present, bars, tooltips via `<title>`); monthly bars; top-15 sessions list; footer with refresh hint. Dark palette, CSS custom properties, max-width ~1100px, responsive grid collapsing to 1 column ≤640px.

- [ ] **Step 2: Add JS rendering**

`fetch('./stats.json')` then render; `fmt(n)` → compact (`1.23B`) with `title` = exact locale string; guard: `!res.ok` or fetch throw → show error panel with `./refresh.sh` + `python3 -m http.server` instructions; `totals.all === 0` → empty-state panel. Escape all text from JSON via a small `esc()` (titles/projects are external data).

- [ ] **Step 3: Verify in browser manually + serve check**

Run: `python3 -m http.server 8765` in project root; confirm with curl `curl -s localhost:8765/stats.json | head -c 200` returns JSON. (Browser visual check performed by user/agent with screenshot tooling if available; otherwise curl checks for both files + JS syntax check via `node --check` on extracted script if node exists.)

- [ ] **Step 4: Commit**

```bash
git add index.html && git commit -m "feat: add lifetime token dashboard UI"
```

---

### Task 4: Final verification & README

**Files:**
- Create: `README.md`

- [ ] **Step 1: End-to-end check**

Run: `./refresh.sh && curl -s localhost:8765/ | grep -c "odometer"` (server assumed running; else start briefly). Verify totals in stats.json match `sqlite3` SUM query printed by extract.py.

- [ ] **Step 2: README**

One paragraph + usage: `./refresh.sh`, `python3 -m http.server 8765`, open URL. Note privacy stance.

- [ ] **Step 3: Commit**

```bash
git add README.md && git commit -m "docs: add README"
```
