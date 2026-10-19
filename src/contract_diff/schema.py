"""Set inclusion for the supported subset of Schema Objects.

Requests require old <= new; responses require new <= old. Keeping this function
direction-free avoids maintaining two subtly different rule implementations.
"""

import json
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from .model import Schema

ALL_TYPES = frozenset({"null", "boolean", "integer", "real", "string", "array", "object"})
NEG_INF, POS_INF = Decimal("-Infinity"), Decimal("Infinity")
OPAQUE = frozenset({"format", "pattern", "xml"})


def interval(schema: Schema, *, integer: bool) -> tuple[Decimal, bool, Decimal, bool]:
    low = Decimal(str(schema.get("minimum", "-Infinity")))
    high = Decimal(str(schema.get("maximum", "Infinity")))
    low_open = schema.get("exclusiveMinimum", False)
    high_open = schema.get("exclusiveMaximum", False)
    if integer:
        if low.is_finite():
            rounded = low.to_integral_value(rounding=ROUND_CEILING)
            low = Decimal(int(rounded) + int(low_open and rounded == low))
        if high.is_finite():
            rounded = high.to_integral_value(rounding=ROUND_FLOOR)
            high = Decimal(int(rounded) - int(high_open and rounded == high))
        low_open = high_open = False
    return low, low_open, high, high_open


def value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "integer" if isinstance(value, int) or value.is_integer() else "real"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def equal_value(left: Any, right: Any) -> bool:
    # Python treats True == 1; JSON Schema treats boolean and number as distinct.
    return value_type(left) == value_type(right) and left == right


def types(schema: Schema) -> frozenset[str]:
    kind = schema.get("type")
    if kind is None:
        result = set(ALL_TYPES)
    else:
        result = {"integer", "real"} if kind == "number" else {kind}
    if kind is not None and schema.get("nullable", False):
        result.add("null")
    for numeric in ("integer", "real"):
        low, low_open, high, high_open = interval(schema, integer=numeric == "integer")
        if low > high or (low == high and (low_open or high_open)):
            result.discard(numeric)
        elif numeric == "real" and low == high and low == low.to_integral_value():
            result.discard(numeric)
    if "enum" in schema:
        result &= {
            value_type(value) for value in schema["enum"] if _scalar_constraints(schema, value)
        }
    if "array" in result and schema.get("minItems", 0) > 0 and "items" in schema:
        if not types(schema["items"]):
            result.discard("array")
    if "object" in result:
        for name in schema.get("required", []):
            rule = schema.get("properties", {}).get(name, schema.get("additionalProperties", True))
            if rule is False or (isinstance(rule, dict) and not types(rule)):
                result.discard("object")
                break
    return frozenset(result)


def accepts_scalar(schema: Schema, value: Any) -> bool:
    if value_type(value) not in types(schema):
        return False
    if "enum" in schema and not any(equal_value(value, item) for item in schema["enum"]):
        return False
    return _scalar_constraints(schema, value)


def _scalar_constraints(schema: Schema, value: Any) -> bool:
    if value_type(value) in {"integer", "real"}:
        low, low_open, high, high_open = interval(schema, integer=False)
        number = Decimal(str(value))
        return (number > low if low_open else number >= low) and (
            number < high if high_open else number <= high
        )
    if isinstance(value, str):
        return schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf"))
    return True


def same_schema(left: Schema, right: Schema) -> bool:
    # Dict equality would also collapse enum [True] and enum [1].
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def subset(source: Schema, target: Schema, path: str = "$") -> list[str] | None:
    """Explain values allowed by source but rejected by target; None means unknown."""
    if "_unsupported" in source or "_unsupported" in target:
        return None
    if not target:
        return []
    if same_schema(source, target):
        return []
    if any(key in source or key in target for key in OPAQUE):
        if any(source.get(key) != target.get(key) for key in OPAQUE):
            return None
        # A common refinement preserves a proven inclusion. It can eliminate a
        # counterexample, so failure of the unrefined comparison is not proof.
        bare_source = {key: value for key, value in source.items() if key not in OPAQUE}
        bare_target = {key: value for key, value in target.items() if key not in OPAQUE}
        result = subset(bare_source, bare_target, path)
        return [] if result == [] else None
    if "enum" in source:
        return [
            f"{path}: value {json.dumps(value, ensure_ascii=True)} is not accepted."
            for value in source["enum"]
            if accepts_scalar(source, value) and not accepts_scalar(target, value)
        ]
    source_types, target_types = types(source), types(target)
    if "enum" in target:
        if source_types <= {"boolean", "null"}:
            values = [value for value in (None, False, True) if value_type(value) in source_types]
            return [
                f"{path}: value {json.dumps(value)} is not accepted."
                for value in values
                if not accepts_scalar(target, value)
            ]
        if source_types <= {"integer", "null"}:
            low, _, high, _ = interval(source, integer=True)
            if low.is_finite() and high.is_finite() and high - low < len(target["enum"]):
                values = list(range(int(low), int(high) + 1))
                if "null" in source_types:
                    values.append(None)
                return [
                    f"{path}: value {json.dumps(value)} is not accepted."
                    for value in values
                    if not accepts_scalar(target, value)
                ]
        singleton = None
        if source_types <= {"real", "integer", "null"}:
            low, low_open, high, high_open = interval(source, integer=False)
            if low == high and not low_open and not high_open:
                singleton = [int(low) if low == low.to_integral_value() else float(low)]
        elif source_types <= {"string", "null"} and source.get("maxLength") == 0:
            singleton = [""]
        if singleton is not None:
            if "null" in source_types:
                singleton.append(None)
            return [
                f"{path}: value {json.dumps(value)} is not accepted."
                for value in singleton
                if not accepts_scalar(target, value)
            ]
        return [f"{path}: unrestricted values are restricted to an enum."]
    reasons = []
    missing = source_types - target_types
    if missing:
        reasons.append(f"{path}: allowed type(s) {', '.join(sorted(missing))} are not accepted.")
    common = source_types & target_types
    for numeric in sorted(common & {"integer", "real"}):
        a_low, a_open, a_high, a_high_open = interval(source, integer=numeric == "integer")
        b_low, b_open, b_high, b_high_open = interval(target, integer=numeric == "integer")
        if a_low < b_low or (a_low == b_low and b_open and not a_open):
            reasons.append(f"{path}: {numeric} lower bound excludes previously allowed values.")
        if a_high > b_high or (a_high == b_high and b_high_open and not a_high_open):
            reasons.append(f"{path}: {numeric} upper bound excludes previously allowed values.")
    if "string" in common:
        reasons.extend(_length_subset(source, target, "Length", path))
    if "object" in common:
        for name in sorted(set(target.get("required", [])) - set(source.get("required", []))):
            reasons.append(f"{path}[{json.dumps(name)}]: may be absent but must be present.")
        source_props = source.get("properties", {})
        target_props = target.get("properties", {})
        for name in sorted(source_props.keys() | target_props.keys()):
            result = _property_subset(
                source_props.get(name, source.get("additionalProperties", True)),
                target_props.get(name, target.get("additionalProperties", True)),
                f"{path}[{json.dumps(name)}]",
            )
            if result is None:
                return None
            reasons.extend(result)
        result = _property_subset(
            source.get("additionalProperties", True),
            target.get("additionalProperties", True),
            f"{path}[*]",
        )
        if result is None:
            return None
        reasons.extend(result)
    if "array" in common:
        source_bounds = source if types(source.get("items", {})) else {**source, "maxItems": 0}
        target_bounds = target if types(target.get("items", {})) else {**target, "maxItems": 0}
        reasons.extend(_length_subset(source_bounds, target_bounds, "Items", path))
        if source_bounds.get("maxItems") == 0:
            return reasons
        result = subset(source.get("items", {}), target.get("items", {}), f"{path}[]")
        if result is None:
            return None
        reasons.extend(result)
    return reasons


def _length_subset(source: Schema, target: Schema, suffix: str, path: str) -> list[str]:
    reasons = []
    minimum, maximum = "min" + suffix, "max" + suffix
    if source.get(minimum, 0) < target.get(minimum, 0):
        reasons.append(f"{path}: {minimum} excludes previously allowed values.")
    if source.get(maximum, float("inf")) > target.get(maximum, float("inf")):
        reasons.append(f"{path}: {maximum} excludes previously allowed values.")
    return reasons


def _property_subset(source: Schema | bool, target: Schema | bool, path: str) -> list[str] | None:
    if source is False or target is True:
        return []
    if target is False:
        return [f"{path}: property is allowed but the target forbids it."]
    return subset({} if source is True else source, target, path)
