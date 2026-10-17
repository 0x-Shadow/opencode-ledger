# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.x     | ✅        |

## Threat model

opencode-ledger is a local-first tool. It reads your OpenCode SQLite database
read-only, aggregates it, and serves one HTML page plus a small JSON API on
`127.0.0.1:8765` by default.

- Only **aggregates** are ever emitted — token counts, costs, session titles,
  model/provider/agent/project names. **Message content is never read out or
  served.**
- Nothing leaves the machine unless *you* configure
  `OPENCODE_LEDGER_WEBHOOK_URL` — and then only budget alert summaries are
  POSTed to that URL.
- The database is opened with `mode=ro`; the server cannot modify it.
- The regenerated aggregate file (`stats.json`; path configurable via the
  `output` setting) is written with `0600` permissions. It contains session
  titles and project names — treat it as sensitive data.
- The UI makes zero external requests (no CDNs, fonts or analytics).
- The `X-Forwarded-Proto` header only decides whether the `ledger_token`
  cookie is marked `Secure`; it can never disable or downgrade
  authentication. A direct client can set it, which affects only that
  client's own cookie, so trust in the header is delegated to network
  topology: keep the default loopback bind — or firewall the port — so only
  your reverse proxy can reach the server.
- Webhook destinations (`alerts.webhook_url`) come from your own local
  configuration — file, environment or CLI flags — never from an HTTP
  request. Any `http(s)` destination is allowed by design, including
  self-hosted services on your LAN; delivery uses a 10-second timeout and is
  best-effort (a broken webhook never affects serving).

Because the dashboard exposes your usage (and session titles) over HTTP:

- **Keep the default bind `127.0.0.1`** unless you set a token.
- **Set `OPENCODE_LEDGER_TOKEN`** before exposing it on any network, including
  a LAN. All routes then require the token via `Authorization: Bearer`,
  `X-Ledger-Token`, `?token=` or the `ledger_token` cookie.
- Put a TLS-terminating reverse proxy in front for anything beyond localhost.
- Token comparison is constant-time (`hmac.compare_digest`).

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting:

**Security → Report a vulnerability** on
<https://github.com/0x-Shadow/opencode-ledger/security/advisories>

or email 0x-Shadow@users.noreply.github.com. You can expect an
acknowledgement within a few days. Please do not open a public issue for
security problems.
