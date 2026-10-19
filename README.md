# API Contract Diff

Check whether an OpenAPI change can break existing clients. The CLI compares
two local contracts, explains the changes, and returns a CI-friendly exit code.

Version 0.2 supports a defined subset of **OpenAPI 3.0**. It distinguishes
breaking, compatible, and inconclusive results. Unsupported constructs cannot
silently turn a CI check green.

## Try it

Python 3.11 or later. From a checkout:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
contract-diff examples/old.openapi.json examples/new.openapi.json
```

The demo adds a required query parameter, stops guaranteeing a response field,
and adds an optional property to a closed request object. Its result is:

```text
BREAKING: 2 breaking, 1 compatible, 0 unknown.
```

It intentionally exits with code `1`. See the [full output](examples/report.txt).
For a passing example, compare `examples/old.openapi.json` with
`examples/compatible.openapi.json`.

JSON needs no runtime dependencies. YAML is optional:

```sh
python -m pip install ".[yaml]"
contract-diff old.yaml new.yaml
```

`python -m contract_diff` is equivalent to the console command.

## Comparison model

The checker models replacement of a server contract from an existing client's
perspective:

- Requests: every previously valid value must remain accepted.
- Responses: every newly allowed value must fit the previous schema.
- Authentication: each previously sufficient credential/scope combination must
  still satisfy at least one new authentication alternative.

| Change | Request | Response |
| --- | --- | --- |
| `integer` becomes `number` | Compatible | Breaking |
| Enum gains a member | Compatible | Breaking |
| Minimum increases | Breaking | Compatible |
| Field becomes required | Breaking | Compatible |
| Null becomes allowed | Compatible | Breaking |

These examples assume other constraints stay unchanged. Rules interact:
an integer with `minimum: 0` and `exclusiveMinimum: true` has the same lower
bound as `minimum: 1`. An enum is also intersected with its bounds.

Object openness matters. Adding an optional `nickname: string` to a request
with `additionalProperties: false` is compatible. Doing that in an open object
rejects the previously valid `{"nickname": 42}`. The checker does not assume
clients never send undeclared keys.

## Supported scope

- OpenAPI 3.0.x in UTF-8 JSON, or YAML with the optional parser.
- Primitive types, scalar enums, `nullable`, nested properties, `required`,
  arrays, and boolean/schema-valued `additionalProperties`.
- Numeric bounds and exclusive bounds; `int32`/`int64` as signed ranges.
- `minLength`, `maxLength`, `minItems`, and `maxItems`.
- Request/response projection of `readOnly` and `writeOnly` properties.
- Local `$ref` in schemas, parameters, request bodies, responses, and headers.
- Parameter inheritance, requiredness, serialization-change detection,
  response headers, and exact media type keys.
- Explicit response codes, status ranges such as `2XX`, and `default`, with
  explicit code taking precedence over range and range over default.
- API key, HTTP, OAuth2, and OpenID requirements, including OR alternatives,
  AND requirements, OAuth scopes, and operation-level overrides.

Other `format` values, `pattern`, and XML metadata are retained as opaque
constraints. Identical predicates can preserve a proven inclusion; the engine
does not validate dates/UUIDs or solve regular-expression inclusion. Changed
predicates, and cases requiring those semantics, return inconclusive.

Still inconclusive: schema composition (`allOf`, `oneOf`, `anyOf`, `not`),
recursive/external references, Path Item references, object/array enums,
`uniqueItems`, property-count constraints, callbacks, links, encoding objects,
media wildcards, and other unsupported keywords. Changed issuer/credential
configuration, parameter serialization, or effective servers also need review.

OpenAPI 3.1/3.2 and Swagger 2 are rejected. Descriptions, examples, defaults,
deprecation annotations, operation IDs, and `x-*` extensions are outside the
comparison. Unreferenced component contents are not traversed.

The parser checks the structures needed by the engine; it is not a complete
OpenAPI validator. Results concern the declared wire contract, not implementation
behavior, route shadowing, SDK names, or authorization policy absent from the
specification. See [design decisions](docs/design.md) for conservative HTTP and
field-projection policies.

## Evidence from upstream specifications

The corpus contains unmodified snapshots with pinned commits, SHA-256 hashes,
and upstream licenses:

| Snapshot | Scope check |
| --- | --- |
| Swagger Petstore, 19 operations | Supported |
| GitHub REST API, 1242 operations | Loads; composition and several other features remain inconclusive |
| Meilisearch, OpenAPI 3.1 | Explicitly rejected |

A self-comparison measures scope, not correctness under changes. Separate tests
mutate the actual Petstore contract and a documented GitHub `/licenses`
projection: required parameters, OAuth scopes, integer bounds, and response
guarantees must produce the expected result.

Read the [validation report](docs/validation.md) for exact revisions, remaining
limitations, local timings, and reproduction commands.

## Reports and CI

```sh
contract-diff old.json new.json --format json > report.json
```

Reports have `report_version`, `status`, `summary`, and `findings`. Each finding
has a severity, rule code, operation, logical location, and reason. Locations
use escaped pointer segments; merged parameters use `parameters[query:name]`,
and response fallbacks may use a `[statuses:...]` selector. These selectors are
logical addresses, not directly dereferenceable JSON Pointers. Reference
findings identify the use site; removed elements refer to the old contract.
Counts are findings, not necessarily distinct edits.

| Exit | Meaning |
| --- | --- |
| `0` | Compatible within the supported scope |
| `1` | Breaking changes; analysis completed |
| `2` | Invalid/unreadable input, missing YAML dependency, or resource limit |
| `3` | Inconclusive; manual review required |

Unknown takes priority over breaking in the exit code; both remain visible.
Unsupported constructs produce diagnostics even when unchanged. Opaque
predicates are different: their unchanged identity can participate in a proof.
There is no switch to silently treat unknown as compatible.

Text input errors go to stderr. JSON mode emits one object with `status:
"error"` and `error` on stdout. Argument errors use argparse's normal output.

Run the command in CI after installing the checker and obtaining both contract
files; any nonzero exit fails the step. The [repository workflow](.github/workflows/ci.yml)
tests the checker on Linux and Windows and smoke-tests its wheel. It does not
publish a package or release.

## Input boundaries

No external references are read and no network requests are made by the CLI.
Files are limited to 16 MiB, schema/reference depth to 64 levels, and expanded
schema work to 200,000 nodes per document. The higher node budget in 0.2 permits
the full GitHub snapshot; it remains bounded against repeated-reference growth.

YAML uses JSON-compatible scalar rules, rejects aliases/merge keys, non-JSON
tags, duplicate keys, and non-string mapping keys. Quote numeric response codes.
YAML composition is limited to 100,000 nodes and 64 levels. JSON duplicates and
non-finite numbers are rejected. Decimal literals that lose precision when
decoded are rejected instead of rounded into different enum values.
Contradictory string/array length bounds are rejected.

## Develop

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python scripts/corpus_report.py --repeat 3 --output docs/corpus-report.json
python -m build
```

Tests include bounded exhaustive checks, 1500 reproducible Hypothesis examples
checked against an independent JSON Schema validator, and offline upstream
regressions. No test fetches a service or requires API credentials.

Python interface:

```python
from contract_diff import compare, load

report = compare(load("old.json"), load("new.json"))
print(report.as_dict())
```

Project code is MIT licensed. Vendored fixtures retain their
[upstream licenses](tests/corpus/licenses) and [provenance](tests/corpus/manifest.json).
