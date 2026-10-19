from .loader import pointer
from .model import Contract, Finding, Report, Schema, Severity
from .schema import same_schema, subset
from .security import compare_security


def response_groups(before: dict, after: dict) -> list[tuple[str | None, str | None, list[int]]]:
    """Exact status beats a range, which beats default; group identical lookups."""

    def lookup(responses, status):
        for key in (str(status), f"{status // 100}XX", "default"):
            if key in responses:
                return key
        return None

    groups: dict[tuple[str | None, str | None], list[int]] = {}
    for status in range(100, 600):
        old_key, new_key = lookup(before, status), lookup(after, status)
        if old_key is not None or new_key is not None:
            groups.setdefault((old_key, new_key), []).append(status)
    return [(old, new, statuses) for (old, new), statuses in groups.items()]


def compare(old: Contract, new: Contract) -> Report:
    """Check whether existing clients can keep using the new server contract."""
    findings = [*old.diagnostics, *new.diagnostics]

    def add(severity: Severity, code: str, location: str, message: str) -> None:
        findings.append(Finding(severity, code, operation, location, message))

    def schema_change(before: Schema, after: Schema, loc: str, *, request: bool) -> None:
        if same_schema(before, after):
            return
        reasons = subset(before, after) if request else subset(after, before)
        if reasons is None:
            add(
                "unknown",
                "schema.inconclusive",
                loc,
                "Changed schema involves an opaque predicate or unsupported construct; "
                "inclusion requires review.",
            )
            return
        subject = "Previously valid request" if request else "New response"
        if reasons:
            for reason in reasons:
                add("breaking", "schema.incompatible", loc, f"{subject}: {reason}")
        else:
            message = (
                "Request schema still accepts all previously valid values."
                if request
                else "New response values remain valid under the previous schema."
            )
            add("compatible", "schema.compatible", loc, message)

    def content_change(before: dict, after: dict, loc: str, *, request: bool) -> None:
        for media in sorted(before.keys() | after.keys()):
            media_loc = loc + "/" + pointer(media)[2:]
            if media not in after:
                add("breaking", "media.removed", media_loc, f"Media type removed: {media}.")
            elif media not in before:
                subject = "Accepted request" if request else "Possible response"
                add(
                    "compatible" if request else "breaking",
                    "media.added",
                    media_loc,
                    f"{subject} media type added: {media}.",
                )
            else:
                schema_change(before[media], after[media], media_loc + "/schema", request=request)

    for path, method in sorted(old.operations.keys() | new.operations.keys()):
        key = (path, method)
        operation = f"{method.upper()} {path}"
        loc = pointer("paths", path, method)
        if key not in new.operations:
            add("breaking", "operation.removed", loc, "Operation removed.")
            continue
        if key not in old.operations:
            add("compatible", "operation.added", loc, "Operation added.")
            continue
        before, after = old.operations[key], new.operations[key]
        security_change = compare_security(before.security, after.security)
        if security_change is not None:
            severity, message = security_change
            add(severity, "security.changed", loc + "/security", message)
        if before.servers != after.servers:
            add(
                "unknown",
                "servers.changed",
                loc + "/servers",
                "Effective server configuration changed.",
            )
        for place, name in sorted(before.parameters.keys() | after.parameters.keys()):
            param_key = (place, name)
            param_loc = f"{loc}/parameters[{place}:{name}]"
            if param_key not in after.parameters:
                add(
                    "breaking",
                    "parameter.removed",
                    param_loc,
                    f"Parameter removed: {place} {name}.",
                )
                continue
            new_param = after.parameters[param_key]
            if param_key not in before.parameters:
                requirement = "Required" if new_param.required else "Optional"
                add(
                    "breaking" if new_param.required else "compatible",
                    "parameter.added",
                    param_loc,
                    f"{requirement} parameter added: {place} {name}.",
                )
                continue
            old_param = before.parameters[param_key]
            if old_param.required != new_param.required:
                add(
                    "breaking" if new_param.required else "compatible",
                    "parameter.required",
                    param_loc,
                    f"Parameter is now {'required' if new_param.required else 'optional'}.",
                )
            if old_param.serialization != new_param.serialization:
                add(
                    "unknown",
                    "parameter.serialization",
                    param_loc,
                    "Parameter serialization changed.",
                )
            schema_change(old_param.schema, new_param.schema, param_loc + "/schema", request=True)
        body_loc = loc + "/requestBody"
        if before.body is None and after.body is not None:
            add(
                "breaking" if after.body.required else "compatible",
                "request_body.added",
                body_loc,
                f"{'Required' if after.body.required else 'Optional'} request body added.",
            )
        elif before.body is not None and after.body is None:
            add("breaking", "request_body.removed", body_loc, "Request body support removed.")
        elif before.body is not None and after.body is not None:
            if before.body.required != after.body.required:
                add(
                    "breaking" if after.body.required else "compatible",
                    "request_body.required",
                    body_loc,
                    f"Request body is now {'required' if after.body.required else 'optional'}.",
                )
            content_change(
                before.body.content, after.body.content, body_loc + "/content", request=True
            )
        for old_status, new_status, statuses in response_groups(before.responses, after.responses):
            response_loc = loc + "/responses/" + (new_status or old_status)
            label = (
                str(statuses[0])
                if len(statuses) == 1
                else f"{statuses[0]}..{statuses[-1]} ({len(statuses)} codes)"
            )
            if old_status != new_status and old_status is not None and new_status is not None:
                response_loc += f"[statuses:{label}]"
            if new_status is None:
                add(
                    "compatible",
                    "response.removed",
                    response_loc,
                    f"Response status removed: {label}.",
                )
            elif old_status is None:
                add(
                    "breaking",
                    "response.added",
                    response_loc,
                    f"New response status may be returned: {label}.",
                )
            else:
                content_change(
                    before.responses[old_status].content,
                    after.responses[new_status].content,
                    response_loc + "/content",
                    request=False,
                )
                old_headers = before.responses[old_status].headers
                new_headers = after.responses[new_status].headers
                for name in sorted(old_headers.keys() | new_headers.keys()):
                    header_loc = response_loc + "/headers/" + pointer(name)[2:]
                    if name not in new_headers:
                        add(
                            "breaking",
                            "response_header.removed",
                            header_loc,
                            f"Response header support removed: {name}.",
                        )
                    elif name not in old_headers:
                        add(
                            "compatible",
                            "response_header.added",
                            header_loc,
                            f"Response header added: {name}.",
                        )
                    else:
                        old_header, new_header = old_headers[name], new_headers[name]
                        if old_header.required != new_header.required:
                            requirement = "required" if new_header.required else "optional"
                            add(
                                "compatible" if new_header.required else "breaking",
                                "response_header.required",
                                header_loc,
                                f"Response header is now {requirement}.",
                            )
                        if old_header.serialization != new_header.serialization:
                            add(
                                "unknown",
                                "response_header.serialization",
                                header_loc,
                                "Response header serialization changed.",
                            )
                        schema_change(
                            old_header.schema,
                            new_header.schema,
                            header_loc + "/schema",
                            request=False,
                        )
    rank = {"breaking": 0, "unknown": 1, "compatible": 2}
    return Report(
        tuple(
            sorted(
                set(findings),
                key=lambda item: (
                    rank[item.severity],
                    item.operation,
                    item.location,
                    item.code,
                    item.message,
                ),
            )
        )
    )
