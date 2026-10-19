import copy
import unittest
from unittest.mock import patch

from contract_diff import compare
from contract_diff.loader import SpecError, parse


def contract(schema=None, *, request=False):
    operation = {"responses": {"200": {"description": "OK"}}}
    if schema is not None:
        content = {"application/json": {"schema": schema}}
        if request:
            operation["requestBody"] = {"content": content}
        else:
            operation["responses"]["200"]["content"] = content
    return {
        "openapi": "3.0.3",
        "info": {"title": "Test", "version": "1"},
        "paths": {"/users": {"get": operation}},
    }


def operation(document):
    return document["paths"]["/users"]["get"]


def report(old, new):
    return compare(parse(old, label="old"), parse(new, label="new"))


def parameter(name="limit", **kwargs):
    return {"name": name, "in": "query", "schema": {"type": "integer"}, **kwargs}


class ContractTests(unittest.TestCase):
    def test_schema_expansion_budget_stops_shared_reference_graphs(self):
        doc = contract(
            {
                "type": "object",
                "properties": {
                    "a": {"$ref": "#/components/schemas/Name"},
                    "b": {"$ref": "#/components/schemas/Name"},
                },
            }
        )
        doc["components"] = {"schemas": {"Name": {"type": "string"}}}
        with patch("contract_diff.loader.MAX_SCHEMA_NODES", 2):
            with self.assertRaisesRegex(SpecError, "expanded schema exceeds"):
                parse(doc)

    def test_schema_depth_limit_is_inconclusive(self):
        doc = contract({"type": "array", "items": {"type": "array", "items": {}}})
        with patch("contract_diff.loader.MAX_DEPTH", 1):
            self.assertEqual(report(doc, doc).exit_code, 3)

    def test_reference_chain_limit_is_inconclusive(self):
        doc = contract({"$ref": "#/components/schemas/A"})
        doc["components"] = {
            "schemas": {
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"type": "string"},
            }
        }
        with patch("contract_diff.loader.MAX_DEPTH", 1):
            self.assertEqual(report(doc, doc).exit_code, 3)

    def test_request_body_cannot_have_empty_content(self):
        doc = contract()
        operation(doc)["requestBody"] = {"content": {}}
        with self.assertRaisesRegex(SpecError, "must not be empty"):
            parse(doc)

    def test_identical_has_no_findings(self):
        doc = contract({"type": "string"})
        self.assertEqual(report(doc, doc).findings, ())

    def test_added_and_removed_operations(self):
        old, new = contract(), contract()
        new["paths"]["/users"]["post"] = operation(contract())
        self.assertEqual(report(old, new).exit_code, 0)
        self.assertEqual(report(new, old).exit_code, 1)

    def test_new_required_parameter_breaks(self):
        old, new = contract(), contract()
        operation(new)["parameters"] = [parameter(required=True)]
        self.assertEqual(report(old, new).findings[0].code, "parameter.added")
        self.assertEqual(report(old, new).exit_code, 1)

    def test_optional_parameter_addition_is_compatible(self):
        old, new = contract(), contract()
        operation(new)["parameters"] = [parameter()]
        self.assertEqual(report(old, new).exit_code, 0)

    def test_removing_parameter_breaks(self):
        old, new = contract(), contract()
        operation(old)["parameters"] = [parameter()]
        self.assertEqual(report(old, new).exit_code, 1)

    def test_parameter_becoming_required_breaks(self):
        old, new = contract(), contract()
        operation(old)["parameters"] = [parameter()]
        operation(new)["parameters"] = [parameter(required=True)]
        self.assertEqual(report(old, new).exit_code, 1)
        self.assertEqual(report(new, old).exit_code, 0)

    def test_operation_parameters_override_path_parameters(self):
        old, new = contract(), contract()
        for doc in (old, new):
            doc["paths"]["/users"]["parameters"] = [parameter(required=True)]
            operation(doc)["parameters"] = [parameter()]
        new["paths"]["/users"]["parameters"][0]["schema"] = {"type": "string"}
        self.assertEqual(report(old, new).findings, ())

    def test_path_parameters_are_inherited(self):
        old, new = contract(), contract()
        old["paths"]["/users"]["parameters"] = [parameter()]
        new["paths"]["/users"]["parameters"] = [parameter(required=True)]
        self.assertEqual(report(old, new).exit_code, 1)

    def test_header_names_are_case_insensitive(self):
        old, new = contract(), contract()
        operation(old)["parameters"] = [parameter("X-Key", **{"in": "header"})]
        operation(new)["parameters"] = [parameter("x-key", **{"in": "header"})]
        self.assertEqual(report(old, new).findings, ())

    def test_duplicate_parameters_are_invalid(self):
        doc = contract()
        operation(doc)["parameters"] = [parameter(), parameter()]
        with self.assertRaisesRegex(SpecError, "duplicate parameter"):
            parse(doc)

    def test_parameter_reordering_does_not_change_report(self):
        old, new = contract(), contract()
        operation(old)["parameters"] = [parameter("a"), parameter("b")]
        operation(new)["parameters"] = list(reversed(operation(old)["parameters"]))
        self.assertEqual(report(old, new).findings, ())

    def test_parameter_serialization_change_is_unknown(self):
        old, new = contract(), contract()
        operation(old)["parameters"] = [parameter()]
        operation(new)["parameters"] = [parameter(explode=False)]
        self.assertEqual(report(old, new).exit_code, 3)

    def test_serialization_defaults_are_normalized(self):
        old, new = contract(), contract()
        operation(old)["parameters"] = [parameter()]
        operation(new)["parameters"] = [parameter(style="form", explode=True, allowReserved=False)]
        self.assertEqual(report(old, new).findings, ())

    def test_numeric_type_direction(self):
        integer, number = {"type": "integer"}, {"type": "number"}
        self.assertEqual(
            report(contract(integer, request=True), contract(number, request=True)).exit_code, 0
        )
        self.assertEqual(
            report(contract(number, request=True), contract(integer, request=True)).exit_code, 1
        )
        self.assertEqual(report(contract(integer), contract(number)).exit_code, 1)
        self.assertEqual(report(contract(number), contract(integer)).exit_code, 0)

    def test_enum_direction(self):
        narrow = {"type": "string", "enum": ["active"]}
        wide = {"type": "string", "enum": ["active", "paused"]}
        self.assertEqual(
            report(contract(narrow, request=True), contract(wide, request=True)).exit_code, 0
        )
        self.assertEqual(report(contract(narrow), contract(wide)).exit_code, 1)
        self.assertEqual(report(contract(wide), contract(narrow)).exit_code, 0)

    def test_nullability_direction(self):
        narrow, wide = {"type": "string"}, {"type": "string", "nullable": True}
        self.assertEqual(
            report(contract(narrow, request=True), contract(wide, request=True)).exit_code, 0
        )
        self.assertEqual(report(contract(narrow), contract(wide)).exit_code, 1)

    def test_nullable_without_type_has_no_effect(self):
        self.assertEqual(report(contract({}), contract({"nullable": True})).findings, ())

    def test_nullable_does_not_override_enum(self):
        before = {"type": "string", "enum": ["a"]}
        after = {**before, "nullable": True}
        self.assertEqual(report(contract(before), contract(after)).exit_code, 0)

    def test_removing_guaranteed_response_property_breaks(self):
        old = {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}
        new = {"type": "object", "additionalProperties": False}
        result = report(contract(old), contract(new))
        self.assertEqual(result.exit_code, 1)
        self.assertIn("email", result.findings[0].message)

    def test_new_required_request_property_breaks(self):
        old = {"type": "object", "properties": {"name": {"type": "string"}}}
        new = {**old, "required": ["name"]}
        self.assertEqual(
            report(contract(old, request=True), contract(new, request=True)).exit_code, 1
        )

    def test_new_optional_property_in_closed_request_is_compatible(self):
        old = {"type": "object", "additionalProperties": False}
        new = {**old, "properties": {"nickname": {"type": "string"}}}
        self.assertEqual(
            report(contract(old, request=True), contract(new, request=True)).exit_code, 0
        )

    def test_new_optional_property_in_open_request_can_break(self):
        old = {"type": "object"}
        new = {**old, "properties": {"nickname": {"type": "string"}}}
        self.assertEqual(
            report(contract(old, request=True), contract(new, request=True)).exit_code, 1
        )

    def test_schema_valued_additional_properties(self):
        old = {"type": "object", "additionalProperties": {"type": "integer"}}
        new = {"type": "object", "additionalProperties": {"type": "number"}}
        self.assertEqual(
            report(contract(old, request=True), contract(new, request=True)).exit_code, 0
        )
        self.assertEqual(report(contract(old), contract(new)).exit_code, 1)

    def test_nested_array_change_reports_item_path(self):
        old = {"type": "array", "items": {"type": "integer"}}
        new = {"type": "array", "items": {"type": "string"}}
        result = report(contract(old), contract(new))
        self.assertEqual(result.exit_code, 1)
        self.assertIn("$[]", result.findings[0].message)

    def test_required_request_body_addition_breaks(self):
        old, new = contract(), contract({}, request=True)
        operation(new)["requestBody"]["required"] = True
        self.assertEqual(report(old, new).exit_code, 1)

    def test_optional_request_body_addition_is_compatible(self):
        self.assertEqual(report(contract(), contract({}, request=True)).exit_code, 0)

    def test_removed_request_body_breaks(self):
        self.assertEqual(report(contract({}, request=True), contract()).exit_code, 1)

    def test_request_media_type_removal_breaks(self):
        old, new = contract({}, request=True), contract({}, request=True)
        operation(old)["requestBody"]["content"]["text/plain"] = {"schema": {"type": "string"}}
        self.assertEqual(report(old, new).exit_code, 1)
        self.assertEqual(report(new, old).exit_code, 0)

    def test_new_response_status_breaks(self):
        old, new = contract(), contract()
        operation(new)["responses"]["202"] = {"description": "Queued"}
        self.assertEqual(report(old, new).exit_code, 1)
        self.assertEqual(report(new, old).exit_code, 0)

    def test_response_media_removal_and_addition_need_client_changes(self):
        old, new = contract({}), contract({})
        operation(new)["responses"]["200"]["content"]["text/plain"] = {"schema": {"type": "string"}}
        self.assertEqual(report(old, new).exit_code, 1)
        self.assertEqual(report(new, old).exit_code, 1)

    def test_metadata_changes_are_ignored(self):
        old = contract({"type": "string", "description": "Before", "example": "A"})
        new = contract({"type": "string", "description": "After", "example": "B"})
        operation(new)["operationId"] = "renamed"
        new["info"]["version"] = "2"
        self.assertEqual(report(old, new).findings, ())

    def test_local_schema_reference_tracks_target_changes(self):
        old = contract({"$ref": "#/components/schemas/User"})
        old["components"] = {"schemas": {"User": {"type": "integer"}}}
        new = copy.deepcopy(old)
        new["components"]["schemas"]["User"] = {"type": "number"}
        self.assertEqual(report(old, new).exit_code, 1)

    def test_json_pointer_escapes_and_percent_encoding(self):
        doc = contract({"$ref": "#/components/schemas/a~1b~0c%20d"})
        doc["components"] = {"schemas": {"a/b~c d": {"type": "string"}}}
        self.assertEqual(report(doc, contract({"type": "string"})).exit_code, 0)

    def test_reference_siblings_are_ignored_in_openapi_30(self):
        doc = contract({"$ref": "#/components/schemas/Name", "type": "integer"})
        doc["components"] = {"schemas": {"Name": {"type": "string"}}}
        self.assertEqual(report(doc, contract({"type": "string"})).findings, ())

    def test_reused_reference_is_not_a_cycle(self):
        doc = contract(
            {
                "type": "object",
                "properties": {
                    "a": {"$ref": "#/components/schemas/Name"},
                    "b": {"$ref": "#/components/schemas/Name"},
                },
            }
        )
        doc["components"] = {"schemas": {"Name": {"type": "string"}}}
        self.assertEqual(report(doc, doc).exit_code, 0)

    def test_recursive_reference_is_unknown(self):
        doc = contract({"$ref": "#/components/schemas/Node"})
        doc["components"] = {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "next": {"$ref": "#/components/schemas/Node"},
                    },
                }
            }
        }
        self.assertEqual(report(doc, doc).exit_code, 3)

    def test_external_reference_is_unknown_without_reading_it(self):
        for ref in (
            "https://example.invalid/schema.json",
            "file:///etc/passwd",
            "other.json#/User",
        ):
            with self.subTest(ref=ref):
                doc = contract({"$ref": ref})
                self.assertEqual(report(doc, doc).exit_code, 3)

    def test_broken_reference_is_invalid(self):
        with self.assertRaisesRegex(SpecError, "unresolved reference"):
            parse(contract({"$ref": "#/missing"}))

    def test_unsupported_schema_keywords_fail_closed_even_if_unchanged(self):
        for keyword, value in (("oneOf", []), ("allOf", []), ("uniqueItems", True)):
            with self.subTest(keyword=keyword):
                doc = contract({"type": "string", keyword: value})
                self.assertEqual(report(doc, doc).exit_code, 3)

    def test_unknown_has_priority_over_breaking(self):
        old, new = contract(), contract({"type": "string", "oneOf": []})
        operation(new)["parameters"] = [parameter(required=True)]
        result = report(old, new)
        self.assertGreater(result.summary["breaking"], 0)
        self.assertGreater(result.summary["unknown"], 0)
        self.assertEqual(result.exit_code, 3)

    def test_security_can_be_overridden_at_operation_level(self):
        doc = contract()
        doc["security"] = [{"bearerAuth": []}]
        doc["components"] = {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        }
        self.assertEqual(report(doc, doc).exit_code, 0)
        authenticated = copy.deepcopy(doc)
        operation(doc)["security"] = []
        self.assertEqual(report(authenticated, doc).exit_code, 0)
        self.assertEqual(report(doc, authenticated).exit_code, 1)

    def test_response_headers_are_supported(self):
        doc = contract()
        operation(doc)["responses"]["200"]["headers"] = {"X-Token": {"schema": {"type": "string"}}}
        self.assertEqual(report(doc, doc).exit_code, 0)

    def test_default_and_range_responses_are_supported(self):
        for status in ("default", "2XX"):
            doc = contract()
            operation(doc)["responses"][status] = {"description": "Fallback"}
            self.assertEqual(report(doc, doc).exit_code, 0)

    def test_effective_server_change_is_unknown(self):
        old, new = contract(), contract()
        new["servers"] = [{"url": "/v2"}]
        self.assertEqual(report(old, new).exit_code, 3)
        for doc in (old, new):
            operation(doc)["servers"] = [{"url": "/v1"}]
        self.assertEqual(report(old, new).findings, ())

    def test_unused_components_do_not_affect_analysis(self):
        doc = contract()
        doc["components"] = {"schemas": {"Unused": {"allOf": [{"$ref": "other.json"}]}}}
        self.assertEqual(report(doc, contract()).findings, ())

    def test_supported_version_is_explicit(self):
        for version in ("2.0", "3.1.0", "3.2.0", None):
            doc = contract()
            doc["openapi"] = version
            with self.subTest(version=version), self.assertRaises(SpecError):
                parse(doc)

    def test_invalid_schema_shapes(self):
        for schema in (
            {"type": ["string", "null"]},
            {"type": "array"},
            {"required": "name"},
            {"nullable": "true"},
            {"enum": []},
            {"enum": [1, 1.0]},
            {"type": "string", "enum": [None]},
            {"properties": []},
            {"type": "object", "required": ["x"], "additionalProperties": False},
        ):
            with self.subTest(schema=schema), self.assertRaises(SpecError):
                parse(contract(schema))

    def test_boolean_and_integer_are_distinct_enum_values(self):
        self.assertEqual(report(contract({"enum": [True]}), contract({"enum": [1]})).exit_code, 1)
        self.assertEqual(
            report(contract({"enum": [True, 1]}), contract({"enum": [True, 1]})).exit_code, 0
        )

    def test_report_order_does_not_depend_on_dictionary_insertion_order(self):
        old, new = contract(), contract()
        new["paths"]["/z"] = {"post": copy.deepcopy(operation(new))}
        new["paths"]["/a"] = {"post": copy.deepcopy(operation(new))}
        reordered = copy.deepcopy(new)
        reordered["paths"] = dict(reversed(list(reordered["paths"].items())))
        self.assertEqual(report(old, new).as_dict(), report(old, reordered).as_dict())


if __name__ == "__main__":
    unittest.main()
