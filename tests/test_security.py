import copy
import unittest

from test_contracts import contract, operation, report

from contract_diff.loader import SpecError, parse


def secured(requirements):
    doc = contract()
    doc["components"] = {
        "securitySchemes": {
            "key": {"type": "apiKey", "in": "header", "name": "X-Key"},
            "bearer": {"type": "http", "scheme": "bearer"},
            "oauth": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": "https://example.test/token",
                        "scopes": {"read": "Read", "write": "Write"},
                    }
                },
            },
        }
    }
    doc["security"] = requirements
    return doc


class SecurityTests(unittest.TestCase):
    def test_oauth_flow_requires_its_endpoint(self):
        doc = secured([{"oauth": []}])
        del doc["components"]["securitySchemes"]["oauth"]["flows"]["clientCredentials"]["tokenUrl"]
        with self.assertRaisesRegex(SpecError, "OAuth endpoint URL"):
            parse(doc)

    def test_adding_authentication_breaks_public_clients(self):
        self.assertEqual(report(secured([]), secured([{"key": []}])).exit_code, 1)

    def test_removing_authentication_is_compatible(self):
        self.assertEqual(report(secured([{"key": []}]), secured([])).exit_code, 0)

    def test_or_alternative_can_be_added_but_not_removed(self):
        a, b = secured([{"key": []}]), secured([{"key": []}, {"bearer": []}])
        self.assertEqual(report(a, b).exit_code, 0)
        self.assertEqual(report(b, a).exit_code, 1)

    def test_and_requirement_restricts_credentials(self):
        a, b = secured([{"key": []}]), secured([{"key": [], "bearer": []}])
        self.assertEqual(report(a, b).exit_code, 1)
        self.assertEqual(report(b, a).exit_code, 0)

    def test_scope_addition_is_breaking(self):
        a, b = secured([{"oauth": ["read"]}]), secured([{"oauth": ["read", "write"]}])
        self.assertEqual(report(a, b).exit_code, 1)
        self.assertEqual(report(b, a).exit_code, 0)

    def test_scope_order_and_duplicates_do_not_matter(self):
        a = secured([{"oauth": ["read", "write"]}])
        b = secured([{"oauth": ["write", "read", "read"]}])
        self.assertEqual(report(a, b).findings, ())

    def test_empty_alternative_means_public(self):
        self.assertEqual(report(secured([]), secured([{}, {"key": []}])).findings, ())

    def test_redundant_alternative_does_not_narrow_security(self):
        a, b = secured([{"key": []}]), secured([{"key": []}, {"key": [], "bearer": []}])
        self.assertEqual(report(a, b).findings, ())

    def test_scheme_rename_is_not_a_wire_change(self):
        a = secured([{"key": []}])
        b = copy.deepcopy(a)
        b["components"]["securitySchemes"]["renamed"] = b["components"]["securitySchemes"].pop(
            "key"
        )
        b["security"] = [{"renamed": []}]
        self.assertEqual(report(a, b).findings, ())

    def test_header_names_are_case_insensitive(self):
        a = secured([{"key": []}])
        b = copy.deepcopy(a)
        b["components"]["securitySchemes"]["key"]["name"] = "x-key"
        self.assertEqual(report(a, b).findings, ())

    def test_changed_issuer_requires_review(self):
        a = secured([{"oauth": ["read"]}])
        b = copy.deepcopy(a)
        b["components"]["securitySchemes"]["oauth"]["flows"]["clientCredentials"]["tokenUrl"] = (
            "https://example.test/new-token"
        )
        self.assertEqual(report(a, b).exit_code, 3)

    def test_scope_descriptions_do_not_affect_credentials(self):
        a = secured([{"oauth": ["read"]}])
        b = copy.deepcopy(a)
        b["components"]["securitySchemes"]["oauth"]["flows"]["clientCredentials"]["scopes"][
            "read"
        ] = "Updated docs"
        self.assertEqual(report(a, b).findings, ())

    def test_operation_override_is_respected(self):
        a, b = secured([{"key": []}]), secured([{"bearer": []}])
        operation(a)["security"] = operation(b)["security"] = []
        self.assertEqual(report(a, b).findings, ())

    def test_invalid_requirements_are_rejected(self):
        for requirements in (
            {},
            [{"missing": []}],
            [{"key": ["read"]}],
            [{"oauth": ["undefined"]}],
            [{"key": "read"}],
        ):
            with self.subTest(requirements=requirements), self.assertRaises(SpecError):
                parse(secured(requirements))
