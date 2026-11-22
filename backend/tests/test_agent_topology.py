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
    "talk",
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
    ("plan", "talk"),  # a chat turn, or a refusal that code declined for lack of data
    ("reference", "act"),
    ("reference", "respond"),
    ("act", "tools"),
    ("act", "compose"),
    ("act", "respond"),
    ("tools", "collect"),
    ("collect", "act"),
    ("compose", "talk"),  # every composed turn passes the talk step; a repeat gets one short line
    ("compose", "respond"),
    ("talk", "verify"),
    ("verify", "respond"),
    ("respond", "__end__"),
}


def test_the_graph_has_exactly_the_documented_stages_and_transitions():
    graph = build_graph(ScriptedLLM(), InMemorySaver()).get_graph()
    assert set(graph.nodes) == STAGES
    assert {(edge.source, edge.target) for edge in graph.edges} == EDGES


def test_tools_are_entered_only_from_act_and_lead_back_only_through_collect_to_act():
    # Read from the compiled graph, so the claim holds for the real wiring, not just the constant above.
    edges = {
        (e.source, e.target) for e in build_graph(ScriptedLLM(), InMemorySaver()).get_graph().edges
    }
    assert {src for src, dst in edges if dst == "tools"} == {"act"}
    assert {dst for src, dst in edges if src == "tools"} == {"collect"}
    assert {dst for src, dst in edges if src == "collect"} == {"act"}


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


def test_the_talk_step_is_entered_only_from_plan_and_compose_and_leads_only_to_verify():
    edges = {
        (e.source, e.target) for e in build_graph(ScriptedLLM(), InMemorySaver()).get_graph().edges
    }
    assert {src for src, dst in edges if dst == "talk"} == {"plan", "compose"}
    assert {dst for src, dst in edges if src == "talk"} == {"verify"}
    # nothing reaches verify without passing the talk step, and verify is the only way to the response
    # for a composed answer
    assert {src for src, dst in edges if dst == "verify"} == {"talk"}
