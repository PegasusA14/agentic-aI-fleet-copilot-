"""Code-only action recommendations, built on the eligibility rules.

For each device this asks `eligibility.check` about every action that could apply (a ticket per
currently failing check, a replacement flag, a memory and a storage upgrade, and a notification for
the strongest current finding) and keeps the eligible ones. Every number and every word comes from
the tenant-bound repository and the pinned as-of through eligibility; the model writes nothing here.
Recommending never creates a proposal: acting on a suggestion is a separate, approved turn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fleet_copilot import policy
from fleet_copilot.actions import eligibility
from fleet_copilot.actions.eligibility import Eligible
from fleet_copilot.contracts import ActionType
from fleet_copilot.repository import FleetRepository, NotFound

_DEFAULT_MAX_SUGGESTIONS = 6

# Ranks, best first: failing high > battery service > sustained storage/memory > medium/low checks >
# notifications. Checks of lower severity sort after the sustained-resource rank.
_FAILING_HIGH, _BATTERY, _SUSTAINED, _CHECK, _NOTIFICATION = range(5)
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_TOPIC_PHRASE = {
    "low_disk": "low disk space",
    "memory_pressure": "high memory use",
    "battery_service": "its battery service recommendation",
}

# What a person calls each compliance check ("an OS update ticket"). Unknown ids read as their own words.
_CHECK_NAME = {
    "os_up_to_date": "OS update",
    "screen_lock": "screen lock",
    "disk_encryption": "disk encryption",
}


def check_name(check_id: str) -> str:
    return _CHECK_NAME.get(check_id, check_id.replace("_", " "))


def _a(phrase: str) -> str:
    """`a screen lock`, `an OS update`: the article by sound, treating a leading capital acronym as spoken letters."""
    first = phrase[:1]
    spoken_vowel = first.lower() in "aeiou" or (phrase[:2].isupper() and first in "FHLMNORSX")
    return f"{'an' if spoken_vowel else 'a'} {phrase}"


@dataclass(frozen=True)
class Suggestion:
    action_type: ActionType
    device_id: str
    target_label: str  # hostname
    label: str  # chip text, e.g. "Order a storage upgrade (1 TiB → 2 TiB) for acme-macbook-4"
    reason: str  # code-written from eligibility, numbers from data
    refs: tuple[str, ...]  # tenant evidence refs, from eligibility
    params: dict[str, str]  # canonical, from eligibility
    request: str  # the user message the chip sends to propose this action


def max_suggestions() -> int:
    """The cap, read when asked: `policy.MAX_SUGGESTIONS` when present, else 6."""
    return int(getattr(policy, "MAX_SUGGESTIONS", _DEFAULT_MAX_SUGGESTIONS))


def _suggestion(got: Eligible, label: str, request: str) -> Suggestion:
    return Suggestion(
        got.action_type,
        got.device_id,
        got.target_label,
        label,
        f"{got.target_label}: {got.rationale}",
        got.refs,
        dict(got.params),
        request,
    )


def _ticket(got: Eligible) -> Suggestion:
    name = check_name(got.params["check_id"])
    host = got.target_label
    return _suggestion(
        got,
        f"Open {_a(name)} ticket for {host}",
        f"Open a remediation ticket for the {name} check on {host}",
    )


def _replacement(got: Eligible) -> Suggestion:
    return _suggestion(
        got,
        f"Flag {got.target_label} for replacement",
        f"Flag {got.target_label} for replacement",
    )


def _upgrade(got: Eligible) -> Suggestion:
    p = got.params
    return _suggestion(
        got,
        f"Order a {p['component']} upgrade ({p['current']} → {p['target']}) for {got.target_label}",
        f"Order a {p['component']} upgrade for {got.target_label}",
    )


def _notification(got: Eligible, check_id: str | None) -> Suggestion:
    topic = got.params["topic"]
    host = got.target_label
    if topic == "failing_check" and check_id is not None:
        phrase = f"its failing {check_name(check_id)} check"
        about = f"{_a('failing ' + check_name(check_id))} check"
    else:
        phrase = _TOPIC_PHRASE[topic]
        about = "battery service" if topic == "battery_service" else phrase
    return _suggestion(
        got,
        f"Notify the owner of {host} about {about}",
        f"Send the employee of {host} a notification about {phrase}",
    )


def _for_device(
    repo: FleetRepository,
    as_of: datetime,
    device_id: str,
    only: Callable[[Suggestion], bool] | None = None,
) -> list[tuple[tuple[int, int], Suggestion]]:
    """Every eligible suggestion for one device with its sort key, in no particular order.

    With `only`, the one notification is the strongest finding `only` accepts, not the strongest overall.
    """
    try:
        latest = repo.latest(device_id)
    except NotFound:
        return []
    out: list[tuple[tuple[int, int], Suggestion]] = []

    def ask(action_type: ActionType, **kwargs: str | None) -> Eligible | None:
        got = eligibility.check(repo, as_of, action_type, device_id, **kwargs)
        return got if isinstance(got, Eligible) else None

    failing: list[tuple[int, str]] = []  # (severity rank, check id) of the tickets kept
    if latest is not None:
        for check_id in sorted(c for c, r in latest.compliance.items() if r.status == "fail"):
            got = ask("remediation_ticket", check_id=check_id)
            if got is None:
                continue
            severity = _SEVERITY_ORDER.get(got.params["severity"], len(_SEVERITY_ORDER))
            rank = _FAILING_HIGH if severity == 0 else _CHECK
            out.append(((rank, severity), _ticket(got)))
            failing.append((severity, check_id))
    failing.sort()

    battery = ask("replacement_flag")
    if battery is not None:
        out.append(((_BATTERY, 0), _replacement(battery)))
    for order, component in enumerate(("storage", "memory")):
        got = ask("upgrade_order", component=component)
        if got is not None:
            out.append(((_SUSTAINED, order), _upgrade(got)))

    # One notification, for the strongest current finding: a failing high-severity check, the
    # battery, low disk, memory pressure, then a lower-severity failing check.
    candidates: list[tuple[str, str | None]] = []
    high = [c for s, c in failing if s == 0]
    low = [c for s, c in failing if s != 0]
    if high:
        candidates.append(("failing_check", high[0]))
    candidates += [("battery_service", None), ("low_disk", None), ("memory_pressure", None)]
    if low:
        candidates.append(("failing_check", low[0]))
    for topic, check_id in candidates:
        got = ask("employee_notification", topic=topic, check_id=check_id)
        if got is not None:
            note = _notification(got, check_id)
            if only is None or only(note):
                out.append(((_NOTIFICATION, 0), note))
                break
    return out


def recommend(
    repo: FleetRepository,
    as_of: datetime,
    device_ids: list[str] | tuple[str, ...],
    action_type: ActionType | None = None,
    only: Callable[[Suggestion], bool] | None = None,
) -> list[Suggestion]:
    """Eligible suggestions for the given devices, best first, at most `max_suggestions()`.

    With `action_type`, only suggestions of that type are kept; with `only`, only those it accepts.
    Both filters apply before the cap, so unrelated suggestions never crowd out related ones.

    Unknown and out-of-tenant ids are skipped without a trace. Order is by rank, then by the order
    the devices were given (duplicates ignored), then by action within a device, so the same input
    always gives the same output.
    """
    ranked: list[tuple[tuple[int, int, int, int], Suggestion]] = []
    for position, device_id in enumerate(dict.fromkeys(device_ids)):
        for index, ((rank, sub), suggestion) in enumerate(
            _for_device(repo, as_of, device_id, only)
        ):
            if action_type is not None and suggestion.action_type != action_type:
                continue
            if only is not None and not only(suggestion):
                continue
            ranked.append(((rank, sub if rank == _CHECK else 0, position, index), suggestion))
    ranked.sort(key=lambda item: item[0])
    return [s for _, s in ranked[: max_suggestions()]]
