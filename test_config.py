"""Tests for opencode_ledger.config — layered configuration loading."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_ledger import config


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)
    return path


class DefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_CONFIG_HOME": os.path.join(self.tmp.name, ".config")}

    def load(self, cli=None, env=None, cwd=None):
        return config.load(cli=cli, env=self.env if env is None else env,
                           cwd=cwd or self.tmp.name)

    def test_defaults(self):
        cfg = self.load()
        self.assertEqual(cfg.port, 8765)
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertIsNone(cfg.token)
        self.assertEqual(cfg.budgets["daily_tokens"], None)
        self.assertEqual(cfg.alerts["thresholds"], [0.8, 1.0])
        self.assertEqual(cfg.throttle_seconds, 3.0)
        self.assertNotIn("~", cfg.db_path)
        self.assertNotIn("~", cfg.state_file)

    def test_missing_auto_config_is_fine(self):
        cfg = self.load()
        self.assertIsNone(cfg.config_file)

    def test_explicit_missing_config_is_an_error(self):
        with self.assertRaises(config.ConfigError):
            self.load(cli={"config_file": os.path.join(self.tmp.name, "nope.json")})

    def test_env_explicit_missing_config_is_an_error(self):
        env = dict(self.env, OPENCODE_LEDGER_CONFIG=os.path.join(self.tmp.name, "nope.json"))
        with self.assertRaises(config.ConfigError):
            self.load(env=env)


class PrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_CONFIG_HOME": os.path.join(self.tmp.name, ".config")}

    def test_file_merges_over_defaults(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"), {
            "port": 9000,
            "budgets": {"daily_tokens": 100},
            "alerts": {"webhook_url": "http://hook.example/x"},
            "pricing": {"opencode/test-model": {"input": 1.0, "output": 2.0}},
        })
        cfg = config.load(env=self.env, cwd=self.tmp.name)
        self.assertEqual(cfg.port, 9000)
        self.assertEqual(cfg.budgets["daily_tokens"], 100)
        self.assertIsNone(cfg.budgets["monthly_tokens"])  # untouched key keeps default
        self.assertEqual(cfg.alerts["webhook_url"], "http://hook.example/x")
        self.assertEqual(cfg.alerts["thresholds"], [0.8, 1.0])
        self.assertEqual(cfg.pricing["opencode/test-model"]["input"], 1.0)

    def test_env_overrides_file(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"), {"port": 9000})
        cfg = config.load(env=dict(self.env, OPENCODE_LEDGER_PORT="9100"),
                          cwd=self.tmp.name)
        self.assertEqual(cfg.port, 9100)
        self.assertEqual(cfg.sources["port"], "env:OPENCODE_LEDGER_PORT")

    def test_cli_overrides_env(self):
        cfg = config.load(env=dict(self.env, OPENCODE_LEDGER_PORT="9100"),
                          cwd=self.tmp.name, cli={"port": 9200})
        self.assertEqual(cfg.port, 9200)
        self.assertEqual(cfg.sources["port"], "cli")

    def test_nested_budget_env(self):
        env = dict(self.env, OPENCODE_LEDGER_BUDGET_DAILY_TOKENS="500",
                   OPENCODE_LEDGER_BUDGET_MONTHLY_COST="12.5")
        cfg = config.load(env=env, cwd=self.tmp.name)
        self.assertEqual(cfg.budgets["daily_tokens"], 500)
        self.assertEqual(cfg.budgets["monthly_cost_usd"], 12.5)

    def test_thresholds_env_csv(self):
        env = dict(self.env, OPENCODE_LEDGER_ALERT_THRESHOLDS="0.5, 0.9")
        cfg = config.load(env=env, cwd=self.tmp.name)
        self.assertEqual(cfg.alerts["thresholds"], [0.5, 0.9])

    def test_xdg_config_found_when_cwd_has_none(self):
        d = os.path.join(self.tmp.name, ".config", "opencode-ledger")
        os.makedirs(d)
        write_json(os.path.join(d, "config.json"), {"port": 9300})
        cfg = config.load(env=self.env, cwd=self.tmp.name)
        self.assertEqual(cfg.port, 9300)
        self.assertEqual(cfg.config_file, os.path.join(d, "config.json"))

    def test_expanduser_applied(self):
        cfg = config.load(env=dict(self.env, OPENCODE_LEDGER_DB="~/fun.db"),
                          cwd=self.tmp.name)
        self.assertEqual(cfg.db_path, os.path.join(self.tmp.name, "fun.db"))


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_CONFIG_HOME": os.path.join(self.tmp.name, ".config")}

    def test_unknown_top_level_key_rejected(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"), {"nope": 1})
        with self.assertRaises(config.ConfigError):
            config.load(env=self.env, cwd=self.tmp.name)

    def test_unknown_nested_key_rejected(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"),
                   {"budgets": {"weekly": 1}})
        with self.assertRaises(config.ConfigError):
            config.load(env=self.env, cwd=self.tmp.name)

    def test_invalid_port_rejected(self):
        for bad in ("abc", "70000"):
            with self.assertRaises(config.ConfigError):
                config.load(env=dict(self.env, OPENCODE_LEDGER_PORT=bad),
                            cwd=self.tmp.name)

    def test_port_zero_allowed_for_ephemeral_bind(self):
        cfg = config.load(env=dict(self.env, OPENCODE_LEDGER_PORT="0"),
                          cwd=self.tmp.name)
        self.assertEqual(cfg.port, 0)

    def test_invalid_webhook_format_rejected(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"),
                   {"alerts": {"webhook_format": "teams"}})
        with self.assertRaises(config.ConfigError):
            config.load(env=self.env, cwd=self.tmp.name)

    def test_invalid_thresholds_rejected(self):
        for bad in ("", "0.5,-1", "abc"):
            with self.assertRaises(config.ConfigError):
                config.load(env=dict(self.env, OPENCODE_LEDGER_ALERT_THRESHOLDS=bad),
                            cwd=self.tmp.name)

    def test_negative_budget_rejected(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"),
                   {"budgets": {"daily_tokens": -5}})
        with self.assertRaises(config.ConfigError):
            config.load(env=self.env, cwd=self.tmp.name)

    def test_malformed_file_rejected(self):
        with open(os.path.join(self.tmp.name, "ledger.json"), "w") as f:
            f.write("{not json")
        with self.assertRaises(config.ConfigError):
            config.load(env=self.env, cwd=self.tmp.name)


class DescribeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name, "XDG_CONFIG_HOME": os.path.join(self.tmp.name, ".config")}

    def test_describe_rows_have_key_value_source(self):
        write_json(os.path.join(self.tmp.name, "ledger.json"), {"port": 9000})
        cfg = config.load(env=self.env, cwd=self.tmp.name)
        rows = config.describe(cfg)
        by_key = {r["key"]: r for r in rows}
        self.assertEqual(by_key["port"]["value"], 9000)
        self.assertTrue(by_key["port"]["source"].startswith("file:"))
        self.assertEqual(by_key["host"]["source"], "default")

    def test_describe_redacts_token(self):
        cfg = config.load(env=dict(self.env, OPENCODE_LEDGER_TOKEN="sekrit"),
                          cwd=self.tmp.name)
        rows = {r["key"]: r for r in config.describe(cfg)}
        self.assertNotIn("sekrit", json.dumps(rows))
        self.assertEqual(rows["token"]["value"], "***")


if __name__ == "__main__":
    unittest.main()
