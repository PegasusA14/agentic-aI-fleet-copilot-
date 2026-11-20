"""Helpers for graph and scenario tests: a service wired to a scripted model and the real dataset."""

from __future__ import annotations

from itertools import count

from langgraph.checkpoint.memory import InMemorySaver

from fleet_copilot.agent.identity import DEMO_IDENTITIES
from fleet_copilot.agent.llm import Explanation, Plan, Reference, ScriptedLLM
from fleet_copilot.agent.service import AgentService, TurnResult

ACME_ADMIN = DEMO_IDENTITIES["admin-acme"]
GLOBEX_ADMIN = DEMO_IDENTITIES["admin-globex"]
INITECH_ADMIN = DEMO_IDENTITIES["admin-initech"]


def make_service(db_path, llm: ScriptedLLM, saver=None, **kwargs) -> AgentService:
    ids = count(1)
    return AgentService(
        db_path=db_path,
        gateway=llm,
        checkpointer=saver or InMemorySaver(),
        trace_id_factory=lambda: f"t{next(ids)}",
        **kwargs,
    )


def answer_plan(summary="look it up", **kw) -> Plan:
    return Plan(intent="answer", summary=summary, **kw)


def follow_up_plan(kind="ordinal", index=1, list_hint=None, device_ref=None, **kw) -> Plan:
    ref = Reference(
        kind=kind,
        index=index if kind == "ordinal" else None,
        list_hint=list_hint,
        device_ref=device_ref,
    )
    return Plan(intent="answer", summary="follow-up", reference=ref, **kw)


def explanation(headline="Two of 10 devices are low on disk", text=None) -> Explanation:
    return Explanation(headline=headline, explanation=text)


def kinds(result: TurnResult) -> list[str]:
    return [event.kind for event in result.trace]


def golden(tenant: str) -> dict:
    """The oracle's pinned answers for one tenant (never hand-typed in tests)."""
    import json

    from fleet_copilot.config import REPO_ROOT

    path = REPO_ROOT / "evals" / "golden" / "golden.json"
    return json.loads(path.read_text(encoding="utf-8"))["tenants"][tenant]


def listed_ids(envelope) -> list[str]:
    return [item.device_id for entity_list in envelope.entity_lists for item in entity_list.items]


def assert_clean(result, llm: ScriptedLLM) -> None:
    """The turn used exactly the scripted model responses and recorded no error step."""
    assert "error" not in kinds(result), [e.data for e in result.trace if e.kind == "error"]
    assert llm.remaining == {"plan": 0, "act": 0, "explain": 0}, llm.remaining
