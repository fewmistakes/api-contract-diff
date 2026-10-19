import argparse
import json
import sys

from . import __version__
from .compare import compare
from .loader import SpecError, load
from .model import Report


def _terminal_safe(value: str) -> str:
    return "".join(
        character if character.isprintable() else f"\\u{ord(character):04x}" for character in value
    )


def render_text(report: Report) -> str:
    lines = []
    for finding in report.findings:
        lines.extend(
            [
                f"{finding.severity.upper():10} {_terminal_safe(finding.operation)}",
                f"           {_terminal_safe(finding.message)}",
                f"           {_terminal_safe(finding.location)} [{finding.code}]",
                "",
            ]
        )
    counts = report.summary
    if not report.findings:
        lines.append("No contract changes detected within the supported scope.")
    lines.append(
        f"{report.status.upper()}: {counts['breaking']} breaking, "
        f"{counts['compatible']} compatible, {counts['unknown']} unknown."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="contract-diff",
        description="Check whether an OpenAPI 3.0 contract preserves existing clients.",
        epilog="Exit codes: 0 compatible, 1 breaking, 2 invalid input, 3 inconclusive.",
    )
    parser.add_argument("old", help="previous contract, as local JSON or YAML")
    parser.add_argument("new", help="proposed contract, as local JSON or YAML")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    try:
        report = compare(load(args.old, label="old"), load(args.new, label="new"))
    except (SpecError, RecursionError) as exc:
        if args.format == "json":
            print(json.dumps({"report_version": 1, "status": "error", "error": str(exc)}))
        else:
            print(f"contract-diff: {_terminal_safe(str(exc))}", file=sys.stderr)
        return 2
    try:
        print(
            json.dumps(report.as_dict(), indent=2) if args.format == "json" else render_text(report)
        )
    except BrokenPipeError:
        return report.exit_code
    return report.exit_code
