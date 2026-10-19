from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .security import Security

Severity = Literal["breaking", "compatible", "unknown"]
Schema = dict[str, Any]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    operation: str
    location: str
    message: str


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]

    @property
    def summary(self) -> dict[str, int]:
        return {
            severity: sum(item.severity == severity for item in self.findings)
            for severity in ("breaking", "compatible", "unknown")
        }

    @property
    def exit_code(self) -> int:
        counts = self.summary
        if counts["unknown"]:
            return 3
        return 1 if counts["breaking"] else 0

    @property
    def status(self) -> str:
        return {0: "compatible", 1: "breaking", 3: "inconclusive"}[self.exit_code]

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_version": 1,
            "status": self.status,
            "summary": self.summary,
            "findings": [asdict(item) for item in self.findings],
        }


@dataclass(frozen=True)
class Parameter:
    required: bool
    schema: Schema
    serialization: tuple[str, bool, bool]


@dataclass(frozen=True)
class Body:
    required: bool
    content: dict[str, Schema]


@dataclass(frozen=True)
class Response:
    content: dict[str, Schema]
    headers: dict[str, Parameter]


@dataclass(frozen=True)
class Operation:
    parameters: dict[tuple[str, str], Parameter]
    body: Body | None
    responses: dict[str, Response]
    servers: Any
    security: Security


@dataclass
class Contract:
    operations: dict[tuple[str, str], Operation] = field(default_factory=dict)
    diagnostics: list[Finding] = field(default_factory=list)
