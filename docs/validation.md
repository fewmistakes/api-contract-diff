# Validation against upstream specifications

The checker is exercised on three unmodified public OpenAPI documents stored in
[`tests/corpus`](../tests/corpus). Each snapshot is pinned to an upstream commit
and verified by SHA-256 in `test_upstream_snapshots_match_recorded_hashes`.
Upstream licenses are kept in [`tests/corpus/licenses`](../tests/corpus/licenses).

| Snapshot | Upstream | Commit | Size | License |
| --- | --- | --- | --- | --- |
| `swagger-petstore.yaml` | swagger-api/swagger-petstore | `d57941e8` | 23 KB | Apache-2.0 |
| `meilisearch.json` | meilisearch/open-api | `0b0b0fdb` | 431 KB | MIT |
| `github.json` | github/rest-api-description | `755ee33b` | 13.5 MB | MIT |

Full commit hashes, source URLs and checksums are in
[`tests/corpus/manifest.json`](../tests/corpus/manifest.json).

## Scope check

Each document is loaded and compared with itself. This measures how much of a
real specification falls inside the supported subset; it says nothing about
correctness under changes, which the mutation tests below cover.

| Snapshot | Operations | With unsupported features | Result |
| --- | --- | --- | --- |
| Swagger Petstore | 19 | 0 | compatible |
| GitHub REST API | 1242 | 433 | inconclusive (820 unknown findings) |
| Meilisearch | — | — | rejected: OpenAPI 3.1 |

Unsupported keywords in the GitHub snapshot, by number of diagnostics:

| Keyword | Count |
| --- | --- |
| `oneOf` | 549 |
| `uniqueItems` | 103 |
| `anyOf` | 99 |
| `allOf` | 54 |
| `discriminator` + `oneOf` | 7 |
| `maxProperties` | 6 |
| `minProperties` | 2 |

Schema composition dominates. Until `allOf`/`oneOf`/`anyOf` are modelled, about
a third of GitHub's operations can only be reported as inconclusive, which is
the intended conservative outcome rather than a silent pass.

## Mutation tests

`tests/test_corpus.py` edits the real contracts and checks the verdict:

| Mutation | Expected |
| --- | --- |
| Petstore `POST /pet`: new required query parameter | breaking |
| Petstore `POST /pet`: new optional query parameter | compatible |
| Petstore `POST /pet`: OAuth requirement gains the `write:pets` scope | breaking |
| Petstore `GET /pet/{petId}`: path parameter gets `minimum: 1` | breaking |
| GitHub `/licenses`: `key` no longer required in the response schema | breaking |
| GitHub `/licenses`: `per_page` maximum lowered to 50 | breaking |

The GitHub cases use a projection of the snapshot that keeps only the
`/licenses` path. The full document is inconclusive because of composition in
other operations, so the projection is what makes a definite verdict possible.

## Timings

Median of three runs per document, read + normalise + self-compare, measured
with checker 0.2.0 on Python 3.12 / Windows:

| Snapshot | Median |
| --- | --- |
| Swagger Petstore | 0.05 s |
| Meilisearch (rejected at version check) | 0.007 s |
| GitHub REST API | 1.14 s |

These numbers are machine-dependent and only meant as an order of magnitude.
The raw output is in [`corpus-report.json`](corpus-report.json).

## Reproduce

```sh
python -m pip install -e ".[dev]"
python -m unittest tests.test_corpus -v
python scripts/corpus_report.py --repeat 3 --output docs/corpus-report.json
```

No step downloads anything; all inputs are in the repository.
