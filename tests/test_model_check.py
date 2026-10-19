"""Bounded semantic check with an independent value validator.

This catches interactions between rules that example-by-example tests miss.
It is not a proof for unbounded schemas or values.
"""

import itertools
import unittest

from contract_diff.schema import subset


def validates(schema, value):
    kind = schema.get("type")
    if value is None:
        matches = kind is None or schema.get("nullable", False)
    elif kind == "integer":
        matches = type(value) is int or (type(value) is float and value % 1 == 0)
    elif kind == "number":
        matches = type(value) in (int, float)
    elif kind is None:
        matches = True
    else:
        matches = (
            type(value)
            is {
                "string": str,
                "boolean": bool,
                "object": dict,
                "array": list,
            }[kind]
        )
    if not matches:
        return False
    if "enum" in schema:

        def same(left, right):
            if type(left) in (int, float) and type(right) in (int, float):
                return left == right
            return type(left) is type(right) and left == right

        if not any(same(value, choice) for choice in schema["enum"]):
            return False
    if isinstance(value, dict):
        if not set(schema.get("required", [])) <= value.keys():
            return False
        for name, item in value.items():
            rule = schema.get("properties", {}).get(name, schema.get("additionalProperties", True))
            if rule is False or (isinstance(rule, dict) and not validates(rule, item)):
                return False
    if isinstance(value, list):
        return all(validates(schema.get("items", {}), item) for item in value)
    return True


class BoundedModelTests(unittest.TestCase):
    def test_every_reported_inclusion_holds_for_sampled_values(self):
        scalar = [None, False, True, -1, 0, 1, 1.5, "", "a", "b"]
        values = scalar + [[]] + [[value] for value in scalar]
        values += [{}] + [{key: value} for key in ("a", "b", "extra") for value in scalar]
        values += [{"a": a, "b": b} for a, b in itertools.product(scalar, repeat=2)]
        schemas = [{}, {"enum": [False, True]}, {"enum": [1, "a"]}, {"enum": [True]}, {"enum": [1]}]
        for kind in ("string", "boolean", "integer", "number"):
            schemas.extend([{"type": kind}, {"type": kind, "nullable": True}])
        schemas += [{"type": "array", "items": item} for item in ({}, {"type": "integer"})]
        for extra in (True, False, {"type": "integer"}):
            for required in ([], ["a"]):
                for prop in ({}, {"type": "string"}, {"type": "integer"}):
                    schemas.append(
                        {
                            "type": "object",
                            "properties": {"a": prop},
                            "required": required,
                            "additionalProperties": extra,
                        }
                    )
        # Cache the oracle results so this exhaustive pair check stays quick.
        accepted = [
            {index for index, value in enumerate(values) if validates(s, value)} for s in schemas
        ]
        for left, right in itertools.product(range(len(schemas)), repeat=2):
            if subset(schemas[left], schemas[right]) == []:
                witnesses = accepted[left] - accepted[right]
                self.assertFalse(
                    witnesses,
                    (
                        schemas[left],
                        schemas[right],
                        [values[index] for index in witnesses],
                    ),
                )


if __name__ == "__main__":
    unittest.main()
