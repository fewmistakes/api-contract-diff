import copy
import unittest

from test_contracts import contract, operation, report


def response(schema):
    return {"description": "OK", "content": {"application/json": {"schema": schema}}}


class ResponseTests(unittest.TestCase):
    def test_explicit_response_can_refine_default(self):
        a = contract()
        operation(a)["responses"] = {"default": response({"type": "number"})}
        b = copy.deepcopy(a)
        operation(b)["responses"]["200"] = response({"type": "integer"})
        self.assertEqual(report(a, b).exit_code, 0)
        self.assertEqual(report(b, a).exit_code, 1)

    def test_range_takes_precedence_over_default(self):
        a = contract()
        operation(a)["responses"] = {
            "default": response({"type": "string"}),
            "2XX": response({"type": "integer"}),
        }
        b = copy.deepcopy(a)
        operation(b)["responses"]["200"] = response({"type": "string"})
        self.assertEqual(report(a, b).exit_code, 1)

    def test_explicit_response_takes_precedence_over_range(self):
        a = contract()
        operation(a)["responses"] = {
            "2XX": response({"type": "string"}),
            "200": response({"type": "integer"}),
        }
        b = copy.deepcopy(a)
        operation(b)["responses"]["2XX"] = response({"type": "integer"})
        result = report(a, b)
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(all("/200/" not in item.location for item in result.findings))

    def test_new_default_adds_unhandled_statuses_once(self):
        a, b = contract(), contract()
        operation(b)["responses"]["default"] = {"description": "Error"}
        result = report(a, b)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(sum(item.code == "response.added" for item in result.findings), 1)

    def test_redundant_explicit_response_has_no_changes(self):
        a = contract()
        operation(a)["responses"] = {"default": response({"type": "integer"})}
        b = copy.deepcopy(a)
        operation(b)["responses"]["200"] = copy.deepcopy(operation(b)["responses"]["default"])
        self.assertEqual(report(a, b).findings, ())

    def test_header_type_uses_response_direction(self):
        a, b = contract(), contract()
        operation(a)["responses"]["200"]["headers"] = {"X-Count": {"schema": {"type": "integer"}}}
        operation(b)["responses"]["200"]["headers"] = {"x-count": {"schema": {"type": "number"}}}
        self.assertEqual(report(a, b).exit_code, 1)
        self.assertEqual(report(b, a).exit_code, 0)

    def test_header_removal_is_conservative(self):
        a, b = contract(), contract()
        operation(a)["responses"]["200"]["headers"] = {"X-Count": {"schema": {"type": "integer"}}}
        self.assertEqual(report(a, b).exit_code, 1)
        self.assertEqual(report(b, a).exit_code, 0)

    def test_media_type_without_schema_means_unconstrained(self):
        a, b = contract({"type": "string"}), contract()
        operation(b)["responses"]["200"]["content"] = {"application/json": {}}
        self.assertEqual(report(a, b).exit_code, 1)
        self.assertEqual(report(b, a).exit_code, 0)
