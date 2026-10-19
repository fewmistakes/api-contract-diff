import copy
import unittest

from test_contracts import contract, report

from contract_diff.loader import SpecError, parse
from contract_diff.schema import subset


class ConstraintTests(unittest.TestCase):
    def test_integer_bounds_respect_exclusive_endpoints(self):
        source = {"type": "integer", "minimum": 0, "exclusiveMinimum": True}
        target = {"type": "integer", "minimum": 1}
        self.assertEqual(subset(source, target), [])
        self.assertEqual(subset(target, source), [])
        self.assertTrue(subset({"type": "number", "minimum": 0.1}, target))

    def test_integer_bounds_round_outward(self):
        a = {"type": "integer", "minimum": 1.2, "maximum": 3.9}
        b = {"type": "integer", "minimum": 2, "maximum": 3}
        self.assertEqual(subset(a, b), [])
        self.assertEqual(subset(b, a), [])

    def test_numeric_bound_direction(self):
        old = {"type": "integer", "minimum": 0, "maximum": 100}
        new = {"type": "integer", "minimum": 1, "maximum": 100}
        self.assertEqual(
            report(contract(old, request=True), contract(new, request=True)).exit_code, 1
        )
        self.assertEqual(report(contract(old), contract(new)).exit_code, 0)

    def test_exclusive_bound_is_not_treated_as_inclusive(self):
        a = {"type": "number", "maximum": 1}
        b = {**a, "exclusiveMaximum": True}
        self.assertTrue(subset(a, b))
        self.assertEqual(subset(b, a), [])

    def test_empty_numeric_domain_is_a_subset(self):
        empty = {
            "type": "integer",
            "minimum": 0,
            "maximum": 1,
            "exclusiveMinimum": True,
            "exclusiveMaximum": True,
        }
        self.assertEqual(subset(empty, {"type": "string"}), [])

    def test_nullable_empty_numeric_domain_still_contains_null(self):
        source = {"type": "integer", "minimum": 2, "maximum": 1, "nullable": True}
        self.assertTrue(subset(source, {"type": "integer"}))
        self.assertEqual(subset(source, {"enum": [None]}), [])

    def test_number_singleton_can_be_integer(self):
        self.assertEqual(
            subset({"type": "number", "minimum": 1, "maximum": 1}, {"type": "integer"}), []
        )

    def test_small_integer_range_can_be_an_enum(self):
        self.assertEqual(
            subset({"type": "integer", "minimum": 1, "maximum": 2}, {"enum": [1, 2]}), []
        )

    def test_singleton_number_and_empty_string_can_be_enums(self):
        self.assertEqual(
            subset({"type": "number", "minimum": 1.5, "maximum": 1.5}, {"enum": [1.5]}), []
        )
        self.assertEqual(subset({"type": "string", "maxLength": 0}, {"enum": [""]}), [])

    def test_large_integer_bound_does_not_round_through_decimal_context(self):
        boundary = 10**50 + 1
        a = {"type": "integer", "minimum": boundary, "exclusiveMinimum": True}
        b = {"type": "integer", "minimum": boundary + 1}
        self.assertEqual(subset(a, b), [])
        self.assertEqual(subset(b, a), [])

    def test_impossible_items_mean_only_an_empty_array(self):
        a = {"type": "array", "items": {"type": "integer", "minimum": 2, "maximum": 1}}
        self.assertEqual(subset(a, {"type": "array", "maxItems": 0, "items": {}}), [])
        self.assertEqual(subset({**a, "minItems": 1}, {"type": "string"}), [])

    def test_enum_intersects_numeric_constraints(self):
        source = {"type": "integer", "enum": [1, 2], "minimum": 2}
        self.assertEqual(subset(source, {"enum": [2]}), [])
        self.assertTrue(subset({"enum": [1]}, {"type": "integer", "minimum": 2}))

    def test_string_enum_intersects_length(self):
        self.assertEqual(subset({"enum": ["", "ab"], "minLength": 1}, {"enum": ["ab"]}), [])

    def test_length_limits_have_direction(self):
        for minimum, maximum, kind in (
            ("minLength", "maxLength", "string"),
            ("minItems", "maxItems", "array"),
        ):
            base = {"type": kind, **({"items": {}} if kind == "array" else {})}
            bounded = {**base, minimum: 1, maximum: 10}
            with self.subTest(kind=kind):
                self.assertTrue(subset(base, bounded))
                self.assertEqual(subset(bounded, base), [])

    def test_empty_array_does_not_depend_on_item_schema(self):
        self.assertEqual(
            subset(
                {"type": "array", "maxItems": 0, "items": {"type": "string"}},
                {"type": "array", "items": {"type": "integer"}},
            ),
            [],
        )

    def test_int32_to_int64_has_opposite_request_and_response_outcomes(self):
        old = {"type": "integer", "format": "int32"}
        new = {"type": "integer", "format": "int64"}
        self.assertEqual(
            report(contract(old, request=True), contract(new, request=True)).exit_code, 0
        )
        self.assertEqual(report(contract(old), contract(new)).exit_code, 1)

    def test_int64_preserves_large_exact_bounds(self):
        old = {"type": "integer", "format": "int64"}
        new = {**old, "maximum": 2**63 - 2}
        result = report(contract(old, request=True), contract(new, request=True))
        self.assertEqual(result.exit_code, 1)

    def test_redundant_integer_format_does_not_change_semantics(self):
        a = {"type": "integer", "format": "int32", "minimum": 0, "maximum": 10}
        b = {"type": "integer", "minimum": 0, "maximum": 10}
        self.assertEqual(report(contract(a), contract(b)).findings, ())

    def test_opaque_format_change_is_unknown(self):
        for key, old_value, new_value in (
            ("format", "uuid", "date"),
            ("pattern", "^a$", "^b$"),
            ("xml", {"name": "a"}, {"name": "b"}),
        ):
            a = {"type": "string", key: old_value}
            b = {"type": "string", key: new_value}
            with self.subTest(key=key):
                self.assertEqual(report(contract(a), contract(a)).exit_code, 0)
                self.assertEqual(report(contract(a), contract(b)).exit_code, 3)

    def test_unchanged_opaque_predicate_preserves_proven_inclusion(self):
        a = {"type": "string", "pattern": "^[a-z]+$", "maxLength": 5}
        b = {**a, "maxLength": 10}
        self.assertEqual(report(contract(a, request=True), contract(b, request=True)).exit_code, 0)
        self.assertEqual(report(contract(a), contract(b)).exit_code, 3)

    def test_readonly_is_projected_out_of_request_required(self):
        base = {"type": "object", "additionalProperties": False}
        with_id = {
            **base,
            "required": ["id"],
            "properties": {"id": {"type": "integer", "readOnly": True}},
        }
        self.assertEqual(
            report(contract(base, request=True), contract(with_id, request=True)).exit_code, 0
        )
        self.assertEqual(report(contract(with_id), contract(base)).exit_code, 1)

    def test_writeonly_is_projected_out_of_response(self):
        a = {
            "type": "object",
            "properties": {"password": {"type": "string", "writeOnly": True}},
            "required": ["password"],
        }
        b = copy.deepcopy(a)
        b["properties"]["password"]["type"] = "integer"
        self.assertEqual(report(contract(a), contract(b)).exit_code, 0)
        self.assertEqual(report(contract(a, request=True), contract(b, request=True)).exit_code, 1)

    def test_readonly_through_ref(self):
        doc = contract(
            {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"$ref": "#/components/schemas/ID"}},
            },
            request=True,
        )
        doc["components"] = {"schemas": {"ID": {"type": "integer", "readOnly": True}}}
        normalized = parse(doc).operations[("/users", "get")].body.content["application/json"]
        self.assertNotIn("required", normalized)
        self.assertIs(normalized["properties"]["id"], False)

    def test_invalid_constraint_values(self):
        for schema in (
            {"minimum": True},
            {"maximum": "5"},
            {"exclusiveMinimum": 1},
            {"exclusiveMaximum": True},
            {"minLength": -1},
            {"minItems": 1.5},
            {"format": []},
            {"pattern": 1},
            {"readOnly": True, "writeOnly": True},
        ):
            with self.subTest(schema=schema), self.assertRaises(SpecError):
                parse(contract(schema))
