"""Measure scope on pinned, unmodified upstream documents; never fetch the network."""

import argparse
import collections
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

from contract_diff import __version__, compare, load
from contract_diff.loader import SpecError

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20")
    corpus = ROOT / "tests" / "corpus"
    rows = []
    for source in json.loads((corpus / "manifest.json").read_text(encoding="utf-8")):
        path = corpus / source["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Snapshot hash mismatch: {path}")
        times = []
        row = {key: source[key] for key in ("name", "commit", "sha256", "bytes")}
        for _ in range(args.repeat):
            start = time.perf_counter()
            try:
                contract = load(path, label="snapshot")
                result = compare(contract, contract)
                row.update(
                    operations=len(contract.operations),
                    operations_with_unsupported_features=len(
                        {f.operation for f in contract.diagnostics}
                    ),
                    status=result.status,
                    summary=result.summary,
                    diagnostics=dict(
                        collections.Counter(
                            f.message for f in result.findings if f.severity == "unknown"
                        )
                    ),
                )
            except SpecError as exc:
                # Keep paths relative so reports can be committed without machine-specific paths.
                row.update(status="error", error=str(exc).replace(str(path), source["file"]))
            times.append(time.perf_counter() - start)
        row["median_seconds"] = round(statistics.median(times), 4)
        rows.append(row)
    report = {
        "checker_version": __version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "repeat": args.repeat,
        "measurement": "Read and normalize one file, then compare that contract with itself.",
        "sources": rows,
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
