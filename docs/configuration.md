# Configuration reference

opencode-ledger reads its configuration from four layers, later layers
overriding earlier ones:

1. **Defaults** (table below)
2. **Config file** — `ledger.json` in the working directory, or
   `~/.config/opencode-ledger/config.json`, or an explicit
   `--config PATH` / `OPENCODE_LEDGER_CONFIG`
3. **Environment** — every `OPENCODE_LEDGER_*` variable
4. **CLI flags** — `--db`, `--host`, `--port`, `--token`, `--config`

`opencode-ledger config` prints the effective result with sources;
`opencode-ledger config --json` is machine-readable.

## Settings

| Key | Env var | Default | Description |
| --- | --- | --- | --- |
| `db_path` | `OPENCODE_LEDGER_DB` | `~/.local/share/opencode/opencode.db` | SQLite database, opened `mode=ro`. `~` is expanded. |
| `host` | `OPENCODE_LEDGER_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only with a token + firewall/proxy. |
| `port` | `OPENCODE_LEDGER_PORT` | `8765` | Bind port. `0` = ephemeral (printed on startup). |
| `token` | `OPENCODE_LEDGER_TOKEN` | — | When set, required on **every** route. |
| `throttle_seconds` | `OPENCODE_LEDGER_THROTTLE` | `3.0` | Minimum seconds between database re-extracts. |
| `poll_seconds` | `OPENCODE_LEDGER_POLL` | `4` | UI poll cadence (browsers can override per-user). |
| `stale_seconds` | `OPENCODE_LEDGER_STALE` | `30` | While stats are younger than this and the DB mtime is unchanged, refresh is skipped entirely. |
| `state_file` | `OPENCODE_LEDGER_STATE_FILE` | `~/.local/state/opencode-ledger/state.json` | Alert dedup state; only file written. |
| `budgets.daily_tokens` | `..._BUDGET_DAILY_TOKENS` | — | Integer token limit per local day. |
| `budgets.monthly_tokens` | `..._BUDGET_MONTHLY_TOKENS` | — | Integer token limit per calendar month. |
| `budgets.daily_cost_usd` | `..._BUDGET_DAILY_COST` | — | USD (float) per local day. |
| `budgets.monthly_cost_usd` | `..._BUDGET_MONTHLY_COST` | — | USD (float) per calendar month. |
| `alerts.thresholds` | `..._ALERT_THRESHOLDS` | `[0.8, 1.0]` | Fractions of a budget that fire an alert. |
| `alerts.webhook_url` | `..._WEBHOOK_URL` | — | POST target for alerts. Unset = no outbound traffic. |
| `alerts.webhook_format` | `..._WEBHOOK_FORMAT` | `generic` | `generic`, `discord`, `slack`, `ntfy`. |
| `pricing` | — | `{}` | Per-model price overrides, USD per **million** tokens. |
| `pricing_file` | `..._PRICING_FILE` | — | JSON file with the same shape as `pricing`. |

## Config file example

```json
{
  "db_path": "~/.local/share/opencode/opencode.db",
  "port": 8765,
  "budgets": {
    "daily_tokens": 20000000,
    "monthly_cost_usd": 40
  },
  "alerts": {
    "thresholds": [0.8, 1.0],
    "webhook_url": "https://discord.com/api/webhooks/...",
    "webhook_format": "discord"
  },
  "pricing": {
    "my-custom-model": {"input": 0.4, "output": 1.6, "cache_read": 0.04, "cache_write": 0.5}
  }
}
```

Validation errors name the offending key and its source
(`file:ledger.json`, `env:OPENCODE_LEDGER_PORT`, `cli`).

## Cost estimation

`pricing.json` ships list prices for common models. Tokens that match no price
are counted as `unpriced_tokens` and never guessed in the totals — the UI marks
costs with `≈` and the API reports `priced` per model. Override any model via
`pricing` / `pricing_file`; keys are the model id as stored in the database.

## Alerts behaviour

For each configured budget, thresholds are evaluated per period
(`day`/`month`). Each `(budget, period-start, threshold)` combination alerts
**once**, recorded in `state_file`; restarts don't re-deliver. Delivery is
best-effort (10 s timeout, errors swallowed) and never blocks the dashboard.
`opencode-ledger check` evaluates once for cron/systemd timers and exits 1 when
any budget is at or over 100%.
