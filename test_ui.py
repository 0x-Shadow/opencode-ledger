"""Contract tests for the single-file UI (opencode_ledger/static/index.html)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "opencode_ledger", "static", "index.html")

REQUIRED_IDS = [
    "odometer", "odometer-sr", "stats", "stack", "legend", "models-table",
    "providers-table", "daily-chart", "monthly-chart", "daily-table",
    "monthly-table", "heatmap", "heatmap-table", "sessions", "budgets",
    "projects-table", "agents-table", "settings-dialog", "live-badge",
    "range-chips", "panels", "generated", "equivalence",
]


class UiContractTests(unittest.TestCase):
    def setUp(self):
        with open(UI_PATH) as f:
            self.html = f.read()

    def test_required_hooks_present(self):
        for hook in REQUIRED_IDS:
            self.assertIn(f'id="{hook}"', self.html, f"missing id {hook}")

    def test_no_external_requests(self):
        self.assertNotRegex(self.html, r'(?:src|href)\s*=\s*"https?://')
        self.assertNotIn("@import", self.html)

    def test_basic_document_hygiene(self):
        self.assertIn('lang="en"', self.html)
        self.assertIn('name="viewport"', self.html)
        self.assertIn('class="skip-link"', self.html)
        self.assertIn('rel="icon"', self.html)

    def test_settings_features_wired(self):
        for needle in ("ledger_token", "ledger_theme", "ledger_range",
                       "ledger_poll", "settings-dialog", "prefers-reduced-motion",
                       "/api/export.csv", "download"):
            self.assertIn(needle, self.html, f"missing {needle}")

    def test_schema2_fields_consumed(self):
        for needle in ("budget_status", "daily_models", "by_project",
                       "heatmap", "est_cost", "duration_min"):
            self.assertIn(needle, self.html, f"missing {needle}")

    def test_inline_script_is_valid_js(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertTrue(scripts, "no inline script found")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write("\n".join(scripts))
            path = f.name
        try:
            result = subprocess.run([node, "--check", path],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0,
                             f"JS syntax error: {result.stderr[:800]}")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
