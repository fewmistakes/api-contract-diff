"""Optional YAML input with JSON scalar rules and explicit resource limits."""

import re


def decode(text: str, parse_float):
    try:
        import yaml
    except ImportError as exc:
        raise ValueError(
            'YAML input requires PyYAML; from a source checkout: pip install ".[yaml]"'
        ) from exc

    class Loader(yaml.SafeLoader):
        def __init__(self, stream):
            super().__init__(stream)
            self.depth = 0
            self.nodes = 0

        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValueError("YAML aliases and merge keys are not supported")
            self.nodes += 1
            self.depth += 1
            try:
                if self.nodes > 100_000 or self.depth > 64:
                    raise ValueError("YAML node/depth limit exceeded")
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str):
                    raise ValueError(
                        "YAML mapping keys must be strings; quote response status codes"
                    )
                if key in result:
                    raise ValueError(f"duplicate YAML key: {key!r}")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    # Do not inherit YAML 1.1's implicit dates or yes/no/on/off booleans.
    Loader.yaml_implicit_resolvers = {}
    Loader.yaml_constructors = {
        tag: constructor
        for tag, constructor in Loader.yaml_constructors.items()
        if tag is None
        or tag
        in {
            "tag:yaml.org,2002:" + name
            for name in ("null", "bool", "int", "float", "str", "seq", "map")
        }
    }
    Loader.add_implicit_resolver(
        "tag:yaml.org,2002:null", re.compile(r"^(?:null|Null|NULL|~|)$"), ["n", "N", "~", ""]
    )
    Loader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
        list("tTfF"),
    )
    Loader.add_implicit_resolver(
        "tag:yaml.org,2002:int", re.compile(r"^-?(?:0|[1-9][0-9]*)$"), list("-0123456789")
    )
    Loader.add_implicit_resolver(
        "tag:yaml.org,2002:float",
        re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$"),
        list("-0123456789"),
    )
    Loader.add_constructor("tag:yaml.org,2002:int", lambda loader, node: int(node.value))
    Loader.add_constructor("tag:yaml.org,2002:float", lambda loader, node: parse_float(node.value))
    loader = Loader(text)
    try:
        return loader.get_single_data()
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    finally:
        loader.dispose()
