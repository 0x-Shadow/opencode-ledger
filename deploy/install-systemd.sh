#!/usr/bin/env bash
# Install opencode-ledger as a systemd user service.
#
# Usage: deploy/install-systemd.sh [--bin /path/to/opencode-ledger] [--no-start]
#
# The unit runs as your user, reads the OpenCode database read-only, serves on
# 127.0.0.1:8765 and keeps only its small state file writable. Configuration is
# read from ${XDG_CONFIG_HOME:-~/.config}/opencode-ledger/env.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy/install-systemd.sh [--bin PATH] [--no-start]

  --bin PATH   opencode-ledger executable to run (default: first found in PATH,
               then ~/.local/bin/opencode-ledger)
  --no-start   install and enable, but do not start the service yet
EOF
}

BIN=""
NO_START=0
while [ $# -gt 0 ]; do
  case "$1" in
    --bin)
      [ $# -ge 2 ] || { echo "error: --bin needs a path" >&2; exit 2; }
      BIN=$2; shift 2 ;;
    --bin=*) BIN=${1#--bin=}; shift ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
UNIT_SRC="$SCRIPT_DIR/opencode-ledger.service"
[ -f "$UNIT_SRC" ] || { echo "error: $UNIT_SRC not found" >&2; exit 1; }

if [ -z "$BIN" ]; then
  if command -v opencode-ledger >/dev/null 2>&1; then
    BIN=$(command -v opencode-ledger)
  elif [ -x "$HOME/.local/bin/opencode-ledger" ]; then
    BIN="$HOME/.local/bin/opencode-ledger"
  else
    echo "error: opencode-ledger not found — install it first" >&2
    echo "       pipx install .   (from the repo)  or  pip install --user ." >&2
    exit 1
  fi
fi
[ -x "$BIN" ] || { echo "error: not executable: $BIN" >&2; exit 1; }

echo "Using: $("$BIN" version 2>/dev/null || "$BIN" --version 2>/dev/null || echo "$BIN")"

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode-ledger"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/opencode-ledger"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$UNIT_DIR"

if [ ! -f "$CONFIG_DIR/env" ]; then
  cat > "$CONFIG_DIR/env" <<'EOF'
# opencode-ledger service environment — uncomment and edit as needed.
# Paths may use ~ (expanded by opencode-ledger). Defaults already read
# ~/.local/share/opencode/opencode.db, so no DB line is needed at all.
# OPENCODE_LEDGER_DB=~/.local/share/opencode/opencode.db
# OPENCODE_LEDGER_HOST=127.0.0.1
# OPENCODE_LEDGER_PORT=8765
# Recommended on any shared machine (clients must then send the token):
# OPENCODE_LEDGER_TOKEN=change-me
# Budgets and deduplicated webhook alerts:
# OPENCODE_LEDGER_BUDGET_DAILY_COST=5
# OPENCODE_LEDGER_BUDGET_MONTHLY_TOKENS=50000000
# OPENCODE_LEDGER_WEBHOOK_URL=https://ntfy.sh/your-topic
# OPENCODE_LEDGER_WEBHOOK_FORMAT=ntfy
EOF
  echo "Wrote config sample: $CONFIG_DIR/env"
else
  echo "Kept existing config:  $CONFIG_DIR/env"
fi

sed "s|@EXEC@|$BIN|g" "$UNIT_SRC" > "$UNIT_DIR/opencode-ledger.service"
echo "Installed unit:       $UNIT_DIR/opencode-ledger.service"

if [ "$NO_START" -eq 1 ]; then
  echo
  echo "Enable and start it with:"
  echo "  systemctl --user daemon-reload"
  echo "  systemctl --user enable --now opencode-ledger"
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user daemon-reload 2>/dev/null; then
  echo
  echo "systemctl --user not usable here. Files are installed; on a real system run:"
  echo "  systemctl --user daemon-reload"
  echo "  systemctl --user enable --now opencode-ledger"
  exit 0
fi

systemctl --user enable --now opencode-ledger
echo
echo "Status:  systemctl --user status opencode-ledger"
echo "Logs:    journalctl --user -u opencode-ledger -f"
echo "Open:    http://localhost:8765/"
echo
echo "Tip: run 'loginctl enable-linger $USER' so the dashboard also runs when"
echo "you are not logged in (e.g. after a reboot)."
