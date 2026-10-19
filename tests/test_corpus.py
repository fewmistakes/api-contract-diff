import copy
import hashlib
import json
import unittest
from pathlib import Path

from contract_diff import compare, load
from contract_diff.loader import SpecError, _float, parse
from contract_diff.yaml_support import decode

CORPUS = Path(__file__).parent / "corpus"


class UpstreamCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.petstore = decode(
            (CORPUS / "swagger-petstore.yaml").read_text(encoding="utf-8"), _float
        )
        cls.petstore_contract = parse(cls.petstore, label="old")

    def test_upstream_snapshots_match_recorded_hashes(self):
        for source in json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8")):
            raw = (CORPUS / source["file"]).read_bytes()
            with self.subTest(source=source["name"]):
                self.assertEqual(hashlib.sha256(raw).hexdigest(), source["sha256"])
                self.assertEqual(len(raw), source["bytes"])

    def test_unmodified_petstore_is_within_supported_scope(self):
        self.assertEqual(len(self.petstore_contract.operations), 19)
        self.assertEqual(compare(self.petstore_contract, parse(self.petstore)).findings, ())

    def test_petstore_required_parameter_mutation_is_breaking(self):
        new = copy.deepcopy(self.petstore)
        new["paths"]["/pet"]["post"]["parameters"] = [
            {
                "name": "region",
                "in": "query",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
        result = compare(self.petstore_contract, parse(new))
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(
            any(f.code == "parameter.added" and f.operation == "POST /pet" for f in result.findings)
        )

    def test_petstore_optional_parameter_mutation_is_compatible(self):
        new = copy.deepcopy(self.petstore)
        new["paths"]["/pet"]["post"]["parameters"] = [
            {
                "name": "trace",
                "in": "query",
                "schema": {"type": "boolean"},
            }
        ]
        self.assertEqual(compare(self.petstore_contract, parse(new)).exit_code, 0)

    def test_petstore_oauth_scope_mutation_is_breaking(self):
        old = copy.deepcopy(self.petstore)
        new = copy.deepcopy(self.petstore)
        old["paths"]["/pet"]["post"]["security"] = [{"petstore_auth": ["read:pets"]}]
        new["paths"]["/pet"]["post"]["security"] = [{"petstore_auth": ["read:pets", "write:pets"]}]
        result = compare(parse(old), parse(new))
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(any(f.code == "security.changed" for f in result.findings))

    def test_petstore_integer_bound_mutation_is_breaking(self):
        new = copy.deepcopy(self.petstore)
        param = new["paths"]["/pet/{petId}"]["get"]["parameters"][0]
        param["schema"]["minimum"] = 1
        result = compare(self.petstore_contract, parse(new))
        self.assertEqual(result.exit_code, 1)

    def test_full_github_spec_loads_and_reports_unsupported_composition(self):
        contract = load(CORPUS / "github.json", label="snapshot")
        self.assertEqual(len(contract.operations), 1242)
        result = compare(contract, contract)
        self.assertEqual(result.exit_code, 3)
        self.assertTrue(any("oneOf" in f.message for f in result.findings))
        self.assertTrue(any("allOf" in f.message for f in result.findings))
        self.assertEqual(result.summary["breaking"], 0)

    def test_meilisearch_31_is_explicitly_rejected(self):
        with self.assertRaisesRegex(SpecError, "expected OpenAPI 3.0.x"):
            load(CORPUS / "meilisearch.json")

    def test_github_licenses_response_requiredness_mutation(self):
        # A documented projection, not a modified fixture presented as a full API.
        old = json.loads((CORPUS / "github.json").read_text(encoding="utf-8"))
        old["paths"] = {"/licenses": old["paths"]["/licenses"]}
        before = parse(old)
        self.assertEqual(compare(before, before).exit_code, 0)
        new = copy.deepcopy(old)
        new["components"]["schemas"]["license-simple"]["required"].remove("key")
        result = compare(before, parse(new))
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(
            any("key" in f.message and f.code == "schema.incompatible" for f in result.findings)
        )

    def test_github_licenses_parameter_bound_mutation(self):
        old = json.loads((CORPUS / "github.json").read_text(encoding="utf-8"))
        old["paths"] = {"/licenses": old["paths"]["/licenses"]}
        before = parse(old)
        new = copy.deepcopy(old)
        new["components"]["parameters"]["per-page"]["schema"]["maximum"] = 50
        result = compare(before, parse(new))
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(any("per_page" in f.location for f in result.findings))
