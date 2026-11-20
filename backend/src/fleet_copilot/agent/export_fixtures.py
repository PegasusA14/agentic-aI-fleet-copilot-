"""Export real composer answers as replay fixtures for the frontend.

Each fixture is the output of the real tool layer and the deterministic composer, run as one tenant
over the real dataset: `execute()` -> `compose()` -> `Composition.to_envelope()`. Nothing is hand-written
data. The trace of every real (`tools`) scenario is the trace of a real scripted `AgentService` turn. A few outcomes cannot occur yet (there is no model, no proposal backend and no outage to observe),
so those are built from the same parts and flagged `synthetic` with a note saying why.

Run: `python -m fleet_copilot.agent.export_fixtures [out_dir]` (default `frontend/src/fixtures`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from fleet_copilot import policy
from fleet_copilot.agent import texts
from fleet_copilot.agent.compose import Composition, compose
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.identity import Identity
from fleet_copilot.agent.llm import (
    Explanation,
    Plan,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.agent.service import AgentService
from fleet_copilot.agent.tools import execute
from fleet_copilot.config import EXPECTED_DATASET_SHA256, REPO_ROOT, Settings
from fleet_copilot.contracts import (
    AnswerEnvelope,
    Clarification,
    ProposalCard,
    ScopeInfo,
    TraceEvent,
)
from fleet_copilot.data.loader import connect, load_dataset
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

TENANTS = ("acme-001", "globex-002", "initech-003")
DEFAULT_OUT = REPO_ROOT / "frontend" / "src" / "fixtures"
# A device that belongs to another tenant: every tenant is refused it in the same words.
_FOREIGN_DEVICE = {"acme-001": "4SEFDLPTKXAM", "globex-002": "1LYSSFD074BB"}
_FOREIGN_FALLBACK = "4SEFDLPTKXAM"

ToolCall = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class Scenario:
    name: str
    question: str
    keywords: tuple[str, ...]
    kind: str  # tools | clarification | unsupported | proposal | tool_error
    plan: str
    calls: tuple[ToolCall, ...] = ()
    synthetic: bool = False
    note: str = ""


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "low_disk",
        "Which devices are low on disk space?",
        ("disk", "storage", "space", "free"),
        "tools",
        "Check the latest free disk space against the low-disk rule.",
        (("low_disk_devices", {}),),
    ),
    Scenario(
        "macos_older_than_15",
        "Which Macs run a macOS version older than 15?",
        ("macos", "mac", "version", "older", "outdated"),
        "tools",
        "List macOS devices below version 15; Windows devices are out of scope.",
        (("macos_version_check", {}),),
    ),
    Scenario(
        "compliance_failures",
        "Which devices fail a compliance check?",
        ("compliance", "fail", "encryption", "screen lock", "checks"),
        "tools",
        "List devices with any failing compliance check at the latest snapshot.",
        (("compliance_failures_now", {}),),
    ),
    Scenario(
        "compliance_high_no_match",
        "Are any high-severity compliance checks failing?",
        ("high severity", "severity", "high-severity"),
        "tools",
        "List failing compliance checks of high severity.",
        (("compliance_failures_now", {"severity": "high"}),),
    ),
    Scenario(
        "memory_pressure",
        "Which devices are using too much memory right now?",
        ("memory", "ram"),
        "tools",
        "Check memory use at the latest snapshot against the memory rule.",
        (("memory_pressure_now", {}),),
    ),
    Scenario(
        "disk_trend",
        "Which devices have had low disk space all week?",
        ("week", "trend", "sustained", "all week"),
        "tools",
        "Classify low free disk space over the last 7 days.",
        (("trend", {"metric": "disk"}),),
    ),
    Scenario(
        "insufficient_trend",
        "Is memory use trending up over the last day?",
        ("last day", "yesterday", "24 hours", "today"),
        "tools",
        "Classify memory use over a one-day window; a trend needs at least 3 snapshots.",
        (("trend", {"metric": "memory", "window_days": 1}),),
    ),
    Scenario(
        "battery_wear",
        "Which laptops show battery wear?",
        ("battery", "wear", "cycles"),
        "tools",
        "Look for battery wear signals over the last 30 days.",
        (("trend", {"metric": "battery"}),),
    ),
    Scenario(
        "inventory_by_model",
        "How many Macs do we have, by model?",
        ("inventory", "how many", "models", "by model"),
        "tools",
        "Find macOS devices and group them by model.",
        (("find_devices", {"os_name": "macOS", "group_by": "model"}),),
    ),
    Scenario(
        "new_software",
        "Has any new software been installed recently?",
        ("software", "installed", "new apps"),
        "tools",
        "List apps newly installed in the last 30 days that are not in the common set.",
        (("software_inventory", {"view": "new_installs"}),),
    ),
    Scenario(
        "device_not_found",
        "Show the disk history for device {foreign_id}",
        ("{foreign_id}",),
        "tools",
        "Read one device's disk history.",
        (("device_history", {"device_id": "{foreign_id}", "metric": "disk_free_pct"}),),
    ),
    Scenario(
        "clarification",
        "Are my devices OK?",
        ("are my devices ok", "healthy", "overview", "status"),
        "clarification",
        "The question names no check, so ask which one to run.",
        synthetic=True,
        note="Asking this needs the language model; built here from the contract.",
    ),
    Scenario(
        "unsupported",
        "What's the weather in Berlin?",
        ("weather", "berlin"),
        "unsupported",
        "Decide whether the question is about this fleet.",
        synthetic=True,
        note="The refusal text is the composer's fixed wording; routing needs the model.",
    ),
    Scenario(
        "proposal",
        "Open a ticket for the first one",
        ("ticket", "first one", "open a ticket"),
        "proposal",
        "Resolve 'the first one' against the last list, then propose a remediation ticket.",
        (("low_disk_devices", {}),),
        synthetic=True,
        note="No proposal backend exists yet (Plan 3); built from a real low-disk answer.",
    ),
    Scenario(
        "tool_error",
        "Simulate a data lookup failure",
        ("simulate", "lookup failure", "outage"),
        "tool_error",
        "Check the latest free disk space against the low-disk rule.",
        (("low_disk_devices", {}),),
        synthetic=True,
        note="The failure is simulated; the failure wording is the composer's own.",
    ),
)


def _fill(value: Any, foreign_id: str) -> Any:
    if isinstance(value, str):
        return value.replace("{foreign_id}", foreign_id)
    if isinstance(value, dict):
        return {k: _fill(v, foreign_id) for k, v in value.items()}
    return value


def _tool_call_event(record: ToolRecord) -> tuple[str, dict[str, Any]]:
    args = {k: v for k, v in record.args.items() if v is not None}
    return f"Call {record.tool}", {"tool": record.tool, "args": args}


def _tool_result_event(record: ToolRecord) -> tuple[str, str, dict[str, Any]]:
    if record.error == "not_found":
        return "tool_result", f"{record.tool} found nothing in this company", {"error": "not_found"}
    if record.error:
        return "error", f"{record.tool} failed", {"error": record.error}
    summary = (
        f"{record.tool} matched {len(record.findings)} of {record.population} devices"
        f" ({len(record.stale)} stale, {len(record.missing_latest)} without a latest reading)"
    )
    data = {
        "matched": len(record.findings),
        "population": record.population,
        "stale": len(record.stale),
        "missing_latest": len(record.missing_latest),
        "excluded": {reason: len(ids) for reason, ids in sorted(record.excluded.items())},
    }
    return "tool_result", summary, data


def _trace(scenario: Scenario, records: list[ToolRecord], envelope: AnswerEnvelope) -> list[dict]:
    events: list[tuple[str, str, dict[str, Any]]] = [("plan", scenario.plan, {})]
    for record in records:
        summary, data = _tool_call_event(record)
        events.append(("tool_call", summary, data))
        events.append(_tool_result_event(record))
    if records and not any(r.error == "tool_error" for r in records):
        events.append(
            (
                "verify",
                "Claims, counts and evidence were written by code from the tool results.",
                {"claims": len(envelope.claims), "evidence": len(envelope.evidence)},
            )
        )
    respond_data: dict[str, Any] = {"outcome": envelope.outcome}
    if envelope.reason_code:
        respond_data["reason_code"] = envelope.reason_code
    events.append(("respond", f"Responded with {envelope.outcome}.", respond_data))
    return [
        TraceEvent(
            trace_id=envelope.trace_id, seq=seq, kind=kind, summary=summary, data=data
        ).model_dump(mode="json")
        for seq, (kind, summary, data) in enumerate(events)
    ]


async def live_trace(
    scenario: Scenario, tenant: str, db_path: Path, foreign_id: str, envelope: AnswerEnvelope
) -> list[dict]:
    """The trace of one real, scripted `AgentService` turn for a `tools` scenario.

    The scripted model plans, calls the scenario's tools and explains with the fixture's own headline,
    so the trace has the kinds, tools and data keys a live turn produces.
    """
    calls = [(tool, _fill(args, foreign_id)) for tool, args in scenario.calls]
    llm = ScriptedLLM(
        plans=[Plan(intent="answer", summary=scenario.plan)],
        acts=[tool_calls_message(*calls), final_message()],
        explains=[Explanation(headline=envelope.headline)],
    )
    service = AgentService(db_path=db_path, gateway=llm, checkpointer=InMemorySaver())
    actor = f"admin-{tenant.split('-')[0]}"
    identity = Identity(tenant, actor, f"{tenant} admin (replay)")
    question = scenario.question.replace("{foreign_id}", foreign_id)
    result = await service.run_turn(identity, "replay", question)
    return [
        # Latency is wall-clock time; a fixture must be byte-identical between runs, so it is dropped.
        event.model_copy(update={"trace_id": envelope.trace_id, "latency_ms": None}).model_dump(
            mode="json"
        )
        for event in result.trace
    ]


def _proposal_from(envelope: AnswerEnvelope, tenant: str) -> AnswerEnvelope:
    """Turn a real low-disk answer into a proposal for the first listed device."""
    first = envelope.entity_lists[0].items[0]
    refs = [
        e.ref for e in envelope.evidence if EvidenceRef.parse(e.ref).device_id == first.device_id
    ]
    scope = {"company": tenant, "device": first.device_id, "rule": envelope.scope.rule or ""}
    canonical = {
        "action_type": "remediation_ticket",
        "company_id": tenant,
        "target_device_id": first.device_id,
        "evidence_refs": refs,
        "scope": scope,
    }
    card = ProposalCard(
        proposal_id=f"prop-{envelope.trace_id}",
        action_type="remediation_ticket",
        company_id=tenant,
        target_device_id=first.device_id,
        target_label=first.label,
        rationale=(
            f"{first.label} has under {policy.LOW_DISK_FREE_PCT:g}% free disk space at its latest snapshot."
        ),
        evidence_refs=refs,
        scope=scope,
        expected_effect="Simulated: a remediation ticket would be opened. Nothing changes in a real system.",
        status="pending",
        payload_hash=hashlib.sha256(
            json.dumps(canonical, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )
    data = envelope.model_dump(mode="json")
    data.update(
        outcome="proposal",
        headline=f"Proposed: open a remediation ticket for {first.label}",
        proposal=card.model_dump(mode="json"),
    )
    return AnswerEnvelope.model_validate(data)


def _clarification(tenant: str, trace_id: str, as_of) -> AnswerEnvelope:
    return AnswerEnvelope(
        outcome="clarification",
        trace_id=trace_id,
        headline="Which check should I run?",
        scope=ScopeInfo(company_id=tenant, as_of=as_of),
        clarification=Clarification(
            question="“OK” can mean a few things. Which check do you want?",
            options=[
                "Low disk space",
                "Outdated macOS",
                "Failing compliance checks",
                "High memory use",
            ],
        ),
    )


def _build(
    scenario: Scenario, tenant: str, ctx: AgentContext, repo: FleetRepository, foreign_id: str
) -> tuple[AnswerEnvelope, list[ToolRecord]]:
    trace_id = f"replay-{tenant}-{scenario.name}"
    if scenario.kind == "clarification":
        return _clarification(tenant, trace_id, ctx.as_of), []
    if scenario.kind in ("unsupported", "tool_error"):
        records: list[ToolRecord] = []
        if scenario.kind == "unsupported":
            composition = Composition("unsupported", "off_topic", fallback_headline=texts.OFF_TOPIC)
        else:
            records = [ToolRecord(tool="low_disk_devices", args={}, error="tool_error")]
            composition = compose(records, resolve=repo.resolve)
        envelope = composition.to_envelope(company_id=tenant, as_of=ctx.as_of, trace_id=trace_id)
        return envelope, records
    records = [execute(tool, _fill(args, foreign_id), ctx) for tool, args in scenario.calls]
    composition = compose(records, resolve=repo.resolve)
    envelope = composition.to_envelope(company_id=tenant, as_of=ctx.as_of, trace_id=trace_id)
    if scenario.kind == "proposal":
        envelope = _proposal_from(envelope, tenant)
    return envelope, records


def _meta(scenario: Scenario, tenant: str, foreign_id: str) -> dict[str, Any]:
    return {
        "scenario": scenario.name,
        "tenant": tenant,
        "question": scenario.question.replace("{foreign_id}", foreign_id),
        "keywords": [k.replace("{foreign_id}", foreign_id).lower() for k in scenario.keywords],
        "synthetic": scenario.synthetic,
        "note": scenario.note,
    }


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def export_all(out_dir: Path) -> None:
    """Write `<out>/<tenant>/<scenario>.json` for every tenant and scenario, plus `<out>/index.json`."""
    out_dir = Path(out_dir)
    index: dict[str, list[dict[str, Any]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "fleet.sqlite"
        load_dataset(
            Settings.from_env().data_path, db_path, expected_sha256=EXPECTED_DATASET_SHA256
        )
        conn = connect(db_path)
        try:
            as_of = dataset_as_of(conn)
        finally:
            conn.close()
        for tenant in TENANTS:
            ctx = AgentContext(
                db_path=db_path,
                company_id=tenant,
                actor_id=f"admin-{tenant.split('-')[0]}",
                as_of=as_of,
            )
            foreign_id = _FOREIGN_DEVICE.get(tenant, _FOREIGN_FALLBACK)
            conn = connect(db_path, read_only=True)
            try:
                repo = FleetRepository(conn, TenantContext(tenant, ctx.actor_id))
                index[tenant] = []
                for scenario in SCENARIOS:
                    envelope, records = _build(scenario, tenant, ctx, repo, foreign_id)
                    meta = _meta(scenario, tenant, foreign_id)
                    if scenario.kind == "tools":
                        trace = asyncio.run(
                            live_trace(scenario, tenant, db_path, foreign_id, envelope)
                        )
                    else:
                        trace = _trace(scenario, records, envelope)
                    _write(
                        out_dir / tenant / f"{scenario.name}.json",
                        {
                            "meta": meta,
                            "envelope": envelope.model_dump(mode="json"),
                            "trace": trace,
                        },
                    )
                    index[tenant].append(meta)
            finally:
                conn.close()
    _write(out_dir / "index.json", index)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else DEFAULT_OUT
    export_all(out)
    print(f"wrote fixtures to {out}")


if __name__ == "__main__":
    main()
