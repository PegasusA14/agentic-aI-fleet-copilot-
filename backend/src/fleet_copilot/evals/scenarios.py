"""Scenario files (`evals/scenarios/<suite>.json`): loading, validation and paraphrase expansion.

A case is a short conversation. Exactly one of its turns may carry a LIST of phrasings; the case then
expands into one `Instance` per phrasing, each a conversation of its own. Every validation error names the
file and the case, so a broken scenario is found without reading a traceback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from fleet_copilot.agent.identity import DEMO_IDENTITIES
from fleet_copilot.contracts import ActionType, OutcomeType, ReasonCode
from fleet_copilot.evals.golden_ref import validate_ref

_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_OUTCOMES = frozenset(get_args(OutcomeType))
_REASONS = frozenset(get_args(ReasonCode))
_ACTIONS = frozenset(get_args(ActionType))
_BOOLEAN_KEYS = ("cites_every_device", "no_foreign_data", "prose_verified", "no_proposal")
_DEVICE_SET_KEYS = ("devices", "devices_include", "devices_exclude")
_EXPECT_KEYS = frozenset(
    {
        "outcome",
        "reason_code",
        "population",
        "proposal",
        "decisions",
        *_BOOLEAN_KEYS,
        *_DEVICE_SET_KEYS,
    }
)
_DECISIONS = ("approve", "approve_again", "approve_wrong_hash", "reject", "approve_as")
_DECISION_STATUSES = frozenset(
    {"pending", "approved", "rejected", "executed", "failed", "uncertain", "invalidated"}
)
_DECISION_EXPECT_KEYS = frozenset({"status", "duplicate", "error", "effects_delta"})


class ScenarioError(ValueError):
    """A scenario file or case is malformed. The message names the file and the case."""


@dataclass(frozen=True)
class Turn:
    """One user message and what to expect of the answer. `say` is a single phrasing."""

    say: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class CaseTurn:
    """A turn as written in a case: one phrasing, or the list of phrasings to expand."""

    say: str | tuple[str, ...]
    expect: dict[str, Any]

    @property
    def phrasings(self) -> tuple[str, ...]:
        return (self.say,) if isinstance(self.say, str) else self.say


@dataclass(frozen=True)
class Case:
    suite: str
    id: str
    user: str
    turns: tuple[CaseTurn, ...]


@dataclass(frozen=True)
class Scenario:
    suite: str
    path: Path
    cases: tuple[Case, ...]


@dataclass(frozen=True)
class Instance:
    """One runnable conversation: a case with a single phrasing chosen for its paraphrased turn."""

    id: str  # <suite>.<case>#<n>, n counting phrasings from 1
    suite: str
    case_id: str
    phrasing_index: int
    paraphrased: bool
    user: str
    turns: tuple[Turn, ...]

    @property
    def decisions(self) -> tuple[dict[str, Any], ...]:
        """Human approval steps to run after the last turn (declared in the last turn's expect)."""
        return tuple(self.turns[-1].expect.get("decisions", ()))


def expand(case: Case) -> list[Instance]:
    """One instance per phrasing of the case's list turn, or a single instance when there is none."""
    list_at = next((i for i, t in enumerate(case.turns) if not isinstance(t.say, str)), None)
    count = 1 if list_at is None else len(case.turns[list_at].phrasings)
    instances: list[Instance] = []
    for n in range(1, count + 1):
        turns = tuple(
            Turn(say=t.phrasings[n - 1] if i == list_at else t.phrasings[0], expect=t.expect)
            for i, t in enumerate(case.turns)
        )
        instances.append(
            Instance(
                id=f"{case.suite}.{case.id}#{n}",
                suite=case.suite,
                case_id=case.id,
                phrasing_index=n,
                paraphrased=list_at is not None,
                user=case.user,
                turns=turns,
            )
        )
    return instances


def load_suites(directory: Path | str) -> list[Scenario]:
    """Every `*.json` suite in the directory, sorted by file name."""
    root = Path(directory)
    if not root.is_dir():
        raise ScenarioError(f"no such scenario directory: {root}")
    return [_load_file(path) for path in sorted(root.glob("*.json"))]


def _load_file(path: Path) -> Scenario:
    where = path.name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f"{where}: cannot read the suite ({type(exc).__name__})") from None
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: the file must hold an object with 'suite' and 'cases'")
    suite = raw.get("suite")
    cases = raw.get("cases")
    if not isinstance(suite, str) or not _ID.match(suite):
        raise ScenarioError(f"{where}: 'suite' must be a lower-case name")
    if not isinstance(cases, list) or not cases:
        raise ScenarioError(f"{where}: 'cases' must be a non-empty list")
    loaded: list[Case] = []
    seen: set[str] = set()
    for index, item in enumerate(cases):
        label = (
            item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else None
        )
        case_where = f"{where}, case {label if label else f'#{index + 1}'}"
        try:
            case = _load_case(suite, item)
        except ScenarioError as exc:
            raise ScenarioError(f"{case_where}: {exc}") from None
        if case.id in seen:
            raise ScenarioError(f"{case_where}: duplicate case id")
        seen.add(case.id)
        loaded.append(case)
    return Scenario(suite=suite, path=path, cases=tuple(loaded))


def _load_case(suite: str, item: Any) -> Case:
    if not isinstance(item, dict):
        raise ScenarioError("a case must be an object")
    case_id = item.get("id")
    if not isinstance(case_id, str) or not _ID.match(case_id):
        raise ScenarioError("id must be lower-case letters, digits and underscores")
    user = item.get("user")
    if user not in DEMO_IDENTITIES:
        raise ScenarioError(f"user must be one of {sorted(DEMO_IDENTITIES)}, got {user!r}")
    raw_turns = item.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ScenarioError("turns must be a non-empty list")
    turns = tuple(_load_turn(n, t, last=n == len(raw_turns)) for n, t in enumerate(raw_turns, 1))
    if sum(1 for t in turns if not isinstance(t.say, str)) > 1:
        raise ScenarioError("at most one turn may list several phrasings in 'say'")
    return Case(suite=suite, id=case_id, user=user, turns=turns)


def _load_turn(number: int, raw: Any, *, last: bool) -> CaseTurn:
    where = f"turn {number}"
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: a turn must be an object")
    say = raw.get("say")
    if isinstance(say, str):
        if not say.strip():
            raise ScenarioError(f"{where}: say must not be empty")
        parsed: str | tuple[str, ...] = say
    elif isinstance(say, list):
        if not say or not all(isinstance(s, str) and s.strip() for s in say):
            raise ScenarioError(f"{where}: say must be a non-empty list of non-empty strings")
        if len(set(say)) != len(say):
            raise ScenarioError(f"{where}: say lists the same phrasing twice")
        parsed = tuple(say)
    else:
        raise ScenarioError(f"{where}: say must be a string or a list of strings")
    expect = raw.get("expect", {})
    try:
        _check_expect(expect, last=last)
    except ScenarioError as exc:
        raise ScenarioError(f"{where}: {exc}") from None
    return CaseTurn(say=parsed, expect=expect)


def _one_or_many(key: str, value: Any, allowed: frozenset[str]) -> None:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        raise ScenarioError(f"expect.{key} must be a string or a non-empty list of strings")
    unknown = [v for v in values if v not in allowed]
    if unknown:
        raise ScenarioError(f"expect.{key} has unknown value {unknown}; allowed {sorted(allowed)}")


def _device_set(key: str, value: Any) -> None:
    if isinstance(value, dict):
        problem = validate_ref(value)
        if problem is not None:
            raise ScenarioError(f"expect.{key}: {problem}")
    elif not isinstance(value, list) or not all(isinstance(d, str) and d for d in value):
        raise ScenarioError(f"expect.{key} must be a list of device ids or a golden reference")


def _check_expect(expect: Any, *, last: bool) -> None:
    if not isinstance(expect, dict):
        raise ScenarioError("expect must be an object")
    unknown = sorted(set(expect) - _EXPECT_KEYS)
    if unknown:
        raise ScenarioError(f"unknown expect key {unknown}; allowed {sorted(_EXPECT_KEYS)}")
    if "outcome" in expect:
        _one_or_many("outcome", expect["outcome"], _OUTCOMES)
    if "reason_code" in expect:
        _one_or_many("reason_code", expect["reason_code"], _REASONS)
    for key in _DEVICE_SET_KEYS:
        if key in expect:
            _device_set(key, expect[key])
    if "population" in expect:
        population = expect["population"]
        if isinstance(population, dict):
            problem = validate_ref(population)
            if problem is not None:
                raise ScenarioError(f"expect.population: {problem}")
        elif not isinstance(population, int) or isinstance(population, bool) or population < 0:
            raise ScenarioError("expect.population must be a whole number or a golden reference")
    for key in _BOOLEAN_KEYS:
        if key in expect and not isinstance(expect[key], bool):
            raise ScenarioError(f"expect.{key} must be true or false")
    if "proposal" in expect:
        _check_proposal(expect["proposal"])
    if "decisions" in expect:
        if not last:
            raise ScenarioError("decisions belong on the last turn of the case")
        _check_decisions(expect["decisions"])


def _check_proposal(card: Any) -> None:
    if not isinstance(card, dict):
        raise ScenarioError("expect.proposal must be an object")
    extra = sorted(set(card) - {"action_type", "target", "status"})
    if extra:
        raise ScenarioError(f"expect.proposal has unknown key {extra}")
    if card.get("action_type") not in _ACTIONS:
        raise ScenarioError(f"expect.proposal.action_type must be one of {sorted(_ACTIONS)}")
    target = card.get("target")
    if isinstance(target, dict):
        problem = validate_ref(target)
        if problem is not None:
            raise ScenarioError(f"expect.proposal.target: {problem}")
    elif not isinstance(target, str) or not target:
        raise ScenarioError("expect.proposal.target must be a device id or a golden reference")
    if "status" in card and card["status"] not in _DECISION_STATUSES:
        raise ScenarioError(f"expect.proposal.status must be one of {sorted(_DECISION_STATUSES)}")


def _check_decisions(steps: Any) -> None:
    if not isinstance(steps, list) or not steps:
        raise ScenarioError("expect.decisions must be a non-empty list of steps")
    for number, step in enumerate(steps, 1):
        where = f"expect.decisions step {number}"
        if not isinstance(step, dict):
            raise ScenarioError(f"{where} must be an object")
        extra = sorted(set(step) - {"do", "user", "expect"})
        if extra:
            raise ScenarioError(f"{where} has unknown key {extra}")
        if step.get("do") not in _DECISIONS:
            raise ScenarioError(f"{where}: do must be one of {list(_DECISIONS)}")
        if step["do"] == "approve_as":
            if step.get("user") not in DEMO_IDENTITIES:
                raise ScenarioError(
                    f"{where}: approve_as needs 'user', one of {sorted(DEMO_IDENTITIES)}"
                )
        elif "user" in step:
            raise ScenarioError(f"{where}: 'user' only goes with approve_as")
        _check_decision_expect(where, step.get("expect", {}))


def _check_decision_expect(where: str, expect: Any) -> None:
    if not isinstance(expect, dict):
        raise ScenarioError(f"{where}: expect must be an object")
    extra = sorted(set(expect) - _DECISION_EXPECT_KEYS)
    if extra:
        raise ScenarioError(f"{where}: unknown expect key {extra}")
    if "status" in expect and expect["status"] not in _DECISION_STATUSES:
        raise ScenarioError(f"{where}: status must be one of {sorted(_DECISION_STATUSES)}")
    if "duplicate" in expect and not isinstance(expect["duplicate"], bool):
        raise ScenarioError(f"{where}: duplicate must be true or false")
    if "error" in expect and not (isinstance(expect["error"], str) and expect["error"]):
        raise ScenarioError(f"{where}: error must be a message")
    delta = expect.get("effects_delta", {})
    if not isinstance(delta, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in delta.items()
    ):
        raise ScenarioError(f"{where}: effects_delta must map table names to whole numbers")
