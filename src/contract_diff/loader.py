import json
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .model import Body, Contract, Finding, Operation, Parameter, Response, Schema
from .schema import accepts_scalar, equal_value, value_type
from .security import Security, canonical_scheme

METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
ANNOTATIONS = frozenset(
    {"title", "description", "default", "example", "deprecated", "externalDocs"}
)
SCHEMA_FIELDS = frozenset(
    {
        "type",
        "nullable",
        "enum",
        "properties",
        "required",
        "items",
        "additionalProperties",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "format",
        "pattern",
        "xml",
        "readOnly",
        "writeOnly",
    }
)
MAX_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 64
MAX_SCHEMA_NODES = 200_000


class SpecError(ValueError):
    """The input cannot be interpreted as a supported OpenAPI document."""


class _UnsupportedRef(Exception):
    pass


def pointer(*parts: str) -> str:
    return "#/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecError(f"{location}: expected an object")
    return value


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{location}: expected a boolean")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SpecError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SpecError("JSON numbers must be finite")
    if Decimal(value) != Decimal(str(result)):
        raise SpecError("JSON decimal cannot be represented without precision loss")
    return result


def _constant(value: str) -> Any:
    raise SpecError(f"invalid JSON number: {value}")


def load(path: str | Path, *, label: str | None = None) -> Contract:
    """Read a local UTF-8 JSON file. References never trigger file or network I/O."""
    path = Path(path)
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise SpecError(f"input exceeds {MAX_BYTES // (1024 * 1024)} MiB")
        text = content.decode("utf-8-sig")
        if path.suffix.lower() in {".yaml", ".yml"}:
            from .yaml_support import decode

            document = decode(text, _float)
        else:
            document = json.loads(
                text, object_pairs_hook=_pairs, parse_float=_float, parse_constant=_constant
            )
        return parse(document, label=label or str(path))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise SpecError(f"{path}: {exc}") from exc


def parse(document: Any, *, label: str = "input") -> Contract:
    """Parse an already decoded document; useful for embedding and tests."""
    return _Parser(_object(document, "#"), label).parse()


class _Parser:
    def __init__(self, document: dict[str, Any], label: str):
        self.document = document
        self.label = label
        self.contract = Contract()
        self.operation = "document"
        self.schema_nodes = 0

    def unknown(self, location: str, message: str) -> None:
        self.contract.diagnostics.append(
            Finding(
                "unknown",
                "analysis.unsupported",
                self.operation,
                location,
                f"{self.label}: {message}",
            )
        )

    def fields(self, obj: dict[str, Any], allowed: set[str] | frozenset[str], loc: str) -> None:
        extra = sorted(key for key in obj if key not in allowed and not key.startswith("x-"))
        if extra:
            self.unknown(loc, f"Unsupported fields: {', '.join(extra)}.")

    def resolve(
        self, raw: Any, loc: str, seen: frozenset[str] = frozenset()
    ) -> tuple[dict[str, Any], frozenset[str]]:
        obj = _object(raw, loc)
        while "$ref" in obj:
            ref = obj["$ref"]
            if not isinstance(ref, str):
                raise SpecError(f"{loc}: $ref must be a string")
            if not ref.startswith("#/"):
                raise _UnsupportedRef(
                    f"Only same-document JSON Pointer references are supported: {ref!r}."
                )
            if ref in seen:
                raise _UnsupportedRef(f"Recursive reference requires review: {ref!r}.")
            if len(seen) >= MAX_DEPTH:
                raise _UnsupportedRef(f"Reference chain exceeds {MAX_DEPTH} levels.")
            seen = seen | {ref}
            target: Any = self.document
            for token in unquote(ref[2:]).split("/"):
                if re.search(r"~(?![01])", token):
                    raise SpecError(f"{loc}: invalid JSON Pointer escape in {ref!r}")
                token = token.replace("~1", "/").replace("~0", "~")
                try:
                    if isinstance(target, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
                        target = target[int(token)]
                    else:
                        target = target[token]
                except (KeyError, IndexError, TypeError) as exc:
                    raise SpecError(f"{loc}: unresolved reference {ref!r}") from exc
            # OAS 3.0 Reference Object siblings do not override the target.
            obj = _object(target, loc)
        return obj, seen

    def resolved(self, raw: Any, loc: str) -> dict[str, Any] | None:
        try:
            return self.resolve(raw, loc)[0]
        except _UnsupportedRef as exc:
            self.unknown(loc, str(exc))
            return None

    def schema(
        self,
        raw: Any,
        loc: str,
        seen: frozenset[str] = frozenset(),
        depth: int = 0,
        *,
        request: bool = False,
        as_property: bool = False,
    ) -> Schema | bool:
        self.schema_nodes += 1
        if self.schema_nodes > MAX_SCHEMA_NODES:
            raise SpecError(f"{loc}: expanded schema exceeds {MAX_SCHEMA_NODES} nodes")
        if depth > MAX_DEPTH:
            self.unknown(loc, f"Schema nesting exceeds {MAX_DEPTH} levels.")
            return {"_unsupported": True}
        try:
            obj, seen = self.resolve(raw, loc, seen)
        except _UnsupportedRef as exc:
            self.unknown(loc, str(exc))
            return {"_unsupported": True}
        read_only = _boolean(obj.get("readOnly", False), loc + "/readOnly")
        write_only = _boolean(obj.get("writeOnly", False), loc + "/writeOnly")
        if read_only and write_only:
            raise SpecError(f"{loc}: a property cannot be both readOnly and writeOnly")
        if as_property and (read_only if request else write_only):
            return False
        result: Schema = {}
        unsupported = sorted(
            key
            for key in obj
            if key not in SCHEMA_FIELDS | ANNOTATIONS
            and not key.startswith("x-")
            and not (key in {"readOnly", "writeOnly"} and obj[key] is False)
        )
        if unsupported:
            self.unknown(loc, f"Unsupported schema keywords: {', '.join(unsupported)}.")
            result["_unsupported"] = True
        if "type" in obj:
            if obj["type"] not in ("string", "integer", "number", "boolean", "object", "array"):
                raise SpecError(f"{loc}: type must be one OpenAPI 3.0 type name")
            result["type"] = obj["type"]
        nullable = _boolean(obj.get("nullable", False), loc + "/nullable")
        if nullable and "type" in obj:
            result["nullable"] = True
        if "enum" in obj:
            values = obj["enum"]
            if not isinstance(values, list) or not values:
                raise SpecError(f"{loc}: enum must be a nonempty array")
            if any(value_type(value) in {"object", "array"} for value in values):
                self.unknown(loc, "Object and array enum values require review.")
                result["_unsupported"] = True
            else:
                for index, value in enumerate(values):
                    if not accepts_scalar(result, value):
                        raise SpecError(f"{loc}: enum value {value!r} conflicts with type/nullable")
                    if any(equal_value(value, earlier) for earlier in values[:index]):
                        raise SpecError(f"{loc}: enum contains duplicate values")
                result["enum"] = sorted(values, key=lambda value: json.dumps(value, sort_keys=True))
        self.constraints(obj, result, loc)
        if "properties" in obj:
            props = _object(obj["properties"], loc + "/properties")
            result["properties"] = {
                name: self.schema(
                    value,
                    loc + "/properties/" + pointer(name)[2:],
                    seen,
                    depth + 1,
                    request=request,
                    as_property=True,
                )
                for name, value in sorted(props.items())
            }
        if "required" in obj:
            required = obj["required"]
            if not isinstance(required, list) or any(
                not isinstance(name, str) for name in required
            ):
                raise SpecError(f"{loc}: required must be an array of property names")
            if len(set(required)) != len(required):
                raise SpecError(f"{loc}: required contains duplicate names")
            if required:
                projected = [
                    name for name in required if result.get("properties", {}).get(name) is not False
                ]
                if projected:
                    result["required"] = sorted(projected)
        extra = obj.get("additionalProperties", True)
        if isinstance(extra, dict):
            extra = self.schema(
                extra, loc + "/additionalProperties", seen, depth + 1, request=request
            )
        elif not isinstance(extra, bool):
            raise SpecError(f"{loc}: additionalProperties must be a boolean or schema")
        if extra is not True:
            result["additionalProperties"] = extra
        if extra is False and set(result.get("required", [])) - result.get("properties", {}).keys():
            raise SpecError(f"{loc}: required property is forbidden by additionalProperties")
        if "items" in obj:
            result["items"] = self.schema(
                obj["items"], loc + "/items", seen, depth + 1, request=request
            )
        elif obj.get("type") == "array":
            raise SpecError(f"{loc}: an array schema requires items")
        return result

    def constraints(self, obj: dict, result: Schema, loc: str) -> None:
        for name in ("minimum", "maximum"):
            if name in obj:
                value = obj[name]
                if type(value) not in (int, float) or (
                    isinstance(value, float) and not math.isfinite(value)
                ):
                    raise SpecError(f"{loc}/{name}: expected a finite number")
                result[name] = value
        for name, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
            if name in obj:
                enabled = _boolean(obj[name], loc + "/" + name)
                if enabled and bound not in obj:
                    raise SpecError(f"{loc}/{name}: requires {bound}")
                if enabled:
                    result[name] = True
        for suffix in ("Length", "Items"):
            for prefix in ("min", "max"):
                name = prefix + suffix
                if name in obj:
                    if type(obj[name]) is not int or obj[name] < 0:
                        raise SpecError(f"{loc}/{name}: expected a nonnegative integer")
                    result[name] = obj[name]
            if result.get("min" + suffix, 0) > result.get("max" + suffix, float("inf")):
                raise SpecError(f"{loc}: contradictory {suffix.lower()} bounds are not supported")
        for name in ("format", "pattern"):
            if name in obj:
                if not isinstance(obj[name], str):
                    raise SpecError(f"{loc}/{name}: expected a string")
                result[name] = obj[name]
        if "xml" in obj:
            result["xml"] = _object(obj["xml"], loc + "/xml")
        if obj.get("type") == "integer" and obj.get("format") in ("int32", "int64"):
            bits = int(obj["format"][3:])
            low, high = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
            if result.get("minimum", float("-inf")) < low:
                result["minimum"] = low
                result.pop("exclusiveMinimum", None)
            if result.get("maximum", float("inf")) > high:
                result["maximum"] = high
                result.pop("exclusiveMaximum", None)
            result.pop("format", None)

    def parameters(self, raw: Any, loc: str) -> dict[tuple[str, str], Parameter]:
        if not isinstance(raw, list):
            raise SpecError(f"{loc}: parameters must be an array")
        result = {}
        for index, value in enumerate(raw):
            item_loc = f"{loc}/{index}"
            obj = self.resolved(value, item_loc)
            if obj is None:
                continue
            self.fields(
                obj,
                {
                    "name",
                    "in",
                    "required",
                    "schema",
                    "description",
                    "deprecated",
                    "example",
                    "examples",
                    "style",
                    "explode",
                    "allowReserved",
                    "allowEmptyValue",
                },
                item_loc,
            )
            name, place = obj.get("name"), obj.get("in")
            if (
                not isinstance(name, str)
                or not name
                or place not in ("query", "header", "path", "cookie")
            ):
                raise SpecError(f"{item_loc}: parameter requires name and a valid in field")
            key = (place, name.lower() if place == "header" else name)
            if key in {
                ("header", "accept"),
                ("header", "content-type"),
                ("header", "authorization"),
            }:
                continue
            if key in result:
                raise SpecError(f"{loc}: duplicate parameter {key!r}")
            required = _boolean(obj.get("required", False), item_loc + "/required")
            if place == "path" and not required:
                raise SpecError(f"{item_loc}: path parameters must be required")
            if obj.get("allowEmptyValue", False):
                self.unknown(item_loc, "allowEmptyValue requires review.")
            style = obj.get("style", "form" if place in ("query", "cookie") else "simple")
            if not isinstance(style, str):
                raise SpecError(f"{item_loc}: style must be a string")
            explode = _boolean(obj.get("explode", style == "form"), item_loc + "/explode")
            reserved = _boolean(obj.get("allowReserved", False), item_loc + "/allowReserved")
            if "schema" not in obj:
                self.unknown(item_loc, "Parameter without a schema requires review.")
            result[key] = Parameter(
                required,
                self.schema(
                    obj.get("schema", {"unsupported": True}), item_loc + "/schema", request=True
                ),
                (style, explode, reserved),
            )
        return result

    def content(self, raw: Any, loc: str, *, request: bool = False) -> dict[str, Schema]:
        result = {}
        for media, value in sorted(_object(raw, loc).items()):
            item_loc = loc + "/" + pointer(media)[2:]
            obj = _object(value, item_loc)
            self.fields(obj, {"schema", "example", "examples"}, item_loc)
            if "*" in media:
                self.unknown(item_loc, "Media type wildcards require review.")
            result[media] = self.schema(
                obj.get("schema", {}),
                item_loc + "/schema",
                request=request,
            )
        return result

    def parse(self) -> Contract:
        version = self.document.get("openapi")
        if not isinstance(version, str) or not re.fullmatch(r"3\.0\.[0-9]+", version):
            raise SpecError("#: expected OpenAPI 3.0.x; 3.1/3.2 and Swagger 2 are not supported")
        info = _object(self.document.get("info"), "#/info")
        if any(not isinstance(info.get(key), str) for key in ("title", "version")):
            raise SpecError("#/info: title and version must be strings")
        self.fields(
            self.document,
            {
                "openapi",
                "info",
                "paths",
                "components",
                "servers",
                "security",
                "tags",
                "externalDocs",
            },
            "#",
        )
        paths = _object(self.document.get("paths"), "#/paths")
        for path, value in sorted(paths.items()):
            if path.startswith("x-"):
                continue
            if not path.startswith("/"):
                raise SpecError(f"#/paths: path must start with '/': {path!r}")
            path_loc = pointer("paths", path)
            self.operation = path
            raw_item = _object(value, path_loc)
            if "$ref" in raw_item:
                self.unknown(path_loc, "Path Item references require review.")
                continue
            self.fields(
                raw_item, METHODS | {"parameters", "servers", "summary", "description"}, path_loc
            )
            for method in sorted(METHODS & raw_item.keys()):
                self.operation = f"{method.upper()} {path}"
                loc = path_loc + "/" + method
                obj = _object(raw_item[method], loc)
                self.fields(
                    obj,
                    {
                        "parameters",
                        "requestBody",
                        "responses",
                        "security",
                        "servers",
                        "tags",
                        "summary",
                        "description",
                        "operationId",
                        "deprecated",
                        "externalDocs",
                        "callbacks",
                    },
                    loc,
                )
                if obj.get("callbacks"):
                    self.unknown(loc + "/callbacks", "Callbacks require review.")
                security = self.security(
                    obj.get("security", self.document.get("security", [])), loc + "/security"
                )
                params = self.parameters(raw_item.get("parameters", []), path_loc + "/parameters")
                params.update(self.parameters(obj.get("parameters", []), loc + "/parameters"))
                for name in re.findall(r"\{([^{}]+)\}", path):
                    if ("path", name) not in params:
                        self.unknown(loc, f"Path parameter {name!r} is not available for analysis.")
                body = None
                if "requestBody" in obj:
                    body_loc = loc + "/requestBody"
                    body_obj = self.resolved(obj["requestBody"], body_loc)
                    if body_obj is not None:
                        self.fields(body_obj, {"description", "required", "content"}, body_loc)
                        body = Body(
                            _boolean(body_obj.get("required", False), body_loc + "/required"),
                            self.content(
                                body_obj.get("content"), body_loc + "/content", request=True
                            ),
                        )
                        if not body.content:
                            raise SpecError(f"{body_loc}: request body content must not be empty")
                responses = {}
                raw_responses = _object(obj.get("responses"), loc + "/responses")
                for status, value in sorted(raw_responses.items()):
                    if status.startswith("x-"):
                        continue
                    response_loc = loc + "/responses/" + status
                    if not re.fullmatch(r"[1-5](?:[0-9]{2}|XX)|default", status):
                        raise SpecError(f"{response_loc}: invalid response status key")
                    response_obj = self.resolved(value, response_loc)
                    responses[status] = Response({}, {})
                    if response_obj is None:
                        continue
                    self.fields(
                        response_obj, {"description", "content", "headers", "links"}, response_loc
                    )
                    if not isinstance(response_obj.get("description"), str):
                        raise SpecError(f"{response_loc}: response description must be a string")
                    if response_obj.get("links"):
                        self.unknown(response_loc + "/links", "Response links require review.")
                    responses[status] = Response(
                        self.content(response_obj.get("content", {}), response_loc + "/content"),
                        self.headers(response_obj.get("headers", {}), response_loc + "/headers"),
                    )
                if not responses:
                    raise SpecError(f"{loc}: at least one response is required")
                servers = obj.get(
                    "servers", raw_item.get("servers", self.document.get("servers", [{"url": "/"}]))
                )
                self.contract.operations[(path, method)] = Operation(
                    params,
                    body,
                    responses,
                    servers,
                    security,
                )
        return self.contract

    def headers(self, raw: Any, loc: str) -> dict[str, Parameter]:
        headers = {}
        for name, value in sorted(_object(raw, loc).items()):
            key = name.lower()
            if key == "content-type":
                continue
            header_loc = loc + "/" + pointer(name)[2:]
            obj = self.resolved(value, header_loc)
            if obj is None:
                continue
            if key in headers:
                raise SpecError(f"{loc}: duplicate response header {name!r}")
            self.fields(
                obj,
                {
                    "schema",
                    "description",
                    "required",
                    "deprecated",
                    "example",
                    "examples",
                    "style",
                    "explode",
                },
                header_loc,
            )
            if obj.get("style", "simple") != "simple":
                self.unknown(header_loc, "Only simple response header serialization is supported.")
            headers[key] = Parameter(
                _boolean(obj.get("required", False), header_loc + "/required"),
                self.schema(obj.get("schema", {}), header_loc + "/schema"),
                ("simple", _boolean(obj.get("explode", False), header_loc + "/explode"), False),
            )
        return headers

    def security(self, raw: Any, loc: str) -> Security:
        if not isinstance(raw, list):
            raise SpecError(f"{loc}: security must be an array")
        requirements = set()
        definitions = {}
        schemes = _object(self.document.get("components", {}), "#/components").get(
            "securitySchemes", {}
        )
        schemes = _object(schemes, "#/components/securitySchemes")
        for index, alternative in enumerate(raw):
            requirement = []
            for name, scopes in _object(alternative, f"{loc}/{index}").items():
                if not isinstance(scopes, list) or any(
                    not isinstance(scope, str) for scope in scopes
                ):
                    raise SpecError(f"{loc}: required scopes must be an array of strings")
                if name not in schemes:
                    raise SpecError(f"{loc}: undefined security scheme {name!r}")
                definition_loc = pointer("components", "securitySchemes", name)
                definition = self.resolved(schemes[name], definition_loc)
                if definition is None:
                    definition = {"type": "unsupported"}
                kind = definition.get("type")
                self.fields(
                    definition,
                    {
                        "type",
                        "description",
                        "name",
                        "in",
                        "scheme",
                        "bearerFormat",
                        "flows",
                        "openIdConnectUrl",
                    },
                    definition_loc,
                )
                if kind == "apiKey":
                    if definition.get("in") not in ("query", "header", "cookie") or not isinstance(
                        definition.get("name"), str
                    ):
                        raise SpecError(f"{definition_loc}: apiKey requires name and valid in")
                    if definition["in"] == "header":
                        definition = {**definition, "name": definition["name"].lower()}
                elif kind == "http":
                    if not isinstance(definition.get("scheme"), str):
                        raise SpecError(f"{definition_loc}: HTTP security requires scheme")
                    definition = {**definition, "scheme": definition["scheme"].lower()}
                elif kind == "oauth2":
                    flows = _object(definition.get("flows"), definition_loc + "/flows")
                    available = set()
                    for flow_name, flow in flows.items():
                        flow = _object(flow, definition_loc + "/flows/" + flow_name)
                        flow_loc = definition_loc + "/flows/" + flow_name
                        self.fields(
                            flow, {"authorizationUrl", "tokenUrl", "refreshUrl", "scopes"}, flow_loc
                        )
                        if flow_name not in {
                            "implicit",
                            "password",
                            "clientCredentials",
                            "authorizationCode",
                        }:
                            self.unknown(definition_loc, f"Unsupported OAuth flow: {flow_name}.")
                        urls = []
                        if flow_name in {"implicit", "authorizationCode"}:
                            urls.append("authorizationUrl")
                        if flow_name in {"password", "clientCredentials", "authorizationCode"}:
                            urls.append("tokenUrl")
                        if "refreshUrl" in flow:
                            urls.append("refreshUrl")
                        if any(not isinstance(flow.get(url), str) or not flow[url] for url in urls):
                            raise SpecError(f"{flow_loc}: missing or invalid OAuth endpoint URL")
                        scope_map = _object(flow.get("scopes"), flow_loc + "/scopes")
                        if any(
                            not isinstance(description, str) for description in scope_map.values()
                        ):
                            raise SpecError(f"{flow_loc}: scope descriptions must be strings")
                        available.update(scope_map)
                    if not set(scopes) <= available:
                        raise SpecError(f"{loc}: requirement references an undefined OAuth scope")
                elif kind == "openIdConnect":
                    if not isinstance(definition.get("openIdConnectUrl"), str):
                        raise SpecError(f"{definition_loc}: missing openIdConnectUrl")
                else:
                    self.unknown(definition_loc, f"Unsupported security scheme type: {kind!r}.")
                if kind not in {"oauth2", "openIdConnect"} and scopes:
                    raise SpecError(f"{loc}: only OAuth/OpenID requirements can declare scopes")
                definitions[name] = canonical_scheme(definition)
                requirement.append((name, frozenset(scopes)))
            requirements.add(frozenset(requirement))
        return Security(frozenset(requirements) or frozenset({frozenset()}), definitions)
