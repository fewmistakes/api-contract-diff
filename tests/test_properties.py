"""Differential tests against jsonschema, with reproducible Hypothesis inputs."""

import copy
import itertools
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from contract_diff.schema import subset
from contract_diff.security import Security, compare_security


def json_schema(schema):
    if isinstance(schema, bool):
        return schema
    result = copy.deepcopy(schema)
    nullable = result.pop("nullable", False)
    if nullable and "type" in result:
        result["type"] = [result["type"], "null"]
    for name, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        if result.pop(name, False):
            result[name] = result.pop(bound)
    for name in ("items", "additionalProperties"):
        if name in result:
            result[name] = json_schema(result[name])
    if "properties" in result:
        result["properties"] = {
            key: json_schema(value) for key, value in result["properties"].items()
        }
    return result


@st.composite
def scalar_schemas(draw):
    kind = draw(st.sampled_from(["integer", "number", "string", "boolean"]))
    schema = {"type": kind, "nullable": draw(st.booleans())}
    if kind in ("integer", "number"):
        low, high = sorted(draw(st.tuples(st.integers(-5, 5), st.integers(-5, 5))))
        schema.update(
            minimum=low,
            maximum=high,
            exclusiveMinimum=draw(st.booleans()),
            exclusiveMaximum=draw(st.booleans()),
        )
    elif kind == "string":
        low, high = sorted(draw(st.tuples(st.integers(0, 4), st.integers(0, 4))))
        schema.update(minLength=low, maxLength=high)
    return schema


leaf = st.one_of(
    scalar_schemas(),
    st.just({}),
    st.sampled_from(
        [
            {"enum": [True]},
            {"enum": [1]},
            {"enum": [None, 0, 1, "a"]},
        ]
    ),
)
schemas = st.recursive(
    leaf,
    lambda children: st.one_of(
        st.builds(
            lambda item, size: {"type": "array", "items": item, "maxItems": size},
            children,
            st.integers(0, 3),
        ),
        st.builds(
            lambda a, b, required, extra: {
                "type": "object",
                "properties": {"a": a, "b": b},
                "required": required,
                "additionalProperties": extra,
            },
            children,
            children,
            st.sampled_from([[], ["a"], ["a", "b"]]),
            st.booleans(),
        ),
    ),
    max_leaves=5,
)
scalar_values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-8, 8),
    st.integers(-16, 16).map(lambda x: x / 2),
    st.text(alphabet="ab", max_size=5),
)
values = st.recursive(
    scalar_values,
    lambda child: st.one_of(
        st.lists(child, max_size=3),
        st.dictionaries(st.sampled_from(["a", "b", "extra"]), child, max_size=3),
    ),
    max_leaves=5,
)


class DifferentialTests(unittest.TestCase):
    @settings(max_examples=1500, derandomize=True, deadline=None, database=None)
    @given(source=schemas, target=schemas, value=values)
    def test_proven_inclusion_agrees_with_independent_validator(self, source, target, value):
        if subset(source, target) != []:
            return
        source_validator = Draft202012Validator(json_schema(source))
        target_validator = Draft202012Validator(json_schema(target))
        candidates = [value, None, False, True, 0, 1, 1.5, "", "a", [], {}, {"a": value}, [value]]
        for candidate in candidates:
            if source_validator.is_valid(candidate):
                self.assertTrue(target_validator.is_valid(candidate), (source, target, candidate))

    def test_bounded_integer_inclusion_matches_exhaustive_oracle_in_both_directions(self):
        schemas = [
            {
                "type": "integer",
                "minimum": low,
                "maximum": high,
                "exclusiveMinimum": low_open,
                "exclusiveMaximum": high_open,
            }
            for low, high in itertools.combinations_with_replacement(range(-3, 4), 2)
            for low_open, high_open in itertools.product((False, True), repeat=2)
        ]
        accepted = [
            {
                value
                for value in range(-4, 5)
                if Draft202012Validator(json_schema(schema)).is_valid(value)
            }
            for schema in schemas
        ]
        for a, b in itertools.product(range(len(schemas)), repeat=2):
            self.assertEqual(
                subset(schemas[a], schemas[b]) == [],
                accepted[a] <= accepted[b],
                (schemas[a], schemas[b]),
            )

    def test_security_dnf_against_exhaustive_credential_sets(self):
        definitions = {
            "a": {"type": "apiKey", "in": "header", "name": "A"},
            "b": {"type": "apiKey", "in": "header", "name": "B"},
        }
        credentials = [frozenset(), frozenset({"a"}), frozenset({"b"}), frozenset({"a", "b"})]
        requirements = [frozenset((key, frozenset()) for key in keys) for keys in credentials]
        contracts = [
            Security(frozenset(group), definitions)
            for length in range(1, 5)
            for group in itertools.combinations(requirements, length)
        ]

        def accepts(security, supplied):
            return any(
                {name for name, _ in alternative} <= supplied
                for alternative in security.alternatives
            )

        for old, new in itertools.product(contracts, repeat=2):
            expected = all(
                not accepts(old, supplied) or accepts(new, supplied) for supplied in credentials
            )
            outcome = compare_security(old, new)
            actual = outcome is None or outcome[0] == "compatible"
            self.assertEqual(actual, expected, (old, new))
