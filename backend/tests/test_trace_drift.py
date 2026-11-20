"""A tool argument value the trace layer does not know would show as "applied". Catch that here."""

from fleet_copilot.agent.tools import TOOLS
from fleet_copilot.agent.trace import _ENUMERATED, SAFE_ARG_KEYS


def _enum_values(spec):
    found = set(spec.get("enum", []))
    for branch in spec.get("anyOf", []):
        found |= _enum_values(branch)
    return found


def _is_enumerated_or_numeric(spec):
    if "enum" in spec or spec.get("type") in ("integer", "number"):
        return True
    return any(_is_enumerated_or_numeric(branch) for branch in spec.get("anyOf", []))


def _properties():
    for tool in TOOLS:
        for key, spec in tool.tool_call_schema.model_json_schema().get("properties", {}).items():
            yield tool.name, key, spec


def test_every_enumerated_tool_value_can_be_traced():
    for tool, key, spec in _properties():
        if key in _ENUMERATED:
            missing = _enum_values(spec) - _ENUMERATED[key]
            assert not missing, (
                f"{tool}.{key} accepts {sorted(missing)} but traces would show 'applied'"
            )


def test_every_enumerated_or_numeric_tool_argument_has_a_trace_rule():
    for tool, key, spec in _properties():
        if _is_enumerated_or_numeric(spec):
            assert key in SAFE_ARG_KEYS, (
                f"{tool}.{key} is enumerated or numeric but has no trace rule"
            )
