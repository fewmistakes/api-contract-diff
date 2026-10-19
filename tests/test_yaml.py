import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_contracts import contract

from contract_diff import compare, load
from contract_diff.loader import SpecError, _float, parse
from contract_diff.yaml_support import decode

SIMPLE = """openapi: 3.0.3
info: {title: Test, version: '1'}
paths:
  /users:
    get:
      responses:
        '200':
          description: OK
"""


class YamlTests(unittest.TestCase):
    def test_missing_optional_dependency_has_an_actionable_error(self):
        with patch.dict("sys.modules", {"yaml": None}):
            with self.assertRaisesRegex(ValueError, "requires PyYAML"):
                decode(SIMPLE, _float)

    def test_load_yaml_matches_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api.yaml"
            path.write_text(SIMPLE, encoding="utf-8")
            self.assertEqual(compare(load(path), parse(contract())).findings, ())

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate YAML key"):
            decode("a: 1\na: 2\n", _float)

    def test_aliases_are_rejected_before_expansion(self):
        for text in ("a: &x [1]\nb: *x", "a: &x [*x]", "a: &x {foo: 1}\nb: {<<: *x}"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode(text, _float)

    def test_implicit_scalar_rules_preserve_on_yes_and_dates(self):
        self.assertEqual(
            decode("on: yes\ndate: 2026-10-19\nflag: true\n", _float),
            {"on": "yes", "date": "2026-10-19", "flag": True},
        )

    def test_unsafe_or_non_json_tags_are_rejected(self):
        for text in (
            "!!python/object/apply:os.system ['echo bad']",
            "!!set {a: null}",
            "!!timestamp 2026-10-19",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode(text, _float)

    def test_numeric_mapping_keys_require_quotes(self):
        with self.assertRaisesRegex(ValueError, "mapping keys must be strings"):
            decode("200: OK", _float)

    def test_precision_is_preserved_by_rejecting_loss(self):
        with self.assertRaisesRegex(ValueError, "precision loss"):
            decode("x: 1.0000000000000001", _float)

    def test_multiple_documents_and_deep_yaml_are_rejected(self):
        for text in ("a: 1\n---\nb: 2", "[" * 100 + "]" * 100):
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode(text, _float)

    def test_yaml_failure_is_wrapped_as_spec_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text("a: [", encoding="utf-8")
            with self.assertRaises(SpecError):
                load(path)
