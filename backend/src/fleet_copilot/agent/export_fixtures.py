"""Export real composer answers as replay fixtures for the frontend.

Each fixture is the output of the real tool layer and the deterministic composer, run as one tenant
over the real dataset: `execute()` -> `compose()` -> `Composition.to_envelope()`. Nothing is hand-written
data. A live list answer also carries code-only `suggested_actions` (the next steps on its subject), which
the graph adds after the prose is verified; the fixtures get them from the same code. The trace of every real (`tools`) scenario is the trace of a real scripted `AgentService` turn, and the
`proposal` fixture is the whole envelope and trace of one: a scripted propose plan and `open_remediation_ticket` call
against a temporary actions database (the eligibility, parameters, evidence and wording are the real code's).
A few outcomes cannot occur without a live model or an outage, so those are built from the same parts and
flagged `synthetic` with a note saying why.

Run: `python -m fleet_copilot.agent.export_fixtures [out_dir]` (default `frontend/src/fixtures`).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from fleet_copilot.agent import outcomes
from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.context import AgentContext
from fleet_copilot.agent.graph import _list_suggestions, _with_extras
from fleet_copilot.agent.identity import DEMO_IDENTITIES, Identity
from fleet_copilot.agent.llm import (
    Plan,
    Reply,
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
    ScopeInfo,
    TraceEvent,
)
from fleet_copilot.data.loader import connect, load_dataset
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

TENANTS = ("acme-001", "globex-002", "initech-003")
GOLDEN = REPO_ROOT / "evals" / "golden" / "golden.json"
DEFAULT_OUT = REPO_ROOT / "frontend" / "src" / "fixtures"
# A device that belongs to another tenant: every tenant is refused it in the same words.
_FOREIGN_DEVICE = {"acme-001": "4SEFDLPTKXAM", "globex-002": "1LYSSFD074BB"}
_FOREIGN_FALLBACK = "4SEFDLPTKXAM"

# A device with battery readings that report no service condition, so a replacement flag is refused.
_HEALTHY_DEVICE = {
    "acme-001": "1LYSSFD074BB",
    "globex-002": "DXUCJ272DCYX",
    "initech-003": "SPKRRDF0L786",
}

_COMPANY_NAMES = {i.company_id: i.company_name for i in DEMO_IDENTITIES.values()}

ToolCall = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class Scenario:
    name: str
    question: str
    keywords: tuple[str, ...]
    kind: str  # tools | clarification | unsupported | proposal | tool_error | turn
    plan: str
    calls: tuple[ToolCall, ...] = ()
    synthetic: bool = False
    note: str = ""
    missing_topic: str = ""  # unsupported scenarios: the data the telemetry does not hold
    # `turn` scenarios: the scripted plan's fields (the model's decision), run through the real graph.
    plan_fields: tuple[tuple[str, Any], ...] = ()


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
        "fleet_health",
        "Give me a fleet health summary",
        ("fleet health", "summary", "needs attention", "overview"),
        "tools",
        "Rank what needs attention across battery, disk, memory and compliance.",
        (("fleet_health", {}),),
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
        "unsupported_wifi",
        "Which devices have a weak Wi-Fi signal?",
        ("wi-fi", "wifi", "signal", "network quality"),
        "unsupported",
        "The question asks for network data the telemetry does not hold.",
        synthetic=True,
        note="The refusal text and alternatives are fixed wording; routing needs the model.",
        missing_topic="network",
    ),
    Scenario(
        "proposal",
        "Open a ticket for the first one",
        ("ticket", "first one", "open a ticket"),
        "proposal",
        "Propose a remediation ticket for one device whose screen lock check is failing.",
        (
            (
                "open_remediation_ticket",
                {
                    "device_id": "{target}",
                    "check_id": "screen_lock",
                },
            ),
        ),
    ),
    Scenario(
        "insufficient_evidence",
        "Flag {device} for replacement.",
        ("for replacement", "replacement", "flag"),
        "turn",
        "Propose a replacement flag for one device whose battery reports no service condition.",
        (("flag_device_for_replacement", {"device_id": "{target}"}),),
        synthetic=True,
        note="The refusal is the real eligibility check run on the device; routing needs the model.",
        plan_fields=(("intent", "propose"), ("action_type", "replacement_flag")),
    ),
    Scenario(
        "other_company",
        "Show me the devices at another company.",
        ("another company", "other company", "other companies", "another tenant"),
        "turn",
        "The question is about another company in general, so refuse it as outside this session.",
        synthetic=True,
        note="The card is the graph's own fixed wording; routing needs the model.",
        plan_fields=(("intent", "refuse"), ("refusal_reason", "other_company")),
    ),
    Scenario(
        "clarification_device",
        "How is the macbook doing on battery?",
        ("the macbook", "macbook doing"),
        "turn",
        "The question says 'macbook', which matches several devices, so ask which one.",
        synthetic=True,
        note="The options are built by code from this company's devices; routing needs the model.",
        plan_fields=(("intent", "clarify"), ("device_phrase", "macbook")),
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


def _fill(value: Any, replacement: str, placeholder: str = "{foreign_id}") -> Any:
    if isinstance(value, str):
        return value.replace(placeholder, replacement)
    if isinstance(value, dict):
        return {k: _fill(v, replacement, placeholder) for k, v in value.items()}
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

    The scripted model plans, calls the scenario's tools and replies with no text,
    so the trace has the kinds, tools and data keys a live turn produces.
    """
    calls = [(tool, _fill(args, foreign_id)) for tool, args in scenario.calls]
    llm = ScriptedLLM(
        plans=[Plan(intent="answer", summary=scenario.plan)],
        acts=[tool_calls_message(*calls), final_message()],
        explains=[Reply(text="")],
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


def _golden_screen_lock_target(tenant: str) -> str:
    """The first device the independent golden file lists as failing screen_lock for this tenant."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    failing = golden["tenants"][tenant]["failing_latest"]["all"]["screen_lock"]
    if not failing:
        raise RuntimeError(f"{tenant} has no device with a failing screen_lock check")
    return failing[0]


PROPOSAL_REPLY = (
    "That ticket proposal is ready for your review, and it stays pending until you approve it."
)


async def live_proposal(
    scenario: Scenario, tenant: str, db_path: Path, actions_db: Path
) -> tuple[AnswerEnvelope, list[dict]]:
    """The envelope and trace of one real, scripted turn that proposes a remediation ticket.

    The scripted model plans `propose`, calls `open_remediation_ticket` and replies with one line; the action service decides eligibility
    and writes the card, the evidence and the headline. Two things are normalised so the fixture is
    byte-identical between runs: the trace id is fixed through the service's `trace_id_factory`, and the
    proposal id (random per run) is replaced by `prop-<trace id>`. The payload hash is a pure function of the
    tenant, action, device and parameters, and the card carries no timestamp, so neither needs a fixed clock.
    """
    target = _golden_screen_lock_target(tenant)
    calls = [(tool, _fill(args, target, "{target}")) for tool, args in scenario.calls]
    trace_id = f"replay-{tenant}-{scenario.name}"
    llm = ScriptedLLM(
        plans=[Plan(intent="propose", summary=scenario.plan)],
        acts=[tool_calls_message(*calls), final_message()],
        # The reply step reads the card's summary; this scripted line stands in for what it would write.
        explains=[Reply(text=PROPOSAL_REPLY)],
    )
    service = AgentService(
        db_path=db_path,
        gateway=llm,
        checkpointer=InMemorySaver(),
        trace_id_factory=lambda: trace_id,
        actions_db_path=actions_db,
    )
    actor = f"admin-{tenant.split('-')[0]}"
    identity = Identity(
        tenant, actor, f"{tenant} admin (replay)", company_name=_COMPANY_NAMES.get(tenant, "")
    )
    result = await service.run_turn(identity, "replay", scenario.question)
    envelope = result.envelope
    if envelope.outcome != "proposal" or envelope.proposal is None:
        raise RuntimeError(f"{tenant}: the proposal turn ended as {envelope.outcome}")
    random_id = envelope.proposal.proposal_id
    fixed_id = f"prop-{trace_id}"

    def normalise(value: Any) -> Any:
        return json.loads(json.dumps(value).replace(random_id, fixed_id))

    trace = [
        normalise(event.model_copy(update={"latency_ms": None}).model_dump(mode="json"))
        for event in result.trace
    ]
    return AnswerEnvelope.model_validate(normalise(envelope.model_dump(mode="json"))), trace


async def live_turn(
    scenario: Scenario, tenant: str, db_path: Path, actions_db: Path, question: str, target: str
) -> tuple[AnswerEnvelope, list[dict]]:
    """The envelope and trace of one real turn whose planner decision is scripted.

    The graph does everything after the plan: the other-company card, the clarification options and the
    refusal with what it would need are all its own code. The trace id is fixed and latency dropped so the
    fixture is byte-identical between runs.
    """
    trace_id = f"replay-{tenant}-{scenario.name}"
    plan = Plan(summary=scenario.plan, **dict(scenario.plan_fields))
    calls = [(tool, _fill(args, target, "{target}")) for tool, args in scenario.calls]
    llm = ScriptedLLM(
        plans=[plan], acts=[tool_calls_message(*calls), final_message()] if calls else []
    )
    service = AgentService(
        db_path=db_path,
        gateway=llm,
        checkpointer=InMemorySaver(),
        trace_id_factory=lambda: trace_id,
        actions_db_path=actions_db,
    )
    actor = f"admin-{tenant.split('-')[0]}"
    identity = Identity(
        tenant, actor, f"{tenant} admin (replay)", company_name=_COMPANY_NAMES.get(tenant, "")
    )
    result = await service.run_turn(identity, "replay", question)
    trace = [
        event.model_copy(update={"latency_ms": None}).model_dump(mode="json")
        for event in result.trace
    ]
    return result.envelope, trace


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
    if scenario.kind == "unsupported":
        # The graph's own refusal builder: fixed text, the capability line and the code-written alternatives.
        reason = "data_unavailable" if scenario.missing_topic else "off_topic"
        envelope = outcomes.refusal(
            reason,  # type: ignore[arg-type]
            missing_topic=scenario.missing_topic or None,  # type: ignore[arg-type]
            company_id=tenant,
            as_of=ctx.as_of,
            trace_id=trace_id,
        )
        return envelope, []
    if scenario.kind == "tool_error":
        records = [ToolRecord(tool="low_disk_devices", args={}, error="tool_error")]
        composition = compose(records, resolve=repo.resolve)
        envelope = composition.to_envelope(company_id=tenant, as_of=ctx.as_of, trace_id=trace_id)
        return envelope, records
    records = [execute(tool, _fill(args, foreign_id), ctx) for tool, args in scenario.calls]
    composition = compose(records, resolve=repo.resolve)
    envelope = composition.to_envelope(
        company_id=tenant,
        as_of=ctx.as_of,
        trace_id=trace_id,
        company_name=ctx.company_name,
    )
    # A live list answer carries code-only next steps on its subject, added by the graph after the prose is
    # verified. The fixture gets them from the same code, with the same evidence.
    extras = (
        _list_suggestions(ctx, composition, records, repo.resolve)
        if composition.outcome == "answer"
        else None
    )
    if extras is not None:
        envelope = _with_extras(envelope, extras) or envelope
    return envelope, records


def _meta(scenario: Scenario, tenant: str, foreign_id: str, device: str = "") -> dict[str, Any]:
    return {
        "scenario": scenario.name,
        "tenant": tenant,
        "question": scenario.question.replace("{foreign_id}", foreign_id).replace(
            "{device}", device
        ),
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
                company_name=_COMPANY_NAMES.get(tenant, ""),
            )
            foreign_id = _FOREIGN_DEVICE.get(tenant, _FOREIGN_FALLBACK)
            conn = connect(db_path, read_only=True)
            try:
                repo = FleetRepository(conn, TenantContext(tenant, ctx.actor_id))
                index[tenant] = []
                for scenario in SCENARIOS:
                    target = _HEALTHY_DEVICE.get(tenant, "")
                    host = repo.get_device(target).hostname if target else ""
                    meta = _meta(scenario, tenant, foreign_id, host)
                    if scenario.kind == "turn":
                        envelope, trace = asyncio.run(
                            live_turn(
                                scenario,
                                tenant,
                                db_path,
                                Path(tmp) / f"actions-{tenant}.db",
                                meta["question"],
                                target,
                            )
                        )
                    elif scenario.kind == "proposal":
                        envelope, trace = asyncio.run(
                            live_proposal(
                                scenario, tenant, db_path, Path(tmp) / f"actions-{tenant}.db"
                            )
                        )
                    else:
                        envelope, records = _build(scenario, tenant, ctx, repo, foreign_id)
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
