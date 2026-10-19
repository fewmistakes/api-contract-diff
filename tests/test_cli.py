import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contract_diff.cli import render_text
from contract_diff.model import Finding, Report

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        return subprocess.run(
            [sys.executable, "-m", "contract_diff", *map(str, args)],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_breaking_example_matches_report(self):
        process = self.run_cli(
            EXAMPLES / "old.openapi.json", EXAMPLES / "new.openapi.json", "--format", "json"
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["summary"], {"breaking": 2, "compatible": 1, "unknown": 0})
        self.assertEqual(result["report_version"], 1)
        self.assertEqual(process.stderr, "")

    def test_compatible_example_passes(self):
        process = self.run_cli(EXAMPLES / "old.openapi.json", EXAMPLES / "compatible.openapi.json")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("COMPATIBLE: 0 breaking, 1 compatible, 0 unknown.", process.stdout)

    def test_unknown_example_exits_three(self):
        process = self.run_cli(EXAMPLES / "old.openapi.json", EXAMPLES / "unsupported.openapi.json")
        self.assertEqual(process.returncode, 3, process.stderr)
        self.assertIn("schema.inconclusive", process.stdout)

    def test_missing_file_returns_structured_error(self):
        process = self.run_cli(
            EXAMPLES / "not-found.json", EXAMPLES / "old.openapi.json", "--format", "json"
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["status"], "error")
        self.assertEqual(process.stderr, "")

    def test_text_error_uses_stderr_without_traceback(self):
        process = self.run_cli(EXAMPLES / "not-found.json", EXAMPLES / "old.openapi.json")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")
        self.assertIn("contract-diff:", process.stderr)
        self.assertNotIn("Traceback", process.stderr)

    def test_malformed_json_duplicate_keys_and_nonfinite_numbers(self):
        for text in ("{", '{"openapi":"3.0.3","openapi":"3.0.4"}', '{"x":NaN}', '{"x":1e999}'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid.json"
                path.write_text(text, encoding="utf-8")
                process = self.run_cli(path, EXAMPLES / "old.openapi.json", "--format", "json")
                self.assertEqual(process.returncode, 2)
                self.assertEqual(json.loads(process.stdout)["status"], "error")

    def test_decimal_enum_precision_is_not_silently_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "precision.json"
            path.write_text('{"x": 1.0000000000000001}', encoding="utf-8")
            process = self.run_cli(path, EXAMPLES / "old.openapi.json", "--format", "json")
            self.assertEqual(process.returncode, 2)
            self.assertIn("precision loss", json.loads(process.stdout)["error"])

    def test_utf8_bom_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bom.json"
            path.write_text((EXAMPLES / "old.openapi.json").read_text(), encoding="utf-8-sig")
            process = self.run_cli(path, EXAMPLES / "old.openapi.json")
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_deep_json_reports_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.json"
            path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
            process = self.run_cli(path, EXAMPLES / "old.openapi.json", "--format", "json")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(json.loads(process.stdout)["status"], "error")

    def test_no_changes_is_explicit(self):
        path = EXAMPLES / "old.openapi.json"
        process = self.run_cli(path, path)
        self.assertEqual(process.returncode, 0)
        self.assertIn("No contract changes detected within the supported scope.", process.stdout)

    def test_terminal_control_characters_are_escaped(self):
        result = render_text(
            Report(
                (
                    Finding(
                        "unknown",
                        "test",
                        "GET /\x1b[31m",
                        "#\n",
                        "line\rnext",
                    ),
                )
            )
        )
        self.assertNotIn("\x1b", result)
        self.assertNotIn("\r", result)
        self.assertIn("\\u001b", result)
        self.assertIn("#\\u000a", result)

    def test_version_and_help(self):
        self.assertEqual(self.run_cli("--version").stdout.strip(), "contract-diff 0.2.0")
        process = self.run_cli("--help")
        self.assertEqual(process.returncode, 0)
        self.assertIn("3 inconclusive", process.stdout)

    def test_json_report_is_reproducible(self):
        args = (EXAMPLES / "old.openapi.json", EXAMPLES / "new.openapi.json", "--format", "json")
        self.assertEqual(self.run_cli(*args).stdout, self.run_cli(*args).stdout)


if __name__ == "__main__":
    unittest.main()
