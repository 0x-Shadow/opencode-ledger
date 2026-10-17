# Self-hosting guide

Three supported ways to run opencode-ledger: directly, in Docker, or as a
systemd user service. All of them read the OpenCode database read-only and
write only their own small state file.

## 1. Direct (simplest)

```bash
pipx install git+https://github.com/0x-Shadow/opencode-ledger
opencode-ledger serve            # http://127.0.0.1:8765/
```

Keep it running with your supervisor of choice. To expose it beyond localhost:

```bash
OPENCODE_LEDGER_TOKEN=$(openssl rand -hex 24) opencode-ledger serve --host 0.0.0.0
```

…and put TLS in front (see [Reverse proxy](#reverse-proxy)).

## 2. Docker Compose

```bash
git clone https://github.com/0x-Shadow/opencode-ledger && cd opencode-ledger
docker compose up -d --build
docker compose logs -f
```

Design notes:

- Runs as **non-root uid 1000** (`ledger`).
- Mounts `~/.local/share/opencode` **read-only** at `/data/opencode`.
- State lives in the named volume `ledger-state` (exports/dedup only).
- Healthcheck hits `/api/healthz`; `restart: unless-stopped`.

Customize with environment variables in `docker-compose.yml` (or a
`docker-compose.override.yml`):

```yaml
environment:
  OPENCODE_LEDGER_TOKEN: change-me
  OPENCODE_LEDGER_BUDGET_DAILY_COST: "5"
  OPENCODE_LEDGER_WEBHOOK_URL: https://ntfy.sh/my-ledger
  OPENCODE_LEDGER_WEBHOOK_FORMAT: ntfy
```

A different data directory:

```bash
OPENCODE_DATA_DIR=/srv/opencode docker compose up -d
```

**uid mismatch:** the OpenCode database is usually `chmod 600` owned by your
user. If your uid is not 1000, build with your uid
(`docker build --build-arg` is not wired yet — simplest is to keep the host
user at 1000 or run the container with `user: "$(id -u):$(id -g)"` and set
`OPENCODE_LEDGER_STATE_FILE=/tmp/ledger-state.json` so state stays writable).

## 3. systemd user service

```bash
deploy/install-systemd.sh              # unit + ~/.config/opencode-ledger/env sample
systemctl --user status opencode-ledger
journalctl --user -u opencode-ledger -f
loginctl enable-linger "$USER"         # start at boot without login
```

The unit is hardened: `ProtectSystem=strict`, `ProtectHome=read-only`,
`PrivateTmp`, `NoNewPrivileges`, and write access only to
`~/.local/state/opencode-ledger`. See [deploy/README.md](../deploy/README.md).

Configuration goes in `~/.config/opencode-ledger/env` (systemd does not expand
`$HOME` there; `~` works, or use absolute paths).

## Reverse proxy

The server speaks plain HTTP. Two common patterns:

**Caddy** — terminate TLS and keep the token at the proxy:

```caddyfile
ledger.example.com {
    reverse_proxy 127.0.0.1:8765
    # optional shared-secret header instead of the app token:
    header_up X-Ledger-Token {env.LEDGER_TOKEN}
}
```

**nginx** — with basic auth in front:

```nginx
location / {
    auth_basic "ledger";
    auth_basic_user_file /etc/nginx/ledger.htpasswd;
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
}
```

Either way: **set a token or put auth in front before exposing the dashboard.**
It reveals your usage metadata (models, projects, session titles).

Hardening checklist for anything beyond localhost:

- [ ] **TLS at the proxy** (Caddy `tls` / nginx `listen 443 ssl`). The app
      itself speaks plain HTTP only — never expose port 8765 directly.
- [ ] **Keep the app bound to `127.0.0.1`** and let the proxy reach it, or
      firewall the port so only the proxy can connect.
- [ ] **Auth required**: a token (or proxy auth) on every route — `/metrics`
      included; scrape it over localhost or with credentials.
- [ ] **Prefer the `Authorization` header over `?token=`** in URLs: query
      strings leak into proxy logs and browser history.
- [ ] **Webhook URLs must be `https://`** (the config validator enforces
      `http(s)`); payloads carry token totals and budget ratios.
- [ ] **Rotate the token** if it ever appears in a screenshot, log or shell
      history.

## Prometheus

```yaml
scrape_configs:
  - job_name: opencode-ledger
    static_configs:
      - targets: ["ledger-host:8765"]
    authorization:
      credentials: your-token
```

Handy alerts: `opencode_ledger_budget_used_ratio > 0.9`,
`opencode_ledger_last_extract_timestamp_seconds` going stale, and
`opencode_ledger_tokens_today` spikes.

## Operations

- **Backups:** nothing to back up. `stats.json` is regenerable; the state file
  only prevents duplicate alerts. Your OpenCode database is the source of truth.
- **Updating:** `git pull`, then restart (rebuild + `docker compose up -d
  --build`, or `systemctl --user restart opencode-ledger`).
- **Cost accuracy:** check `pricing.json` in the repo for your models and add
  overrides per [configuration.md](configuration.md).
- **Troubleshooting:** `opencode-ledger config` shows every effective setting and
  where it came from; `opencode-ledger check` prints budget status without
  serving; `/api/healthz` reports extraction errors.
