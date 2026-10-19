# Design decisions

## One inclusion algorithm, two directions

Let `L(S)` be the values admitted by a supported schema. Requests need
`L(old) ⊆ L(new)`; responses need `L(new) ⊆ L(old)`. The operation layer chooses
the argument order for one inclusion algorithm.

The algorithm returns reasons for failed inclusion, an empty list for a proven
inclusion, or `None` when it cannot decide. The CLI and report policy live outside
this function. Engine code does not print, write files, or exit the process.

## Constraint interactions

Numeric intervals use decimal endpoints. Integer intervals are rounded to their
effective inclusive integer endpoints. Incrementing endpoints uses Python
integers, so large bounds are not rounded through Decimal's precision context.
`int32` and `int64` compile to bounded integer schemas before comparison.

Enums are finite value sets intersected with type, nullability, and bounds.
Booleans and integers remain distinct even though Python considers `True == 1`.
Empty numeric domains, singleton numeric intervals, an empty-string singleton,
and arrays whose only possible value is `[]` have regression tests.

For objects, each named property gets its effective schema: the named definition
or the applicable `additionalProperties` rule. Remaining unnamed keys are
checked separately. Requiredness concerns presence and is independent of the
schema of a present value.

## Opaque predicates versus unsupported syntax

Known predicates such as a nonnumeric `format` or `pattern` can remain opaque.
If both sides apply the same predicate `P`, a proof that `A ⊆ B` implies
`A ∩ P ⊆ B ∩ P`. But a counterexample in `A` might be excluded by `P`.
The checker preserves a successful proof and turns an unproven case into unknown.
It does not execute regular expressions or claim to implement UUID/date
validators. XML metadata is conservatively handled the same way; changes to it
require review.

An unsupported construct such as `oneOf` is different: the loader cannot
construct its semantics. It records a diagnostic even when unchanged. A private
marker prevents comparison from treating that node as an unrestricted schema.
Independent changes remain visible while the overall status stays inconclusive.
Some obvious breaks involving opaque predicates can therefore be reported as
inconclusive; this is a deliberate false-positive avoidance policy.

## Request and response projection

`readOnly` properties are unavailable to requests; `writeOnly` properties are
unavailable in responses. The projected property is forbidden, and its name is
removed from that direction's required set. References are resolved before
projection. A property marked both ways is rejected.

This is an explicit client-facing policy, stricter than treating OpenAPI's
SHOULD NOT wording as a validation no-op. It prevents a required server-generated
ID from becoming an apparent request requirement, while a property becoming
unavailable to existing clients can still be breaking.

## Authentication is a logical expression

A security list is an OR of alternatives. Inside one alternative, schemes and
their OAuth scopes are ANDed. Existing credentials remain usable if every old
alternative implies at least one new alternative. This catches a removed login
option, a new simultaneous API-key requirement, and a newly required OAuth scope.

An empty list or alternative permits unauthenticated requests. Operation security
replaces root security. Scheme names are references, so renaming a scheme with
the same credential definition is not itself a wire change. Header names and
HTTP scheme names are normalized for case; query and cookie key names remain
case-sensitive. Scope descriptions are documentation.

Changed token/discovery URLs or credential placement need review when remaining
alternatives do not already preserve old credentials. The checker does not
contact issuers, validate tokens, or infer resource permissions.

## Response dispatch

The response mapping is evaluated over statuses 100 through 599. The winning
rule is explicit code, then range, then default. Identical rule pairs are grouped,
so adding a default response does not create hundreds of duplicate findings.
An explicit response already covered by default is compared to its previous
effective schema instead of automatically called a new unhandled response.

## Conservative HTTP policies

| Change | Result | Reason |
| --- | --- | --- |
| Remove an operation | Breaking | Existing calls lose their target |
| Add an operation | Compatible | Previous operations remain described |
| Add required parameter/body | Breaking | Existing calls may omit it |
| Add optional parameter/body | Compatible | Existing calls may omit it |
| Remove parameter/body support | Breaking | Continued acceptance is no longer promised |
| Add uncovered response statuses | Breaking | Existing clients may not handle them |
| Remove possible response statuses | Compatible | Output possibilities shrink |
| Remove media type | Breaking | Existing requests or Accept choices lose support |
| Add request media type | Compatible | Accepted input possibilities grow |
| Add response media type | Breaking | Output possibilities grow |
| Remove response header | Breaking | Clients may depend on its documented availability |
| Add response header | Compatible | Existing clients can ignore it |

Header removal is conservative even when the old header was not required.
The checker does not infer route equivalence, shadowing, content negotiation,
or which inputs trigger which statuses. Changing behavior while retaining
the same schemas can escape a contract comparison.

## Input and resource boundaries

Same-document references use a branch-local stack. Reuse in separate fields
works; back-edges require review. Remote/file references are never fetched.
File size, expansion count, and nesting are bounded. The 0.1 expansion budget
rejected the real GitHub document; 0.2 raises it to 200,000 nodes per document
and retains tests for budget exhaustion.

YAML remains optional. Its loader uses JSON-compatible tags, explicit scalar
resolvers, duplicate-key rejection, and alias rejection. It does not mutate
PyYAML's global loader configuration. Missing dependencies and invalid YAML
produce structured input errors, not import tracebacks.

The normalizer is not a complete OpenAPI validator. Run specification validation
before compatibility checks. OpenAPI 3.1 changes the schema language: replacing
its version string with 3.0 would not be a conversion. The Meilisearch snapshot
is retained as a rejection regression for that reason.

## Verification

Example tests establish diagnostics. Independent `jsonschema` checks guard
inclusion against generated nested schemas and values. Finite integer intervals
and authentication alternatives are checked exhaustively in bounded models.
This is not a formal proof of the full language. Pinned upstream snapshots and
explicit mutations test integration without live services. See [validation](validation.md).

## References

Input semantics come from the OpenAPI 3.0.4
[Schema Object](https://spec.openapis.org/oas/v3.0.4.html#schema-object),
[Responses Object](https://spec.openapis.org/oas/v3.0.4.html#responses-object),
[Security Requirement Object](https://spec.openapis.org/oas/v3.0.4.html#security-requirement-object),
and [Reference Object](https://spec.openapis.org/oas/v3.0.4.html#reference-object).
The compatibility policies above are this project's choices.
