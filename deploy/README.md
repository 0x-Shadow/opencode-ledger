# Deploying opencode-ledger

| Path | Purpose |
| --- | --- |
| `opencode-ledger.service` | systemd **user** unit (hardened, runs as you) |
| `install-systemd.sh` | installs the unit, an env-file sample, and starts it |

## systemd (user unit)

```bash
# install the CLI first, e.g.
pipx install /path/to/opencode-ledger

# then install the service
deploy/install-systemd.sh                # or: --bin ~/.venvs/ledger/bin/opencode-ledger
deploy/install-systemd.sh --no-start     # install + enable, start later
```

The installer writes:

- `~/.config/systemd/user/opencode-ledger.service` (ExecStart pinned to your binary)
- `${XDG_CONFIG_HOME:-~/.config}/opencode-ledger/env` — commented sample; uncomment to
  set a token, budgets, webhook alerts. Systemd does **not** expand `$HOME` here;
  `~` works because opencode-ledger expands it, or use absolute paths.
- `~/.local/state/opencode-ledger/` — the writable state directory (budget alert dedup).

Manage it like any user service:

```bash
systemctl --user status opencode-ledger
journalctl --user -u opencode-ledger -f
systemctl --user restart opencode-ledger
```

To keep it running when you are not logged in (recommended on a home server):

```bash
loginctl enable-linger "$USER"
```

## Manual install

```bash
mkdir -p ~/.config/systemd/user ~/.local/state/opencode-ledger
sed "s|@EXEC@|$HOME/.local/bin/opencode-ledger|" deploy/opencode-ledger.service \
  > ~/.config/systemd/user/opencode-ledger.service
systemctl --user daemon-reload
systemctl --user enable --now opencode-ledger
```

## Hardening notes

The unit runs without privileges and with `ProtectSystem=strict`,
`ProtectHome=read-only`, `PrivateTmp`, `NoNewPrivileges` and a minimal
`ReadWritePaths` pointing only at the state directory. The OpenCode SQLite
database is opened read-only and never written.

## Uninstall

```bash
systemctl --user disable --now opencode-ledger
rm ~/.config/systemd/user/opencode-ledger.service
rm -r ~/.config/opencode-ledger ~/.local/state/opencode-ledger
```
