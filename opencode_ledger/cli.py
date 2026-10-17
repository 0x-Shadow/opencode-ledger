"""Command line interface for OpenCode Token Ledger.

Commands: serve (default), extract, check, demo, config, version.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, budget, config, extract

VERSION_TEXT = f"opencode-ledger {__version__}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode-ledger",
        description="Self-hosted token usage analytics for OpenCode.",
        epilog="Config precedence: CLI flags > OPENCODE_LEDGER_* env vars > "
               "ledger.json / ~/.config/opencode-ledger/config.json > defaults.",
    )
    parser.add_argument("--version", action="version", version=VERSION_TEXT)
    sub = parser.add_subparsers(dest="command")

    def add_common(p, db=False, token=True):
        p.add_argument("--config", metavar="PATH",
                       help="config file (JSON; must exist)")
        if db:
            p.add_argument("--db", metavar="PATH", help="opencode.db path")
        if token:
            p.add_argument("--token", metavar="TOKEN",
                           help="require this token on every request")

    serve = sub.add_parser("serve", help="run the dashboard server (default)")
    serve.add_argument("port", nargs="?", type=int, metavar="PORT",
                       help="port (shorthand for --port)")
    serve.add_argument("--host", metavar="HOST", help="bind address")
    serve.add_argument("--port", dest="port_opt", type=int, metavar="PORT")
    serve.add_argument("--demo", action="store_true",
                       help="serve synthetic demo data, no database")
    add_common(serve, db=True)

    ext = sub.add_parser("extract", help="extract stats.json once")
    ext.add_argument("db", nargs="?", metavar="DB")
    ext.add_argument("out", nargs="?", metavar="OUT")
    add_common(ext, db=False, token=False)

    chk = sub.add_parser("check", help="evaluate budgets; exit 1 if exceeded")
    chk.add_argument("--json", action="store_true", help="machine-readable output")
    chk.add_argument("--no-alerts", action="store_true",
                     help="do not send webhook alerts")
    add_common(chk, db=True)

    demo = sub.add_parser("demo", help="serve synthetic demo data (no database)")
    demo.add_argument("--host", metavar="HOST")
    demo.add_argument("--port", dest="port_opt", type=int, metavar="PORT")
    demo.add_argument("--config", metavar="PATH")

    cfgp = sub.add_parser("config", help="show effective configuration")
    cfgp.add_argument("--json", action="store_true")
    cfgp.add_argument("--config", metavar="PATH")
    cfgp.add_argument("--db", metavar="PATH")
    cfgp.add_argument("--token", metavar="TOKEN")

    sub.add_parser("version", help="print the version")

    return parser


def _cli_overrides(args) -> dict:
    overrides = {}
    for key in ("config", "db", "host", "token"):
        value = getattr(args, key, None)
        if value is not None:
            overrides["config_file" if key == "config" else
                      "db_path" if key == "db" else key] = value
    port = getattr(args, "port_opt", None)
    if port is None:
        port = getattr(args, "port", None)
    if port is not None:
        overrides["port"] = port
    return overrides


def _load_config(args) -> config.Config:
    return config.load(cli=_cli_overrides(args))


def cmd_serve(args) -> int:
    cfg = _load_config(args)
    if not args.demo and not os.path.exists(cfg.db_path):
        print(f"error: database not found: {cfg.db_path}\n"
              f"hint: pass --db PATH, or try --demo mode "
              f"(opencode-ledger demo)", file=sys.stderr)
        return 2
    from . import server
    server.run(cfg, demo=args.demo)
    return 0


def cmd_extract(args) -> int:
    cfg = _load_config(args)
    db_path = getattr(args, "db", None) or cfg.db_path
    out_path = getattr(args, "out", None) or os.path.abspath(
        os.path.expanduser(cfg.output))
    try:
        extract.main(db_path, out_path)
    except SystemExit as e:  # extract.main exits on a missing database
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_check(args) -> int:
    cfg = _load_config(args)
    result = budget.check(cfg, send=not args.no_alerts)
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        if not result["status"]:
            print("no budgets configured — nothing to check")
        for row in result["status"]:
            pct = row["ratio"] * 100
            unit = "USD" if row["unit"] == "usd" else "tokens"
            used = f"{row['used']:,.0f}" if row["unit"] == "tokens" else f"{row['used']:.2f}"
            limit = f"{row['limit']:,.0f}" if row["unit"] == "tokens" else f"{row['limit']:.2f}"
            print(f"{row['id']}: {used} / {limit} {unit} ({pct:.1f}%)")
        if result["sent"]:
            print(f"alerts sent: {len(result['sent'])}")
    return result["exit_code"]


def cmd_demo(args) -> int:
    cfg = _load_config(args)
    from . import server
    server.run(cfg, demo=True)
    return 0


def cmd_config(args) -> int:
    cfg = _load_config(args)
    rows = config.describe(cfg)
    if args.json:
        print(json.dumps({
            "config_file": cfg.config_file,
            "version": __version__,
            "settings": rows,
        }, indent=1, default=str))
    else:
        print(f"# opencode-ledger {__version__}")
        print(f"# config file: {cfg.config_file or '(none — using defaults)'}")
        width = max(len(r["key"]) for r in rows)
        for row in rows:
            print(f"{row['key']:<{width}}  {str(row['value']):<28}  [{row['source']}]")
    return 0


def cmd_version(args) -> int:
    print(VERSION_TEXT)
    return 0


COMMANDS = {
    "serve": cmd_serve,
    "extract": cmd_extract,
    "check": cmd_check,
    "demo": cmd_demo,
    "config": cmd_config,
    "version": cmd_version,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        if argv[:1] not in (["-h"], ["--help"], ["-V"], ["--version"]):
            argv.insert(0, "serve")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:   # bare -h/--help/-V/--version
        parser.print_help()
        return 0
    try:
        return COMMANDS[args.command](args)
    except (config.ConfigError, extract.DatabaseIncompatible,
            FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
