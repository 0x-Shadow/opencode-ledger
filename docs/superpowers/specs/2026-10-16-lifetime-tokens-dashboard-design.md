# OpenCode Lifetime Token Dashboard — Design Spec

Date: 2026-10-16
Status: Approved (user delegated approval: "proceed without asking")

## Intent

A personal website that counts and celebrates the user's total lifetime AI token
usage across every model used through OpenCode. Success = opening one page gives
an immediate, wow-inducing answer to "how many tokens have I burned through?"
plus enough breakdowns to be genuinely informative.

## Constraints

- Fully local and private: message content never leaves the database; only
  aggregates are rendered.
- No network dependencies at view time (works offline).
- Refresh = one command (re-run extractor, reload page).
- Read-only access to `~/.local/share/opencode/opencode.db`, opened with
  `file:...?immutable=1` to avoid clashing with a running OpenCode instance.

## Data source

`session_message` rows with `type='assistant'`; each row's `data` JSON contains:

- `model` `{id, providerID, variant?}`
- `tokens` `{input, output, reasoning, cache: {read, write}}`
- `cost` (float, USD; 0 for free-tier models)
- `time.created` (ms epoch)
- `agent` (e.g. `build`, `general`, `explore`)

`session_v2` supplies session context: `title`, `directory`, `project_id`, model,
per-session token totals. `project` maps project ids to worktree paths.

## Architecture

```
extract.py  →  reads opencode.db (read-only)  →  stats.json  →  index.html (static, fetches stats.json)
```

Two components only:

1. **`extract.py`** (Python 3, stdlib only: sqlite3, json, datetime, pathlib).
   Emits `stats.json` next to `index.html`. Aggregations:
   - `totals`: input, output, reasoning, cache_read, cache_write, all, cost, messages, sessions
   - `by_model[]`: {provider, model, variant?, messages, tokens, cost} sorted desc
   - `by_provider[]`: {provider, messages, tokens, cost}
   - `by_agent[]`: {agent, messages, tokens}
   - `timeline[]`: {date: YYYY-MM-DD, tokens, messages} for a daily chart;
     plus `monthly[]` {month: YYYY-MM, tokens, messages}
   - `sessions[]` top 15 by tokens: {title, project, model, tokens, messages, created}
   - `first_message` / `last_message` ISO timestamps; `days_active` (distinct dates)
   - `best_day` {date, tokens}
   - Generated ISO timestamp for display ("data as of")

2. **`index.html`** (single file, vanilla JS + inline CSS, no build step, no
   external requests). Renders:
   - Hero: animated count-up odometer of total lifetime tokens, with
     equivalences line ("≈ N copies of the entire internet's Wikipedia text" style
     fun facts, computed from tokens×~4 chars).
   - Stat cards: messages, sessions, days active, total cost, best day.
   - Token type split (input/output/reasoning/cache read/cache write) as a
     horizontal stacked bar + legend with counts and percentages.
   - Model leaderboard table: provider, model, messages, tokens (bar), share %.
   - Provider donut or share bar.
   - Daily tokens timeline (SVG area/bars, last 90 active days) and monthly bars.
   - Top sessions list.
   - Footer: "data source: local opencode.db · refresh with ./refresh.sh".
   - Dark "mission control" aesthetic; responsive; `prefers-reduced-motion`
     respected; accessible (semantic headings, table markup, aria labels,
     sufficient contrast).

3. **`refresh.sh`**: runs `python3 extract.py`, prints output path + totals.

## Error handling

- extract.py: fails loudly if DB missing; skips malformed message rows with a
  counted warning; never writes to the DB.
- index.html: if `stats.json` missing/unfetchable (e.g. opened via `file://`
  where fetch may be blocked), show clear instruction to run `./refresh.sh` and
  serve via `python3 -m http.server` (refresh.sh prints the URL).

## Testing

- extract.py correctness: totals from stats.json cross-checked against direct
  SQL `SUM()` over `session_v2` (tolerance for messages lacking tokens);
  spot-check one model's sum by hand.
- UI: view via local HTTP server; verify charts render with real data, count-up
  animation completes at the right number, layout at ~375px and desktop width.

## Out of scope (YAGNI)

- Live server / auto-refresh, export features, multi-user, editing/deleting
  data, historical imports from non-OpenCode tools.
