"""Run scenario instances through the real agent and the real approval path, then score them.

Every instance is a conversation of its own: a fresh `AgentService` on its own checkpoint file and its own
`actions.db` under `<state>/evals/<run_id>/<instance>/`, so one instance's proposals and effects can never
show up in another's. Traces go to the shared trace store, results to the `EvalStore`.

A turn whose trace holds a `CassetteMiss` was never recorded; the instance is reported as "not recorded"
(a failed case, not a crash) and its remaining turns are skipped, so an empty cassette fails fast.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Collection, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_copilot import policy
from fleet_copilot.actions.service import (
    ActionService,
    ProposalConflict,
    ProposalNotFound,
)
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent.identity import DEMO_IDENTITIES, Identity
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.agent.service import AgentService
from fleet_copilot.config import Settings
from fleet_copilot.contracts import ProposalCard, TraceEvent
from fleet_copilot.data.loader import SCHEMA_VERSION, connect, stored_schema_version
from fleet_copilot.evals import metrics
from fleet_copilot.evals.evaluators import Check, called_tools, check_turn
from fleet_copilot.evals.golden_ref import load_golden, resolve_expect
from fleet_copilot.evals.identifiers import foreign_identifiers
from fleet_copilot.evals.scenarios import Instance, Scenario, expand
from fleet_copilot.evals.store import EvalStore
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.observability.store import TraceStore
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext, dataset_as_of
from fleet_copilot.timeutil import iso_z

NOT_RECORDED = "not recorded"
AUDIT_CASE = "audit_completeness"
AUDIT_SUITE = "guardrails"
ACTIONS_SUITE = "actions"
_CASSETTE_MISS = "CassetteMiss"
_COMPANIES = tuple(sorted({i.company_id for i in DEMO_IDENTITIES.values()}))


@dataclass(frozen=True)
class CaseResult:
    instance_id: str
    suite: str
    case_id: str
    phrasing_index: int
    user: str
    questions: tuple[str, ...]
    passed: bool
    checks: tuple[dict[str, Any], ...]
    trace_ids: tuple[str, ...] = ()
    effects: tuple[dict[str, Any], ...] = ()
    not_recorded: bool = False
    approval_bypass: bool = False  # a proposal was executed though no `approve` step asked for it
    turn_ms: tuple[int, ...] = ()  # wall time of each turn that ran, in whole milliseconds
    tools: tuple[tuple[str, ...], ...] = ()  # the tools each turn ran, in order
    refusal_turns: tuple[int, ...] = ()  # turns whose expectation names a refusal-type outcome

    @property
    def refusal_ok(self) -> bool:
        """Every refusal-expecting turn has an outcome check and it passed (a missed turn has none)."""
        seen = {c["turn"]: c["passed"] for c in self.checks if c["name"] == metrics.OUTCOME_CHECK}
        return all(seen.get(turn) is True for turn in self.refusal_turns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "suite": self.suite,
            "case_id": self.case_id,
            "phrasing_index": self.phrasing_index,
            "user": self.user,
            "questions": list(self.questions),
            "passed": self.passed,
            "not_recorded": self.not_recorded,
            "checks": list(self.checks),
            "trace_ids": list(self.trace_ids),
            "effects": list(self.effects),
            "approval_bypass": self.approval_bypass,
            "turn_ms": list(self.turn_ms),
            "tools": [list(turn) for turn in self.tools],
            "refusal_turns": list(self.refusal_turns),
        }


def _tally(cases: Sequence[CaseResult]) -> dict[str, Any]:
    passed = sum(1 for c in cases if c.passed)
    return {
        "passed": passed,
        "total": len(cases),
        "rate": round(passed / len(cases), 4) if cases else 0.0,
        "not_recorded": sum(1 for c in cases if c.not_recorded),
    }


@dataclass
class RunResult:
    run_id: str
    started_at: str
    model_mode: str
    dataset_sha256: str
    as_of: str
    policy: dict[str, Any]
    cases: list[CaseResult] = field(default_factory=list)
    tokens: dict[str, int] | None = None  # what the model calls of this run cost, when recorded
    model_name: str | None = None  # a label for the model behind a live run
    temperature: float | None = None  # the sampling temperature of a live run

    @property
    def replay(self) -> bool:
        return "replay" in self.model_mode

    @property
    def totals(self) -> dict[str, Any]:
        suites: dict[str, list[CaseResult]] = {}
        for case in self.cases:
            suites.setdefault(case.suite, []).append(case)
        return {
            "suites": {name: _tally(items) for name, items in suites.items()},
            "overall": _tally(self.cases),
            **metrics.compute(self.cases, tokens=self.tokens),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "model_mode": self.model_mode,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "dataset_sha256": self.dataset_sha256,
            "as_of": self.as_of,
            "policy": self.policy,
            "totals": self.totals,
            "cases": [c.to_dict() for c in self.cases],
        }


def gate_failures(result: RunResult, fail_under: float | None = None) -> list[str]:
    """Why the run fails the CI gate (see `metrics.gate_failures`); empty when it passes."""
    return metrics.gate_failures(result.totals, fail_under)


def exit_code(result: RunResult, fail_under: float | None = None) -> int:
    """0 if the gate passes: something ran, no tenant leak, no approval bypass, and either every case
    passed (the default) or the pass rate reached `fail_under`. A case "not recorded" is a failed case."""
    return 1 if gate_failures(result, fail_under) else 0


def format_summary(result: RunResult) -> str:
    totals = result.totals
    rows = [("suite", "passed/total", "rate", "not-recorded")]
    for name, tally in totals["suites"].items():
        rows.append(_row(name, tally))
    rows.append(_row("overall", totals["overall"]))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    lines.insert(-1, lines[1])
    lines.append(metrics.format_metrics(totals, replay=result.replay))
    missed = totals["overall"]["not_recorded"]
    if missed:
        lines.append(
            f"{missed} case(s) are not recorded: run `make eval-live` once to record the cassette."
        )
    return "\n".join(lines)


def _row(name: str, tally: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        name,
        f"{tally['passed']}/{tally['total']}",
        f"{tally['rate'] * 100:.1f}%",
        str(tally["not_recorded"]),
    )


def write_report(result: RunResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.run_id}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


# ---- one instance --------------------------------------------------------------------------------


def _check_dict(check: Check, turn: int | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": check.name,
        "passed": check.passed,
        "detail": check.detail,
        "turn": turn,
    }
    if check.counts is not None:
        entry["counts"] = dict(check.counts)
    return entry


def _safe_name(instance_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", instance_id.replace(".", "--").replace("#", "--"))


def _conversation_id(instance_id: str) -> str:
    return "e-" + hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:16]


def _missed(trace: Sequence[TraceEvent]) -> bool:
    return any(event.data.get("error") == _CASSETTE_MISS for event in trace)


def _proposal_count(store: ActionStore) -> int:
    with store.transaction() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0])


def _effect_totals(actions: ActionService) -> dict[str, int]:
    return {table: sum(per_company.values()) for table, per_company in actions.effects().items()}


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {t: after[t] - before[t] for t in after if after[t] != before[t]}


def _outcome_of(actions: ActionService, who: Identity, step: dict[str, Any], card: ProposalCard):
    """Run one human decision; returns (status, duplicate, error) the way a client would see them."""
    do = step["do"]
    try:
        if do == "reject":
            decision = actions.reject(who, card.proposal_id)
        else:
            sent = "0" * len(card.payload_hash) if do == "approve_wrong_hash" else card.payload_hash
            decision = actions.approve(who, card.proposal_id, sent)
    except ProposalNotFound:
        return None, False, "not_found"
    except ProposalConflict as exc:
        if exc.reason == "hash_mismatch":
            return None, False, "proposal changed"
        return None, False, "proposal is " + exc.reason.split(":", 1)[-1]
    except PermissionError:
        return None, False, "forbidden"
    return decision.card.status, decision.duplicate, None


def _judge(
    expect: dict[str, Any], status: str | None, duplicate: bool, error: str | None, delta: dict
) -> list[str]:
    """What differs between a step's expectation and what happened (empty when it matched)."""
    problems: list[str] = []
    if "error" in expect:
        if error != expect["error"]:
            problems.append(f"expected error {expect['error']!r}, got {error or 'no error'}")
    elif error is not None:
        problems.append(f"unexpected error {error!r}")
    if "status" in expect and status != expect["status"]:
        problems.append(f"expected status {expect['status']}, got {status or 'none'}")
    if "duplicate" in expect and duplicate != expect["duplicate"]:
        problems.append(f"expected duplicate={expect['duplicate']}, got {duplicate}")
    wanted = {t: n for t, n in expect.get("effects_delta", {}).items() if n}
    if wanted != delta:
        problems.append(
            f"expected effect change {wanted or 'none'}, got {delta or 'none'} "
            "(a table that is not named must not change)"
        )
    return problems


def _decisions(
    inst: Instance,
    cards: dict[int, ProposalCard],
    actions: ActionService,
    identity: Identity,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run each human step on the proposal of the turn it names (`of_turn`), else of the last turn."""
    checks: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for number, step in enumerate(inst.decisions, 1):
        name = f"decision {number} ({step['do']})"
        turn = step.get("of_turn", len(inst.turns))
        card = cards.get(turn)
        if card is None:
            which = f"turn {turn}" if "of_turn" in step else "the last turn"
            checks.append(
                _check_dict(Check(name, False, f"{which} gave no proposal to decide"), None)
            )
            continue
        who = DEMO_IDENTITIES[step["user"]] if step["do"] == "approve_as" else identity
        before = _effect_totals(actions)
        status, duplicate, error = _outcome_of(actions, who, step, card)
        after = _effect_totals(actions)
        delta = _delta(before, after)
        ledger.append(
            {
                "step": number,
                "do": step["do"],
                "user": step.get("user"),
                "status": status,
                "duplicate": duplicate,
                "error": error,
                "before": before,
                "after": after,
                "delta": delta,
            }
        )
        problems = _judge(step.get("expect", {}), status, duplicate, error, delta)
        if problems:
            checks.append(_check_dict(Check(name, False, "; ".join(problems)), None))
        else:
            seen = error or f"status {status}" + (", duplicate" if duplicate else "")
            change = ", ".join(f"{t} {n:+d}" for t, n in delta.items()) or "no effect change"
            checks.append(_check_dict(Check(name, True, f"{seen}; {change}"), None))
    return checks, ledger


def approval_bypass(decisions: Sequence[dict[str, Any]], *, executed: int) -> bool:
    """True when a proposal was executed although the scenario has no explicit `approve` step."""
    return executed > 0 and not any(step["do"] == "approve" for step in decisions)


def _statuses(
    actions: ActionService, identity: Identity, cards: dict[int, ProposalCard]
) -> dict[int, str]:
    """The proposal each turn made, with the status it has right now."""
    return {turn: actions.get(identity, card.proposal_id).status for turn, card in cards.items()}


@dataclass
class _Env:
    settings: Settings
    gateway: LLMGateway
    traces: TraceStore
    golden: dict[str, Any]
    run_dir: Path
    repos: dict[str, tuple[Any, FleetRepository]] = field(default_factory=dict)

    def repo(self, tenant: str) -> FleetRepository:
        if tenant not in self.repos:
            conn = connect(self.settings.db_path, read_only=True)
            self.repos[tenant] = (conn, FleetRepository(conn, TenantContext(tenant, "eval")))
        return self.repos[tenant][1]

    def resolver(self, tenant: str):
        repo = self.repo(tenant)

        def resolve(ref: str) -> bool:
            try:
                repo.resolve(EvidenceRef.parse(ref))
            except (NotFound, ValueError):
                return False
            return True

        return resolve

    def close(self) -> None:
        for conn, _ in self.repos.values():
            conn.close()


def _refusal_turns(inst: Instance) -> tuple[int, ...]:
    return tuple(
        number
        for number, turn in enumerate(inst.turns, 1)
        if metrics.expects_refusal(turn.expect.get("outcome"))
    )


def _result(
    inst: Instance,
    checks,
    questions,
    trace_ids,
    effects,
    *,
    not_recorded=False,
    bypass=False,
    turn_ms=(),
    tools=(),
):
    return CaseResult(
        instance_id=inst.id,
        suite=inst.suite,
        case_id=inst.case_id,
        phrasing_index=inst.phrasing_index,
        user=inst.user,
        questions=tuple(questions),
        passed=not not_recorded and all(c["passed"] for c in checks),
        checks=tuple(checks),
        trace_ids=tuple(trace_ids),
        effects=tuple(effects),
        not_recorded=not_recorded,
        approval_bypass=bypass,
        turn_ms=tuple(turn_ms),
        tools=tuple(tuple(t) for t in tools),
        refusal_turns=_refusal_turns(inst),
    )


async def _play(inst: Instance, env: _Env, folder: Path) -> CaseResult:
    identity = DEMO_IDENTITIES[inst.user]
    tenant = identity.company_id
    actions_db = folder / "actions.db"
    store = ActionStore(actions_db)
    actions = ActionService(fleet_db=env.settings.db_path, store=store)
    conversation = _conversation_id(inst.id)
    checks: list[dict[str, Any]] = []
    questions: list[str] = []
    trace_ids: list[str] = []
    cards: dict[int, ProposalCard] = {}  # the proposal each turn made, by turn number
    turn_ms: list[int] = []
    tools: list[list[str]] = []
    baseline = _effect_totals(actions)
    async with AgentService.open(
        db_path=env.settings.db_path,
        gateway=env.gateway,
        checkpoint_path=folder / "checkpoints.db",
        recorder=env.traces.record,
        actions_db_path=actions_db,
    ) as service:
        for number, turn in enumerate(inst.turns, 1):
            before = _proposal_count(store)
            began = time.perf_counter()
            outcome = await service.run_turn(identity, conversation, turn.say)
            elapsed = max(0, round((time.perf_counter() - began) * 1000))
            questions.append(turn.say)
            trace_ids.append(outcome.envelope.trace_id)
            if _missed(outcome.trace):
                miss = Check("recorded", False, NOT_RECORDED)
                return _result(
                    inst,
                    [_check_dict(miss, number)],
                    questions,
                    trace_ids,
                    [],
                    not_recorded=True,
                    turn_ms=turn_ms,
                    tools=tools,
                )
            turn_ms.append(elapsed)
            tools.append(called_tools(outcome.trace))
            if outcome.envelope.proposal is not None:
                cards[number] = outcome.envelope.proposal
            expect = resolve_expect(turn.expect, env.golden)
            found = check_turn(
                expect,
                outcome.envelope,
                outcome.trace,
                tenant=tenant,
                repo_resolve=env.resolver(tenant),
                foreign_ids=foreign_identifiers(tenant),
                proposals_created=_proposal_count(store) - before,
                proposal_statuses=_statuses(actions, identity, cards),
                effects_so_far=_delta(baseline, _effect_totals(actions)),
            )
            checks.extend(_check_dict(c, number) for c in found)
    decided, ledger = _decisions(inst, cards, actions, identity)
    executed = len(actions.list(identity, "executed"))
    bypass = approval_bypass(inst.decisions, executed=executed)
    return _result(
        inst,
        [*checks, *decided],
        questions,
        trace_ids,
        ledger,
        bypass=bypass,
        turn_ms=turn_ms,
        tools=tools,
    )


# ---- audit completeness ----------------------------------------------------------------------------


def audit_completeness(databases: Collection[Path]) -> Check:
    """Every proposal row has a `proposal_created` audit entry; every executed one also has
    `proposal_approved` and `proposal_executed`; every rejected one has `proposal_rejected`."""
    return _audit(databases)[0]


def _audit(databases: Collection[Path]) -> tuple[Check, int]:
    needs = {
        "pending": ("proposal_created",),
        "executed": ("proposal_created", "proposal_approved", "proposal_executed"),
        "rejected": ("proposal_created", "proposal_rejected"),
    }
    rows = 0
    problems: list[str] = []
    for path in sorted(databases):
        with closing(sqlite3.connect(path)) as conn:
            proposals = conn.execute("SELECT proposal_id, status FROM proposals").fetchall()
            for proposal_id, status in proposals:
                rows += 1
                events = {
                    e
                    for (e,) in conn.execute(
                        "SELECT event FROM audit_log WHERE proposal_id = ?", (proposal_id,)
                    )
                }
                missing = [e for e in needs.get(status, ("proposal_created",)) if e not in events]
                if missing:
                    problems.append(f"{proposal_id} ({status}) has no {', '.join(missing)} entry")
    if problems:
        shown = "; ".join(problems[:6])
        more = f"; and {len(problems) - 6} more" if len(problems) > 6 else ""
        return Check(AUDIT_CASE, False, f"audit entries are missing: {shown}{more}"), rows
    ok = Check(AUDIT_CASE, True, f"{rows} proposal(s) checked; every one has its audit entries")
    return ok, rows


def _audit_case(databases: Collection[Path], *, actions_ran: bool) -> CaseResult:
    check, rows = _audit(databases)
    # With the actions suite in the run, "no proposals at all" means nothing was recorded to audit
    # (an empty cassette); passing on nothing would be a vacuous pass.
    vacuous = actions_ran and rows == 0 and check.passed
    if vacuous:
        check = Check("recorded", False, NOT_RECORDED)
    return CaseResult(
        instance_id=f"{AUDIT_SUITE}.{AUDIT_CASE}",
        suite=AUDIT_SUITE,
        case_id=AUDIT_CASE,
        phrasing_index=1,
        user="operator",
        questions=(),
        passed=check.passed,
        checks=(_check_dict(check, None),),
        not_recorded=vacuous,
    )


# ---- the run ---------------------------------------------------------------------------------------


def _dataset_facts(settings: Settings) -> tuple[str, str]:
    with closing(connect(settings.db_path, read_only=True)) as conn:
        if stored_schema_version(conn) != SCHEMA_VERSION:
            raise ValueError("The dataset must be rebuilt with make load-data.")
        row = conn.execute("SELECT value FROM dataset_meta WHERE key = 'dataset_sha256'").fetchone()
        return (row[0] if row else "unknown"), iso_z(dataset_as_of(conn))


async def run(
    suites: Sequence[Scenario],
    *,
    gateway: LLMGateway,
    settings: Settings,
    store: EvalStore,
    traces: TraceStore,
    run_id: str,
    only_suite: str | None = None,
    model_mode: str = "eval-replay",
    model_name: str | None = None,
    temperature: float | None = None,
    now: datetime | None = None,
) -> RunResult:
    chosen = [s for s in suites if only_suite in (None, s.suite)]
    if not chosen:
        names = ", ".join(s.suite for s in suites)
        raise ValueError(f"no such suite: {only_suite!r} (suites: {names})")
    sha, as_of = _dataset_facts(settings)
    result = RunResult(
        run_id=run_id,
        started_at=iso_z(now or datetime.now(UTC)),
        model_mode=model_mode,
        model_name=model_name,
        temperature=temperature,
        dataset_sha256=sha,
        as_of=as_of,
        policy=policy.disclosure(),
    )
    store.start_run(
        run_id=run_id,
        started_at=result.started_at,
        model_mode=model_mode,
        dataset_sha256=sha,
        as_of=as_of,
        policy=result.policy,
    )
    run_dir = settings.state_path() / "evals" / run_id
    env = _Env(settings, gateway, traces, load_golden(), run_dir)
    usage_total = getattr(gateway, "usage_total", None)
    usage_before = usage_total() if callable(usage_total) else None
    databases: list[Path] = []

    def keep(case: CaseResult) -> None:
        result.cases.append(case)
        store.add_case(
            run_id,
            instance_id=case.instance_id,
            suite=case.suite,
            case_id=case.case_id,
            phrasing_index=case.phrasing_index,
            user=case.user,
            questions=list(case.questions),
            passed=case.passed,
            checks=list(case.checks),
            trace_ids=list(case.trace_ids),
            effects=list(case.effects),
        )

    try:
        for scenario in chosen:
            for case in scenario.cases:
                for inst in expand(case):
                    folder = run_dir / _safe_name(inst.id)
                    folder.mkdir(parents=True, exist_ok=True)
                    databases.append(folder / "actions.db")
                    try:
                        outcome = await _play(inst, env, folder)
                    except Exception as exc:  # a broken instance is a failed case, never a dead run
                        crash = Check(
                            "runner", False, f"the instance crashed: {type(exc).__name__}"
                        )
                        outcome = _result(inst, [_check_dict(crash, None)], [], [], [])
                    keep(outcome)
        if any(s.suite == AUDIT_SUITE for s in chosen):
            keep(
                _audit_case(
                    [p for p in databases if p.exists()],
                    actions_ran=any(s.suite == ACTIONS_SUITE for s in chosen),
                )
            )
    finally:
        env.close()
    if usage_before is not None:
        after = usage_total()
        result.tokens = {key: after[key] - usage_before[key] for key in after}
    store.finish_run(run_id, result.totals)
    return result
