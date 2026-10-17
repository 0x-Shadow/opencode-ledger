# Contributing

Thanks for looking at opencode-ledger! It is deliberately small, so
contributions are easy to review.

## Ground rules

These are load-bearing; pull requests that break them will be asked to change:

1. **Zero runtime dependencies.** Stdlib only, Python 3.10+. Dev tools (ruff)
   are fine as extras.
2. **The database is read-only.** Always open `opencode.db` with
   `mode=ro` and a busy timeout. Never write to the OpenCode data directory.
3. **Aggregates only.** Never emit message content — counts, sums, timestamps,
   model/provider/agent/project names are fine.
4. **The UI is a single file** (`opencode_ledger/static/index.html`) with zero
   external requests: no CDNs, no fonts, no analytics.

## Development setup

```bash
git clone https://github.com/0x-Shadow/opencode-ledger
cd opencode-ledger
make demo                       # live demo at http://localhost:8765/
make smoke                      # boot the demo server, check every route
```

Optional tools:

```bash
pip install ruff                # lint
make lint
```

## Verifying your change

- Run `make smoke` — it boots the demo server and checks every route answers
  `200`. It must pass before you open a PR.
- Also exercise the change by hand in `make demo` mode (that is what the
  endpoint smoke test cannot see: rendering, interactions, edge cases).
- For extractor changes, compare against your real database with
  `opencode-ledger extract` and eyeball totals in the dashboard.

## Style

- `ruff check .` must pass (`pyproject.toml` holds the config).
- Match the surrounding code: stdlib idioms, small functions, no cleverness.
- Docstrings for modules and public functions; comments explain *why*.

## Pull requests

1. One topic per PR; small diffs review faster.
2. Describe *what changed and why*, plus how you verified it (exact commands).
3. Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
4. Screenshots are welcome for UI changes (before/after).
5. CI must pass: ruff, the endpoint smoke test, and a Docker build.

## Reporting bugs

Open a GitHub issue with the output of `opencode-ledger config --json`,
your Python version, and steps to reproduce. Redact your token and, if you
care, your project paths.

Security issues: see [SECURITY.md](SECURITY.md).
