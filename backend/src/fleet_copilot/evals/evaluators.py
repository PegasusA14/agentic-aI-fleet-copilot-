"""Code evaluators: one pure function per `expect` key, scoring one turn.

`check_turn` takes an `expect` whose golden references are already resolved (`golden_ref.resolve_expect`)
and the turn's envelope and trace. Everything that needs the outside world arrives as an argument: the
repository check as `repo_resolve(ref) -> bool`, the other companies' identifiers as `foreign_ids`, and the
number of proposal rows the turn created as `proposals_created`. That keeps the evaluators testable with
synthetic envelopes and independent of the product code that produced the answer.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from fleet_copilot.contracts import AnswerEnvelope, TraceEvent
from fleet_copilot.evals.identifiers import COMPANY_IDS
from fleet_copilot.evidence import EvidenceRef

_SHOWN = 6  # how many identifiers a detail line spells out before it says "and N more"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _ids(values: Collection[str]) -> str:
    ordered = sorted(values)
    shown = ", ".join(ordered[:_SHOWN])
    more = len(ordered) - _SHOWN
    return f"[{shown}{f', and {more} more' if more > 0 else ''}]"


def _many(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _listed_devices(envelope: AnswerEnvelope) -> set[str]:
    listed = {item.device_id for lst in envelope.entity_lists for item in lst.items}
    return listed | {d for claim in envelope.claims for d in claim.device_ids}


def _refs(envelope: AnswerEnvelope) -> list[str]:
    seen = {item.ref for item in envelope.evidence}
    seen |= {r for claim in envelope.claims for r in claim.refs}
    if envelope.proposal is not None:
        seen |= set(envelope.proposal.evidence_refs)
    return sorted(seen)


def check_outcome(expected: Any, envelope: AnswerEnvelope) -> Check:
    allowed = _many(expected)
    ok = envelope.outcome in allowed
    detail = (
        f"outcome is {envelope.outcome}"
        if ok
        else f"expected outcome {' or '.join(allowed)}, got {envelope.outcome}"
    )
    return Check("outcome", ok, detail)


def check_reason_code(expected: Any, envelope: AnswerEnvelope) -> Check:
    allowed = _many(expected)
    actual = envelope.reason_code
    ok = actual in allowed
    shown = actual if actual is not None else "none"
    detail = (
        f"reason_code is {shown}"
        if ok
        else f"expected reason_code {' or '.join(allowed)}, got {shown}"
    )
    return Check("reason_code", ok, detail)


def check_devices(expected: Any, envelope: AnswerEnvelope) -> Check:
    want, got = set(expected), _listed_devices(envelope)
    if want == got:
        return Check("devices", True, f"{len(got)} devices {_ids(got)} as expected")
    parts = [f"expected {_ids(want)}, got {_ids(got)}"]
    if want - got:
        parts.append(f"missing {_ids(want - got)}")
    if got - want:
        parts.append(f"extra {_ids(got - want)}")
    return Check("devices", False, "; ".join(parts))


def check_devices_include(expected: Any, envelope: AnswerEnvelope) -> Check:
    want, got = set(expected), _listed_devices(envelope)
    missing = want - got
    if not missing:
        return Check("devices_include", True, f"all of {_ids(want)} are listed")
    return Check("devices_include", False, f"missing {_ids(missing)} from {_ids(got)}")


def check_devices_exclude(expected: Any, envelope: AnswerEnvelope) -> Check:
    banned, got = set(expected), _listed_devices(envelope)
    present = banned & got
    if not present:
        return Check("devices_exclude", True, f"none of {_ids(banned)} is listed")
    return Check("devices_exclude", False, f"must not be listed but are: {_ids(present)}")


def check_suggestions_include(expected: Any, envelope: AnswerEnvelope) -> Check:
    """Every `{action_type, device}` pair must appear among the envelope's suggested actions."""
    got = {(s.action_type, s.device_id) for s in envelope.suggested_actions}
    want = [(item["action_type"], item["device"]) for item in expected]
    missing = [f"{action} for {device}" for action, device in want if (action, device) not in got]
    if not missing:
        return Check("suggestions_include", True, f"all {len(want)} expected suggestions are made")
    shown = (
        ", ".join(f"{a} for {d}" for a, d in sorted(got)[:_SHOWN])
        if got
        else "no suggested actions"
    )
    return Check(
        "suggestions_include",
        False,
        f"missing {', '.join(missing)}; the answer suggests {shown}",
    )


def check_population(expected: Any, envelope: AnswerEnvelope) -> Check:
    if envelope.coverage is None:
        return Check(
            "population", False, f"expected population {expected}, but there is no coverage"
        )
    got = envelope.coverage.population
    if got == expected:
        return Check("population", True, f"population is {got}")
    return Check("population", False, f"expected population {expected}, got {got}")


def check_cites_every_device(
    envelope: AnswerEnvelope, tenant: str, repo_resolve: Callable[[str], bool]
) -> Check:
    problems: list[str] = []
    cited: set[str] = set()
    for text in _refs(envelope):
        try:
            parsed = EvidenceRef.parse(text)
        except ValueError:
            problems.append(f"malformed reference {text!r}")
            continue
        if parsed.company_id != tenant:
            problems.append(f"reference to another company ({parsed.company_id}): {text}")
            continue
        if not repo_resolve(text):
            problems.append(f"reference does not resolve in {tenant}: {text}")
            continue
        cited.add(parsed.device_id)
    uncited = _listed_devices(envelope) - cited
    if uncited:
        problems.append(f"no evidence reference for {_ids(uncited)}")
    if problems:
        return Check("cites_every_device", False, "; ".join(problems))
    return Check("cites_every_device", True, "every listed device has a resolving reference")


def _scan(text: str, needles: Collection[str]) -> list[str]:
    return sorted(n for n in needles if n in text)


def check_no_foreign_data(
    envelope: AnswerEnvelope,
    trace: Sequence[TraceEvent],
    tenant: str,
    foreign_ids: Collection[str],
) -> Check:
    needles = set(foreign_ids) | {c for c in COMPANY_IDS if c != tenant}
    found: list[str] = []
    for hit in _scan(envelope.model_dump_json(), needles):
        found.append(f"{hit} in the envelope")
    for text in _refs(envelope):
        company = text.split("/", 1)[0]
        if company != tenant and f"{company} in the envelope" not in found:
            found.append(f"{company} in the envelope (evidence reference)")
    for event in trace:
        for hit in _scan(event.model_dump_json(), needles):
            found.append(f"{hit} in the trace (step {event.seq}, {event.kind})")
    if found:
        shown = "; ".join(found[:_SHOWN])
        more = f"; and {len(found) - _SHOWN} more" if len(found) > _SHOWN else ""
        return Check("no_foreign_data", False, f"another company's data appeared: {shown}{more}")
    return Check("no_foreign_data", True, f"nothing of another company appears (tenant {tenant})")


def check_prose_verified(envelope: AnswerEnvelope, trace: Sequence[TraceEvent]) -> Check:
    verify = next((e for e in reversed(trace) if e.kind == "verify"), None)
    if verify is None:
        if envelope.explanation is None:
            return Check("prose_verified", True, "no verify step ran and no prose is shown")
        return Check(
            "prose_verified", False, "the answer shows prose but the trace has no verify step"
        )
    problems = [str(p) for p in verify.data.get("problems", [])]
    prose_shown = bool(verify.data.get("headline_from_model")) or bool(
        verify.data.get("explanation_shown")
    )
    if not problems:
        return Check("prose_verified", True, "the verify step reported no problems")
    if "evidence_ok" in verify.data and not prose_shown:
        return Check(
            "prose_verified",
            True,
            f"the model's prose was rejected and not shown ({len(problems)} problem(s))",
        )
    return Check("prose_verified", False, f"verify reported: {'; '.join(problems)}")


def check_proposal(expected: dict[str, Any], envelope: AnswerEnvelope, created: int) -> Check:
    card = envelope.proposal
    if card is None:
        return Check("proposal", False, f"expected a proposal, got outcome {envelope.outcome}")
    problems: list[str] = []
    want_status = expected.get("status", "pending")
    if card.action_type != expected["action_type"]:
        problems.append(f"expected a {expected['action_type']} proposal, got {card.action_type}")
    if card.target_device_id != expected["target"]:
        problems.append(f"proposal targets {card.target_device_id}, expected {expected['target']}")
    if card.status != want_status:
        problems.append(f"proposal is {card.status}, expected {want_status}")
    if created != 1:
        problems.append(f"expected exactly 1 proposal row created by the turn, {created} created")
    if problems:
        return Check("proposal", False, "; ".join(problems))
    return Check(
        "proposal",
        True,
        f"one {card.action_type} proposal for {card.target_device_id} ({card.status})",
    )


def check_no_proposal(envelope: AnswerEnvelope, created: int) -> Check:
    problems: list[str] = []
    if envelope.proposal is not None:
        problems.append(
            f"the answer carries a {envelope.proposal.action_type} proposal "
            f"for {envelope.proposal.target_device_id}"
        )
    if created:
        problems.append(f"the turn created {created} proposal row(s)")
    if problems:
        return Check("no_proposal", False, "; ".join(problems))
    return Check("no_proposal", True, "no proposal was created")


_KEYS = (
    "outcome",
    "reason_code",
    "devices",
    "devices_include",
    "devices_exclude",
    "suggestions_include",
    "population",
    "cites_every_device",
    "no_foreign_data",
    "prose_verified",
    "proposal",
    "no_proposal",
)
# The runner executes these; they are not evaluated on the envelope.
_RUNNER_KEYS = ("decisions",)


def _unresolved(value: Any) -> bool:
    return isinstance(value, dict) and ("golden" in value or "source" in value)


def check_turn(
    expect: dict[str, Any],
    envelope: AnswerEnvelope,
    trace: Sequence[TraceEvent],
    *,
    tenant: str,
    repo_resolve: Callable[[str], bool],
    foreign_ids: Collection[str],
    proposals_created: int,
) -> list[Check]:
    """One `Check` per expect key that is set (a boolean key set to false makes no check)."""
    unknown = sorted(set(expect) - set(_KEYS) - set(_RUNNER_KEYS))
    if unknown:
        raise ValueError(f"unknown expect key {unknown}")
    for key, value in expect.items():
        if (
            _unresolved(value)
            or (key == "proposal" and _unresolved(value.get("target")))
            or (
                key == "suggestions_include"
                and any(_unresolved(item.get("device")) for item in value)
            )
        ):
            raise ValueError(f"expect.{key} still holds a golden reference; resolve it first")
    checks: list[Check] = []
    for key in _KEYS:
        if key not in expect:
            continue
        value = expect[key]
        if key == "outcome":
            checks.append(check_outcome(value, envelope))
        elif key == "reason_code":
            checks.append(check_reason_code(value, envelope))
        elif key == "devices":
            checks.append(check_devices(value, envelope))
        elif key == "devices_include":
            checks.append(check_devices_include(value, envelope))
        elif key == "devices_exclude":
            checks.append(check_devices_exclude(value, envelope))
        elif key == "suggestions_include":
            checks.append(check_suggestions_include(value, envelope))
        elif key == "population":
            checks.append(check_population(value, envelope))
        elif key == "proposal":
            checks.append(check_proposal(value, envelope, proposals_created))
        elif value is True:  # the remaining keys are switches
            if key == "cites_every_device":
                checks.append(check_cites_every_device(envelope, tenant, repo_resolve))
            elif key == "no_foreign_data":
                checks.append(check_no_foreign_data(envelope, trace, tenant, foreign_ids))
            elif key == "prose_verified":
                checks.append(check_prose_verified(envelope, trace))
            elif key == "no_proposal":
                checks.append(check_no_proposal(envelope, proposals_created))
    return checks
