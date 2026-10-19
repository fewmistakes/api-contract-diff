"""Authentication requirements are ORs of ANDs, including required OAuth scopes."""

import json
from dataclasses import dataclass, field
from typing import Any

Requirement = frozenset[tuple[str, frozenset[str]]]


@dataclass(frozen=True)
class Security:
    alternatives: frozenset[Requirement] = frozenset({frozenset()})
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict, compare=False)


def canonical_scheme(raw: dict[str, Any]) -> dict[str, Any]:
    """Remove documentation fields, retaining credential and issuer identity."""
    result = {
        key: value
        for key, value in raw.items()
        if key not in {"description", "bearerFormat"} and not key.startswith("x-")
    }
    if "flows" in result:
        result["flows"] = {
            name: {
                key: value
                for key, value in flow.items()
                if key != "scopes" and not key.startswith("x-")
            }
            for name, flow in result["flows"].items()
        }
    return result


def canonical_alternative(alternative: Requirement, definitions: dict) -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for name, scopes in alternative:
        signature = json.dumps(definitions[name], sort_keys=True)
        result[signature] = result.get(signature, frozenset()) | scopes
    return result


def compare_security(old: Security, new: Security) -> tuple[str, str] | None:
    before = [canonical_alternative(alt, old.definitions) for alt in old.alternatives]
    after = [canonical_alternative(alt, new.definitions) for alt in new.alternatives]
    covered = all(
        any(
            candidate.keys() <= original.keys()
            and all(scopes <= original[scheme] for scheme, scopes in candidate.items())
            for candidate in after
        )
        for original in before
    )
    if not covered:
        shared = old.definitions.keys() & new.definitions.keys()
        if any(old.definitions[name] != new.definitions[name] for name in shared):
            return "unknown", "An authentication scheme changed credential or issuer configuration."
        return (
            "breaking",
            "Previously sufficient authentication credentials or scopes no longer suffice.",
        )
    reverse_covered = all(
        any(
            candidate.keys() <= original.keys()
            and all(scopes <= original[scheme] for scheme, scopes in candidate.items())
            for candidate in before
        )
        for original in after
    )
    if not reverse_covered:
        return "compatible", "Existing authentication alternatives remain accepted."
    return None
