"""Configuration loading for OpenCode Token Ledger.

Layered, in increasing precedence:

    defaults  <  config file  <  environment  <  command line

The config file is JSON. It is discovered at (first hit wins):

    1. ``--config`` / ``OPENCODE_LEDGER_CONFIG``   (explicit: must exist)
    2. ``./ledger.json``                           (auto: optional)
    3. ``$XDG_CONFIG_HOME/opencode-ledger/config.json``  (auto: optional)
    4. ``~/.config/opencode-ledger/config.json``   (auto: optional)

Only aggregates-safe values live here; nothing is ever written back.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field

CONFIG_ENV = "OPENCODE_LEDGER_CONFIG"
ENV_PREFIX = "OPENCODE_LEDGER_"

DEFAULTS = {
    "db_path": "~/.local/share/opencode/opencode.db",
    "host": "127.0.0.1",
    "port": 8765,
    "token": None,
    "throttle_seconds": 3.0,
    "poll_seconds": 4,
    "stale_seconds": 30,
    "budgets": {
        "daily_tokens": None,
        "monthly_tokens": None,
        "daily_cost_usd": None,
        "monthly_cost_usd": None,
    },
    "alerts": {
        "thresholds": [0.8, 1.0],
        "webhook_url": None,
        "webhook_format": "generic",
    },
    "pricing": {},
    "pricing_file": None,
    "state_file": "~/.local/state/opencode-ledger/state.json",
}

# env var -> dotted config path
ENV_MAP = {
    "DB": "db_path",
    "HOST": "host",
    "PORT": "port",
    "TOKEN": "token",
    "THROTTLE": "throttle_seconds",
    "POLL": "poll_seconds",
    "STALE": "stale_seconds",
    "STATE_FILE": "state_file",
    "PRICING_FILE": "pricing_file",
    "BUDGET_DAILY_TOKENS": "budgets.daily_tokens",
    "BUDGET_MONTHLY_TOKENS": "budgets.monthly_tokens",
    "BUDGET_DAILY_COST": "budgets.daily_cost_usd",
    "BUDGET_MONTHLY_COST": "budgets.monthly_cost_usd",
    "WEBHOOK_URL": "alerts.webhook_url",
    "WEBHOOK_FORMAT": "alerts.webhook_format",
    "ALERT_THRESHOLDS": "alerts.thresholds",
}

WEBHOOK_FORMATS = ("generic", "discord", "slack", "ntfy")

_INT_KEYS = {"port", "poll_seconds", "stale_seconds"}
_FLOAT_KEYS = {"throttle_seconds"}
_PATH_KEYS = {"db_path", "state_file", "pricing_file", "config_file"}


class ConfigError(ValueError):
    """Invalid configuration; message names the offending key and value."""


@dataclass
class Config:
    db_path: str
    host: str
    port: int
    token: str | None
    throttle_seconds: float
    poll_seconds: int
    stale_seconds: int
    budgets: dict
    alerts: dict
    pricing: dict
    pricing_file: str | None
    state_file: str
    config_file: str | None = None
    sources: dict = field(default_factory=dict)


def _get(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(d: dict, dotted: str, value):
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _coerce(dotted: str, value, source: str):
    """Coerce a raw string (env) or JSON value to the config type."""
    key = dotted.split(".")[-1]
    try:
        if value is None:
            return None
        if key in _INT_KEYS:
            if isinstance(value, bool):
                raise ValueError
            return int(str(value).strip())
        if key in _FLOAT_KEYS:
            return float(str(value).strip())
        if key.startswith("budget_") or key in ("daily_tokens", "monthly_tokens",
                                                "daily_cost_usd", "monthly_cost_usd"):
            return float(str(value).strip())
        if key == "thresholds":
            if isinstance(value, str):
                value = [p for p in (s.strip() for s in value.split(",")) if p]
            if not isinstance(value, list) or not value:
                raise ValueError
            return [float(str(v).strip()) for v in value]
        if key == "token":
            value = str(value)
            return value if value else None
    except (TypeError, ValueError):
        raise ConfigError(f"invalid value for {source} {dotted!r}: {value!r}")
    return value


def _find_config_file(cli: dict, env: dict, cwd: str):
    home = env.get("HOME") or os.path.expanduser("~")
    explicit = cli.get("config_file") or env.get(CONFIG_ENV)
    if explicit:
        path = _expand(explicit, home)
        if not os.path.isfile(path):
            raise ConfigError(f"config file not found: {path}")
        return path
    candidates = [os.path.join(cwd, "ledger.json")]
    xdg = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    candidates.append(os.path.join(xdg, "opencode-ledger", "config.json"))
    candidates.append(os.path.join(home, ".config", "opencode-ledger", "config.json"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _deep_merge(base: dict, overlay: dict, source: str, sources: dict,
                prefix: str = ""):
    for key, value in overlay.items():
        dotted = f"{prefix}{key}"
        if dotted in ("budgets", "alerts", "pricing"):
            if not isinstance(value, dict):
                raise ConfigError(f"invalid value for {source} {dotted!r}: expected object")
            _deep_merge(base[key], value, source, sources, prefix=f"{dotted}.")
            continue
        if key not in base and not prefix.startswith("pricing."):
            where = f"{prefix[:-1]} " if prefix else ""
            raise ConfigError(f"unknown config key {key!r} in {where}{source}")
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _deep_merge(base[key], value, source, sources, prefix=f"{dotted}.")
            continue
        base[key] = value
        sources[dotted] = source


def _expand(path: str, home: str) -> str:
    """Expand a leading ~ against the passed environment's home directory."""
    if path == "~":
        return home
    if path.startswith("~/"):
        return os.path.join(home, path[2:])
    return path


def _validate(cfg: Config, sources: dict, home: str):
    def fail(key, why):
        raise ConfigError(f"invalid {key!r} ({sources.get(key, 'cli')}): {why}")

    if not isinstance(cfg.host, str) or not cfg.host.strip():
        fail("host", "must be a non-empty string")
    if not (0 <= cfg.port <= 65535):
        fail("port", "must be 0..65535 (0 = any free port)")
    if cfg.throttle_seconds < 0:
        fail("throttle_seconds", "must be >= 0")
    if cfg.poll_seconds < 1:
        fail("poll_seconds", "must be >= 1")
    if cfg.stale_seconds < 0:
        fail("stale_seconds", "must be >= 0")
    if cfg.token is not None and not str(cfg.token).strip():
        fail("token", "must be non-empty when set")
    for bkey, bval in cfg.budgets.items():
        if bval is not None and (not isinstance(bval, (int, float)) or bval <= 0):
            fail(f"budgets.{bkey}", "must be a positive number or null")
    fmt = cfg.alerts.get("webhook_format", "generic")
    if fmt not in WEBHOOK_FORMATS:
        fail("alerts.webhook_format", f"must be one of {', '.join(WEBHOOK_FORMATS)}")
    thresholds = cfg.alerts.get("thresholds", [])
    if (not isinstance(thresholds, list) or not thresholds
            or any(not isinstance(t, (int, float)) or t <= 0 for t in thresholds)):
        fail("alerts.thresholds", "must be a non-empty list of positive numbers")
    cfg.alerts["thresholds"] = sorted(float(t) for t in thresholds)
    url = cfg.alerts.get("webhook_url")
    if url is not None and not (isinstance(url, str)
                                and url.startswith(("http://", "https://"))):
        fail("alerts.webhook_url", "must be an http(s) URL or null")
    if not isinstance(cfg.pricing, dict):
        fail("pricing", "must be an object of model -> price")
    for pkey, pval in cfg.pricing.items():
        if not isinstance(pval, dict) or not all(
                isinstance(v, (int, float)) and v >= 0 for v in pval.values()):
            fail(f"pricing.{pkey}", "must be an object of non-negative numbers")
    for path_key in ("db_path", "state_file"):
        val = getattr(cfg, path_key)
        if not isinstance(val, str) or not val.strip():
            fail(path_key, "must be a non-empty path")
        setattr(cfg, path_key, _expand(val, home))
    if cfg.pricing_file:
        cfg.pricing_file = _expand(cfg.pricing_file, home)


def load(cli: dict | None = None, env: dict | None = None,
         cwd: str | None = None) -> Config:
    """Load effective config from defaults/file/env/cli (low→high precedence)."""
    cli = {k: v for k, v in (cli or {}).items() if v is not None}
    env = dict(os.environ if env is None else env)
    cwd = cwd or os.getcwd()

    merged = copy.deepcopy(DEFAULTS)
    sources = {k: "default" for k in
               ("db_path", "host", "port", "token", "throttle_seconds",
                "poll_seconds", "stale_seconds", "budgets.daily_tokens",
                "budgets.monthly_tokens", "budgets.daily_cost_usd",
                "budgets.monthly_cost_usd", "alerts.thresholds",
                "alerts.webhook_url", "alerts.webhook_format", "pricing",
                "pricing_file", "state_file")}

    config_file = _find_config_file(cli, env, cwd)
    if config_file:
        try:
            with open(config_file) as f:
                file_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ConfigError(f"cannot read config file {config_file}: {e}")
        if not isinstance(file_data, dict):
            raise ConfigError(f"config file {config_file} must contain a JSON object")
        _deep_merge(merged, file_data, f"file:{config_file}", sources)

    for env_key, dotted in ENV_MAP.items():
        raw = env.get(ENV_PREFIX + env_key)
        if raw is None:
            continue
        value = _coerce(dotted, raw, f"env:{ENV_PREFIX + env_key}")
        if "." in dotted:
            parent, leaf = dotted.split(".", 1)
            merged[parent][leaf] = value
        else:
            merged[dotted] = value
        sources[dotted] = f"env:{ENV_PREFIX + env_key}"

    for key, value in cli.items():
        if key == "config_file":
            continue
        if key in ("budgets", "alerts") and isinstance(value, dict):
            for leaf, leaf_value in value.items():
                if leaf_value is None:
                    continue
                merged[key][leaf] = leaf_value
                sources[f"{key}.{leaf}"] = "cli"
            continue
        if key in ("pricing",) and isinstance(value, dict):
            merged["pricing"] = value
            sources["pricing"] = "cli"
            continue
        value = _coerce(key, value, "cli")
        if value is None:
            continue
        merged[key] = value
        sources[key] = "cli"

    cfg = Config(
        db_path=merged["db_path"],
        host=merged["host"],
        port=merged["port"],
        token=merged["token"],
        throttle_seconds=merged["throttle_seconds"],
        poll_seconds=merged["poll_seconds"],
        stale_seconds=merged["stale_seconds"],
        budgets=merged["budgets"],
        alerts=merged["alerts"],
        pricing=merged["pricing"],
        pricing_file=merged["pricing_file"],
        state_file=merged["state_file"],
        config_file=config_file,
        sources=sources,
    )
    _validate(cfg, sources, home=(env.get("HOME") or os.path.expanduser("~")))
    return cfg


def describe(cfg: Config) -> list[dict]:
    """Effective settings as rows: {key, value, source}. Secrets are masked."""
    rows = []
    for key in ("db_path", "host", "port", "token", "throttle_seconds",
                "poll_seconds", "stale_seconds", "state_file", "pricing_file",
                "config_file"):
        value = getattr(cfg, key)
        if key == "token" and value is not None:
            value = "***"
        rows.append({"key": key, "value": value,
                     "source": cfg.sources.get(key, "default")})
    for key, val in cfg.budgets.items():
        rows.append({"key": f"budgets.{key}", "value": val,
                     "source": cfg.sources.get(f"budgets.{key}", "default")})
    for key, val in cfg.alerts.items():
        rows.append({"key": f"alerts.{key}", "value": val,
                     "source": cfg.sources.get(f"alerts.{key}", "default")})
    rows.append({"key": "pricing", "value": f"{len(cfg.pricing)} override(s)",
                 "source": cfg.sources.get("pricing", "default")})
    return rows
