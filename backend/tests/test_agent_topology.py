from langgraph.checkpoint.memory import InMemorySaver

from fleet_copilot.agent.graph import build_graph
from fleet_copilot.agent.llm import ScriptedLLM
from fleet_copilot.agent.tools import TOOLS

STAGES = {
    "__start__",
    "scope",
    "plan",
    "reference",
    "act",
    "tools",
    "collect",
    "compose",
    "explain",
    "verify",
    "respond",
    "__end__",
}
EDGES = {
    ("__start__", "scope"),
    ("scope", "plan"),
    ("plan", "reference"),
    ("plan", "act"),
    ("plan", "respond"),
    ("reference", "act"),
    ("reference", "respond"),
    ("act", "tools"),
    ("act", "compose"),
    ("act", "respond"),
    ("tools", "collect"),
    ("collect", "act"),
    ("compose", "explain"),
    ("compose", "respond"),
    ("explain", "verify"),
    ("verify", "respond"),
    ("respond", "__end__"),
}


def test_the_graph_has_exactly_the_documented_stages_and_transitions():
    graph = build_graph(ScriptedLLM(), InMemorySaver()).get_graph()
    assert set(graph.nodes) == STAGES
    assert {(edge.source, edge.target) for edge in graph.edges} == EDGES


def test_the_only_way_back_into_the_model_after_tools_is_the_bounded_act_stage():
    # `act` is the only node that can reach `tools`, and `tools` only leads to `collect`, then `act`.
    into_tools = {src for src, dst in EDGES if dst == "tools"}
    out_of_tools = {dst for src, dst in EDGES if src == "tools"}
    assert into_tools == {"act"} and out_of_tools == {"collect"}


FORBIDDEN_PARAMETERS = {
    "company_id",
    "company",
    "tenant",
    "tenant_id",
    "actor",
    "actor_id",
    "runtime",
}


def test_no_model_facing_tool_schema_names_a_tenant_actor_or_the_runtime():
    for tool in TOOLS:
        properties = set(tool.tool_call_schema.model_json_schema()["properties"])
        assert not properties & FORBIDDEN_PARAMETERS, tool.name
