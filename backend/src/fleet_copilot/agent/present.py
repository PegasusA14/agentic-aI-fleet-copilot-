"""Presentation of a list answer: the table, one supporting sentence, scope counts and next questions.

Everything here is written by code from `FindingRecord.values`. It is additive output for the UI: it
never feeds a model prompt, a claim text or a headline, and it adds no evidence the claims do not cite.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from fleet_copilot import policy
from fleet_copilot.actions.eligibility import display_hostname
from fleet_copilot.actions.recommend import Suggestion
from fleet_copilot.agent.format import fmt_bytes, fmt_day, fmt_pct, fmt_signed_pct, noun
from fleet_copilot.agent.records import FindingRecord, ToolRecord
from fleet_copilot.contracts import (
    MAX_SUMMARY_CHARS,
    MAX_TABLE_ROWS,
    Alternative,
    AnswerTable,
    Cell,
    Column,
    Row,
    SortSpec,
)
from fleet_copilot.domain.osversion import parse_version

# Why a device was not covered, when a record carries no per-device text (older stored records).
GENERIC_EXCLUDED_TEXT = {
    "out_of_scope": "Out of scope for this check",
    "unknown_version": "Version not recognised",
    "insufficient_history": "Not enough snapshots in the window",
    "no_reading": "No battery reading",
    "no_readings": "No readings",
}
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
_PATTERN_RANK = {"sustained": 5, "onset": 4, "intermittent": 3, "recovering": 2, "isolated": 1}
_PATTERN_TONE = {"sustained": "attention", "onset": "attention", "recovering": "ok"}
_KIND_RANK = {"persistent_failure": 3, "transition": 2, "recovered": 1}
_KIND_TEXT = {
    "persistent_failure": "Failing throughout",
    "transition": "Newly failing",
    "recovered": "Recovered",
}
_KIND_SENTENCE = {
    "persistent_failure": "failing throughout the window",
    "transition": "newly failing",
    "recovered": "recovered",
}
_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_SAFE_TEXT = re.compile(r"[\w .+()/-]{1,60}")
_LATEST_SNAPSHOT = "latest snapshot per device"

DEVICE = Column(key="device", label="Device", kind="device")


def representative(refs: Sequence[str], limit: int = policy.MAX_REFS_PER_FINDING) -> list[str]:
    """At most `limit` refs, keeping the first and last and spreading the rest evenly."""
    unique = list(dict.fromkeys(refs))
    if len(unique) <= limit:
        return unique
    if limit < 2:
        return unique[-1:]
    step = (len(unique) - 1) / (limit - 1)
    return [unique[i] for i in sorted({round(k * step) for k in range(limit)})]


@dataclass(frozen=True)
class ScopeParts:
    """Counts for the scope line; the composer adds the company name and the as-of time."""

    checked: int
    matched: int
    ok: int | None
    ok_label: str
    not_checked: int
    basis: str


@dataclass
class Presentation:
    table: AnswerTable | None = None
    summary: str = ""
    scope: ScopeParts | None = None
    follow_ups: list[Alternative] = field(default_factory=list)
    # (list group, device id) in the table's default row order, for every row (the table itself stops
    # at MAX_TABLE_ROWS). The entity lists that "the first one" resolves against follow this order.
    order: list[tuple[str, str]] = field(default_factory=list)


# ---- small helpers ------------------------------------------------------------------------------


def _word(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def _safe(text: object, fallback: str = "unknown") -> str:
    """Device-reported text, only when it is plain: names and versions, never markup or sentences."""
    value = str(text)
    return value if _SAFE_TEXT.fullmatch(value) else fallback


def _label(f: FindingRecord) -> str:
    return display_hostname(f.hostname, f.device_id)


def _device_cell(f: FindingRecord) -> Cell:
    label = _label(f)
    return Cell(display=label, value=label)


def _are(n: int) -> str:
    return "is" if n == 1 else "are"


def _who(n: int) -> str:
    return "It" if n == 1 else "Both" if n == 2 else f"All {_word(n)}"


def _sentence(clauses: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`, capitalised, with a full stop, and never over the summary cap."""
    if not clauses:
        return ""
    body = clauses[0] if len(clauses) == 1 else f"{', '.join(clauses[:-1])} and {clauses[-1]}"
    text = f"{body[0].upper()}{body[1:]}."
    return text if len(text) <= MAX_SUMMARY_CHARS else f"{text[: MAX_SUMMARY_CHARS - 1].rstrip()}…"


def _counted(counts: Counter[str], order: Sequence[str] | None = None) -> list[tuple[str, int]]:
    keys = [k for k in order if k in counts] if order else sorted(counts)
    return [(k, counts[k]) for k in keys]


def _distinct(findings: Sequence[FindingRecord]) -> int:
    return len({f.device_id for f in findings})


def _table(
    title: str,
    columns: list[Column],
    rows: list[Row],
    sort: SortSpec,
) -> AnswerTable:
    return AnswerTable(
        title=title,
        columns=[DEVICE, *columns],
        rows=rows[:MAX_TABLE_ROWS],
        total_rows=len(rows),
        default_sort=sort,
    )


def _order(rows: Sequence[Row], groups: Sequence[str] | None = None) -> list[tuple[str, str]]:
    """The default order of the devices of a table: (group, device id) per row, `groups` aligned to rows."""
    return [(groups[i] if groups else "", row.device_id) for i, row in enumerate(rows)]


def _row(f: FindingRecord, refs: Sequence[str], **cells: Cell) -> Row:
    return Row(
        device_id=f.device_id,
        label=_label(f),
        cells={"device": _device_cell(f), **cells},
        refs=list(refs),
    )


def _excluded_ids(rec: ToolRecord, *reasons: str) -> int:
    return len({d for reason in reasons for d in rec.excluded.get(reason, [])})


# ---- one builder per tool -----------------------------------------------------------------------


def _major(version: str) -> int | None:
    parsed = parse_version(version)
    return parsed[0] if parsed else None


def _macos(rec: ToolRecord) -> Presentation:
    target = rec.args.get("older_than") or "15"
    target_major = _major(target)
    ordered = sorted(
        rec.findings,
        key=lambda f: (parse_version(f.values["os_version"]) or (), f.device_id),
    )
    rows = []
    for f in ordered:
        version = _safe(f.values["os_version"])
        major = _major(f.values["os_version"])
        behind = None if major is None or target_major is None else max(target_major - major, 0)
        if behind is None:
            behind_cell = Cell(display="Older", value=None, tone="neutral")
        elif behind == 0:
            behind_cell = Cell(display="Older minor version", value=0, tone="neutral")
        else:
            behind_cell = Cell(
                display=f"{behind} major behind",
                value=behind,
                tone="attention" if behind >= 2 else "neutral",
            )
        rows.append(
            _row(
                f,
                representative(f.refs),
                macos=Cell(display=version, value=version),
                behind=behind_cell,
            )
        )
    versions = Counter(_safe(f.values["os_version"]) for f in ordered)
    groups = sorted(versions.items(), key=lambda kv: parse_version(kv[0]) or ())
    n = len(ordered)
    if len(groups) == 1:
        summary = _sentence([f"{_who(n).lower()} {_are(n)} on {groups[0][0]}"])
    else:
        summary = _sentence(
            [
                f"{_word(k)} {_are(k)} on {v}" if i == 0 else f"{_word(k)} on {v}"
                for i, (v, k) in enumerate(groups)
            ]
        )
    columns = [
        Column(key="macos", label="macOS", kind="version"),
        Column(key="behind", label=f"Behind {target_major or target}", kind="pill"),
    ]
    unknown = _excluded_ids(rec, "unknown_version")
    matched = _distinct(ordered)
    return Presentation(
        order=_order(rows),
        table=_table(
            f"macOS older than {target}",
            columns,
            rows,
            SortSpec(key="macos", direction="asc"),
        ),
        summary=summary,
        scope=ScopeParts(
            checked=rec.population,
            matched=matched,
            ok=None if unknown else max(rec.population - matched, 0),
            ok_label="" if unknown else "up to date",
            not_checked=_excluded_ids(rec, "out_of_scope"),
            basis=_LATEST_SNAPSHOT,
        ),
    )


def _low_disk(rec: ToolRecord) -> Presentation:
    thr = rec.args.get("threshold_pct") or policy.LOW_DISK_FREE_PCT
    ordered = sorted(rec.findings, key=lambda f: (f.values["free_pct"], f.device_id))
    rows = [
        _row(
            f,
            representative(f.refs),
            free=Cell(
                display=fmt_pct(f.values["free_pct"]), value=f.values["free_pct"], tone="attention"
            ),
            space=Cell(
                display=f"{fmt_bytes(f.values['available_bytes'])} of {fmt_bytes(f.values['size_bytes'])}",
                value=f.values["available_bytes"],
            ),
        )
        for f in ordered
    ]
    n = len(ordered)
    lowest = fmt_pct(ordered[0].values["free_pct"])
    summary = (
        _sentence([f"it has {lowest} free ({rows[0].cells['space'].display})"])
        if n == 1
        else _sentence([f"{_who(n).lower()} {_are(n)} under {thr:g}% free, the lowest at {lowest}"])
    )
    return Presentation(
        order=_order(rows),
        table=_table(
            f"Low disk (<{thr:g}% free)",
            [
                Column(key="free", label="Free", kind="percent"),
                Column(key="space", label="Free space", kind="text"),
            ],
            rows,
            SortSpec(key="free", direction="asc"),
        ),
        summary=summary,
        scope=_plain_scope(rec, "healthy"),
        follow_ups=[
            _trend_ask(
                f"Which devices have had under {thr:g}% free disk space for most of the last "
                f"{policy.TREND_WINDOW_DAYS} days?"
            )
        ],
    )


def _memory(rec: ToolRecord) -> Presentation:
    thr = rec.args.get("threshold_pct") or policy.MEMORY_USED_PCT
    ordered = sorted(rec.findings, key=lambda f: (-f.values["used_pct"], f.device_id))
    rows = [
        _row(
            f,
            representative(f.refs),
            used=Cell(
                display=fmt_pct(f.values["used_pct"]), value=f.values["used_pct"], tone="attention"
            ),
            memory=Cell(
                display=f"{fmt_bytes(f.values['used_bytes'])} of {fmt_bytes(f.values['total_bytes'])}",
                value=f.values["used_bytes"],
            ),
        )
        for f in ordered
    ]
    n = len(ordered)
    highest = fmt_pct(ordered[0].values["used_pct"])
    summary = (
        _sentence([f"it is at {highest} used"])
        if n == 1
        else _sentence(
            [f"{_who(n).lower()} {_are(n)} at or above {thr:g}% used, the highest at {highest}"]
        )
    )
    return Presentation(
        order=_order(rows),
        table=_table(
            f"High memory (>={thr:g}% used)",
            [
                Column(key="used", label="Used", kind="percent"),
                Column(key="memory", label="Memory in use", kind="text"),
            ],
            rows,
            SortSpec(key="used", direction="desc"),
        ),
        summary=summary,
        scope=_plain_scope(rec, "healthy"),
        follow_ups=[
            _trend_ask(
                f"Which devices have had memory use at or above {thr:g}% for most of the last "
                f"{policy.TREND_WINDOW_DAYS} days?"
            )
        ],
    )


def _compliance_now(rec: ToolRecord) -> Presentation:
    pairs: list[tuple[FindingRecord, str, str, str]] = []  # finding, check, severity, ref
    for f in rec.findings:
        for failing, ref in zip(f.values["failing"], f.refs, strict=True):
            pairs.append((f, _safe(failing["check_id"]), failing["severity"], ref))
    pairs.sort(key=lambda p: (-_SEVERITY_RANK.get(p[2], 0), p[1], p[0].device_id))
    rows = [
        _row(
            f,
            [ref],
            check=Cell(display=check, value=check),
            severity=Cell(
                display=severity.capitalize(),
                value=_SEVERITY_RANK.get(severity, 0),
                tone="attention" if severity == "high" else "neutral",
            ),
            seen=Cell(display=fmt_day(f.values["collected_at"]), value=f.values["collected_at"]),
        )
        for f, check, severity, ref in pairs
    ]
    by_check = Counter(check for _, check, _, _ in pairs)
    ranked = sorted(by_check.items(), key=lambda kv: (-kv[1], kv[0]))
    clauses = [f"{_word(k)} fail{'s' if k == 1 else ''} {check}" for check, k in ranked[:3]]
    if len(ranked) > 3:
        m = len(ranked) - 3
        clauses.append(f"{_word(m)} other {noun(m, 'check')} also fail{'s' if m == 1 else ''}")
    follow_ups = []
    if len(by_check) == 1:
        check = next(iter(by_check))
        follow_ups.append(
            _trend_ask(
                f"Which devices have been failing {check} over the last "
                f"{policy.COMPLIANCE_WINDOW_DAYS} days?"
            )
        )
    return Presentation(
        order=_order(rows, [check for _, check, _, _ in pairs]),
        table=_table(
            "Failing compliance checks",
            [
                Column(key="check", label="Check", kind="text"),
                Column(key="severity", label="Severity", kind="pill"),
                Column(key="seen", label="Latest snapshot", kind="date"),
            ],
            rows,
            SortSpec(key="severity", direction="desc"),
        ),
        summary=_sentence(clauses),
        scope=_plain_scope(rec, "passing"),
        follow_ups=follow_ups,
    )


def _trend(rec: ToolRecord) -> Presentation:
    metric = rec.args.get("metric")
    if metric in ("memory", "disk"):
        return _trend_breach(rec, metric)
    if metric == "compliance":
        return _trend_compliance(rec)
    return _battery(rec)


def _trend_breach(rec: ToolRecord, metric: str) -> Presentation:
    days = rec.args.get("window_days") or policy.TREND_WINDOW_DAYS
    latest_label = "Used now" if metric == "memory" else "Free now"
    ordered = sorted(
        rec.findings,
        key=lambda f: (-_PATTERN_RANK[f.values["trend"]], f.device_id),
    )
    rows = [
        _row(
            f,
            representative(f.refs),
            pattern=Cell(
                display=f.values["trend"].capitalize(),
                value=_PATTERN_RANK[f.values["trend"]],
                tone=_PATTERN_TONE.get(f.values["trend"], "neutral"),
            ),
            breached=Cell(
                display=f"{f.values['breached']} of {f.values['snapshots']} snapshots",
                value=f.values["breached"],
            ),
            latest=Cell(display=fmt_pct(f.values["latest_value"]), value=f.values["latest_value"]),
        )
        for f in ordered
    ]
    counts = Counter(f.values["trend"] for f in ordered)
    summary = _sentence(
        [f"{_word(k)} {_are(k)} {cls}" for cls, k in _counted(counts, tuple(_PATTERN_RANK))]
    )
    return Presentation(
        order=_order(rows),
        table=_table(
            f"{'Memory' if metric == 'memory' else 'Disk'} trend ({days} d)",
            [
                Column(key="pattern", label="Pattern", kind="pill"),
                Column(key="breached", label="Breached", kind="text"),
                Column(key="latest", label=latest_label, kind="percent"),
            ],
            rows,
            SortSpec(key="pattern", direction="desc"),
        ),
        summary=summary,
        scope=_plain_scope(rec, "healthy", basis=f"last {days} days of snapshots", thin=True),
    )


def _trend_compliance(rec: ToolRecord) -> Presentation:
    days = rec.args.get("window_days") or policy.COMPLIANCE_WINDOW_DAYS
    check = _safe(rec.args.get("check_id", "the check"), "the check")
    ordered = sorted(rec.findings, key=lambda f: (-_KIND_RANK[f.values["kind"]], f.device_id))
    rows = []
    for f in ordered:
        kind = f.values["kind"]
        change = f.values["change_at"]
        rows.append(
            _row(
                f,
                representative(f.refs),
                pattern=Cell(
                    display=_KIND_TEXT[kind],
                    value=_KIND_RANK[kind],
                    tone="ok" if kind == "recovered" else "attention",
                ),
                since=Cell(
                    display=fmt_day(change) if change else "Whole window",
                    value=change,
                ),
                snapshots=Cell(display=str(f.values["snapshots"]), value=f.values["snapshots"]),
            )
        )
    counts = Counter(f.values["kind"] for f in ordered)
    summary = _sentence(
        [
            f"{_word(k)} {_are(k)} {_KIND_SENTENCE[kind]}"
            for kind, k in _counted(counts, tuple(_KIND_RANK))
        ]
    )
    return Presentation(
        order=_order(rows),
        table=_table(
            f"{check} ({days} d)",
            [
                Column(key="pattern", label="Pattern", kind="pill"),
                Column(key="since", label="Since", kind="date"),
                Column(key="snapshots", label="Snapshots", kind="number"),
            ],
            rows,
            SortSpec(key="pattern", direction="desc"),
        ),
        summary=summary,
        scope=_plain_scope(rec, "passing", basis=f"last {days} days of snapshots", thin=True),
    )


def _battery(rec: ToolRecord) -> Presentation:
    days = rec.args.get("window_days") or policy.BATTERY_WINDOW_DAYS

    def key(f: FindingRecord) -> tuple[int, float, str]:
        change = f.values["capacity_change_pct"]
        return (change is None, change if change is not None else 0.0, f.device_id)

    ordered = sorted(rec.findings, key=key)
    rows = []
    declining = servicing = 0
    for f in ordered:
        v = f.values
        change = v["capacity_change_pct"]
        is_declining = change is not None and change < -policy.BATTERY_DECLINE_PCT
        declining += is_declining
        servicing += bool(v["service_streak"])
        rows.append(
            _row(
                f,
                representative(f.refs),
                capacity=Cell(
                    display="Not reported" if change is None else fmt_signed_pct(change),
                    value=change,
                    tone="attention" if is_declining else "neutral",
                ),
                cycles=Cell(
                    display="Not reported"
                    if v["cycles_latest"] is None
                    else str(v["cycles_latest"]),
                    value=v["cycles_latest"],
                ),
                condition=Cell(
                    display="Service recommended"
                    if v["service_streak"]
                    else _safe(v["latest_condition"], "Not reported"),
                    value=v["latest_condition"],
                    tone="attention" if v["service_streak"] else "neutral",
                ),
            )
        )
    clauses = []
    if declining:
        clauses.append(
            f"{_word(declining)} {_are(declining)} down more than {policy.BATTERY_DECLINE_PCT:g}% in capacity"
        )
    if servicing:
        clauses.append(
            f"{_word(servicing)} {'has' if servicing == 1 else 'have'} a Service Recommended flag"
        )
    scope = _plain_scope(
        rec, "with no wear signal", basis=f"last {days} days of snapshots", thin=True
    )
    if scope.ok:
        clauses_text = _sentence(clauses)
        rest = f"{_word(scope.ok)[0].upper()}{_word(scope.ok)[1:]} {noun(scope.ok, 'laptop')} show no meaningful decline."
        summary = f"{clauses_text} {rest}" if clauses_text else rest
        summary = summary if len(summary) <= MAX_SUMMARY_CHARS else clauses_text
    else:
        summary = _sentence(clauses)
    return Presentation(
        order=_order(rows),
        table=_table(
            f"Battery wear ({days} d)",
            [
                Column(key="capacity", label="Capacity change", kind="percent"),
                Column(key="cycles", label="Cycles", kind="number"),
                Column(key="condition", label="Condition", kind="pill"),
            ],
            rows,
            SortSpec(key="capacity", direction="asc"),
        ),
        summary=summary,
        scope=scope,
    )


def _fleet_health(rec: ToolRecord) -> Presentation:
    """No table: the insight cards are the list. Just the counts behind the footer line."""
    days = rec.args.get("window_days") or policy.BATTERY_WINDOW_DAYS
    return Presentation(
        scope=_plain_scope(
            rec, "with nothing to flag", basis=f"last {days} days of snapshots", thin=True
        )
    )


def _find(rec: ToolRecord) -> Presentation:
    ordered = sorted(rec.findings, key=lambda f: (_label(f), f.device_id))
    rows = [
        _row(
            f,
            representative(f.refs),
            os=Cell(display=_os_label(f), value=_os_label(f)),
            model=Cell(display=_safe(f.values["model_name"]), value=_safe(f.values["model_name"])),
        )
        for f in ordered
    ]
    counts = Counter(_os_label(f) for f in ordered)
    groups = sorted(counts.items())
    n = len(ordered)
    if len(groups) > 3:
        summary = _sentence(
            [f"{_word(n)} {noun(n, 'device')} run {_word(len(groups))} different OS versions"]
        )
    else:
        summary = _sentence(
            [
                f"{_word(k)} {_are(k)} on {v}" if i == 0 else f"{_word(k)} on {v}"
                for i, (v, k) in enumerate(groups)
            ]
        )
    return Presentation(
        order=_order(rows),
        table=_table(
            "Matching devices",
            [
                Column(key="os", label="OS", kind="text"),
                Column(key="model", label="Model", kind="text"),
            ],
            rows,
            SortSpec(key="device", direction="asc"),
        ),
        summary=summary,
        scope=ScopeParts(
            checked=rec.population,
            matched=_distinct(ordered),
            ok=None,
            ok_label="",
            not_checked=0,
            basis=_LATEST_SNAPSHOT,
        ),
    )


def _os_label(f: FindingRecord) -> str:
    return f"{_safe(f.values['os_name'])} {_safe(f.values['os_version'], '')}".strip()


def _software(rec: ToolRecord) -> Presentation:
    if rec.args.get("view") == "new_installs":
        return _new_installs(rec)
    if "apps" in rec.findings[0].values:
        return _inventory(rec)
    return _holders(rec)


def _software_scope(rec: ToolRecord, matched: int, basis: str = _LATEST_SNAPSHOT) -> ScopeParts:
    # Devices that report no software are in the installed view's population but were not checked;
    # the new-installs view leaves them out of its population already.
    not_checked = _excluded_ids(rec, "no_readings")
    in_population = rec.args.get("view") != "new_installs"
    return ScopeParts(
        checked=max(rec.population - (not_checked if in_population else 0), 0),
        matched=matched,
        ok=None,
        ok_label="",
        not_checked=not_checked,
        basis=basis,
    )


def _inventory(rec: ToolRecord) -> Presentation:
    ordered = sorted(rec.findings, key=lambda f: (_label(f), f.device_id))
    rows = []
    with_extra = 0
    for f in ordered:
        count = len(f.values["apps"])
        extra = [_safe(f"{a['name']} {a['version']}", "unknown app") for a in f.values["extra"]]
        with_extra += bool(extra)
        rows.append(
            _row(
                f,
                list(f.refs),
                apps=Cell(display=f"{count} {noun(count, 'app')}", value=count),
                extra=Cell(
                    display=", ".join(extra)[:120] if extra else "None",
                    value=len(extra),
                    tone="attention" if extra else "neutral",
                ),
            )
        )
    n = len(ordered)
    summary = (
        _sentence([f"{_word(with_extra)} of {_word(n)} have apps that are not on every device"])
        if with_extra
        else _sentence(["no device has apps beyond the shared set"])
    )
    return Presentation(
        order=_order(rows),
        table=_table(
            "Installed software",
            [
                Column(key="apps", label="Apps", kind="number"),
                Column(key="extra", label="Not on every device", kind="text"),
            ],
            rows,
            SortSpec(key="device", direction="asc"),
        ),
        summary=summary,
        scope=_software_scope(rec, n),
    )


def _holders(rec: ToolRecord) -> Presentation:
    name = _safe(rec.findings[0].values["name"], "the app")
    ordered = sorted(rec.findings, key=lambda f: (_label(f), f.device_id))
    rows = [
        _row(
            f,
            representative(f.refs),
            version=Cell(display=_safe(f.values["version"]), value=_safe(f.values["version"])),
        )
        for f in ordered
    ]
    versions = Counter(_safe(f.values["version"]) for f in ordered)
    groups = sorted(versions.items(), key=lambda kv: parse_version(kv[0]) or ())
    summary = _sentence([f"{_word(k)} {'has' if k == 1 else 'have'} {name} {v}" for v, k in groups])
    return Presentation(
        order=_order(rows),
        table=_table(
            f"Devices with {name}",
            [Column(key="version", label="Version", kind="version")],
            rows,
            SortSpec(key="device", direction="asc"),
        ),
        summary=summary,
        scope=_software_scope(rec, _distinct(ordered)),
    )


def _new_installs(rec: ToolRecord) -> Presentation:
    days = rec.args.get("window_days") or policy.SOFTWARE_WINDOW_DAYS
    ordered = sorted(rec.findings, key=lambda f: f.values["first_seen"], reverse=True)
    rows = []
    for f in ordered:
        v = f.values
        version = _safe(v["version"])
        latest = _safe(v["latest_version"])
        app = _safe(v["app"], "unknown app")
        rows.append(
            _row(
                f,
                list(f.refs),
                app=Cell(display=app, value=app),
                version=Cell(
                    display=version if version == latest else f"{version}, now {latest}",
                    value=latest,
                ),
                first_seen=Cell(display=fmt_day(v["first_seen"]), value=v["first_seen"]),
                status=Cell(
                    display="Installed" if v["still_present"] else "No longer installed",
                    value=int(bool(v["still_present"])),
                    tone="neutral",
                ),
            )
        )
    by_app: dict[str, set[str]] = {}
    for f in ordered:
        by_app.setdefault(_safe(f.values["app"], "an unknown app"), set()).add(f.device_id)
    clauses = [
        f"{app} on {_word(len(ids))} {noun(len(ids), 'device')}"
        for app, ids in sorted(by_app.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return Presentation(
        order=_order(rows),
        table=_table(
            f"New software ({days} d)",
            [
                Column(key="app", label="App", kind="text"),
                Column(key="version", label="Version", kind="version"),
                Column(key="first_seen", label="First seen", kind="date"),
                Column(key="status", label="Status", kind="pill"),
            ],
            rows,
            SortSpec(key="first_seen", direction="desc"),
        ),
        summary=_sentence(
            clauses[:3] + ([f"{len(clauses) - 3} more apps"] if len(clauses) > 3 else [])
        ),
        scope=_software_scope(rec, _distinct(ordered), f"last {days} days of snapshots"),
    )


# ---- scope and follow-ups -----------------------------------------------------------------------


def _plain_scope(
    rec: ToolRecord, ok_label: str, *, basis: str = _LATEST_SNAPSHOT, thin: bool = False
) -> ScopeParts:
    """Counts for a check where every non-matching checked device is fine.

    `thin` checks (trends, battery) do not cover devices without enough readings: those are in the
    record's population but were not checked.
    """
    not_checked = _excluded_ids(rec, "insufficient_history", "no_reading") if thin else 0
    checked = max(rec.population - not_checked, 0)
    matched = _distinct(rec.findings)
    return ScopeParts(
        checked=checked,
        matched=matched,
        ok=max(checked - matched, 0),
        ok_label=ok_label,
        not_checked=not_checked,
        basis=basis,
    )


def _trend_ask(request: str) -> Alternative:
    return Alternative(label=f"Show their {policy.TREND_WINDOW_DAYS}-day trend", request=request)


def follow_ups_for(
    rec: ToolRecord, scope: ScopeParts | None, asks: Sequence[Alternative]
) -> list[Alternative]:
    """Questions to offer next: the devices not checked, a trend, then advice. At most four."""
    ups: list[Alternative] = []
    if scope is not None and scope.not_checked > 0:
        n = scope.not_checked
        ups.append(
            Alternative(
                label=f"Look at the {n} {noun(n, 'device')} not checked",
                request="Which devices were not checked, and why?",
            )
        )
    if rec.findings:
        ups.extend(asks)
        if rec.tool != "find_devices" and rec.tool != "software_inventory":
            ups.append(
                Alternative(
                    label="What should we do about these?",
                    request="What should we do about these devices?",
                )
            )
    return ups[:4]


_BUILDERS: dict[str, Callable[[ToolRecord], Presentation]] = {
    "low_disk_devices": _low_disk,
    "macos_version_check": _macos,
    "compliance_failures_now": _compliance_now,
    "memory_pressure_now": _memory,
    "trend": _trend,
    "find_devices": _find,
    "software_inventory": _software,
    "fleet_health": _fleet_health,
}
# Tools whose list answers offer actions: a finding there means something may need doing.
ADVISABLE_TOOLS = frozenset(
    {
        "low_disk_devices",
        "macos_version_check",
        "compliance_failures_now",
        "memory_pressure_now",
        "trend",
    }
)

# The next steps a list answer offers: only those that belong to what it answered. Keyed by the subject
# ("trend" splits by its metric); each rule is (action_type, parameter name, required value), where the
# parameter is what eligibility puts in the suggestion's params (component, topic or check_id), and no
# parameter means any suggestion of that action type. `_ANSWER_CHECKS` stands for the compliance check(s)
# the answer itself lists. An advise turn ignores this table and recommends over every rule.
_ANSWER_CHECKS = "answer"
_STORAGE_STEPS = (
    ("upgrade_order", "component", "storage"),
    ("employee_notification", "topic", "low_disk"),
)
_MEMORY_STEPS = (("upgrade_order", "component", "memory"),)
_TICKET_STEPS = (("remediation_ticket", "check_id", _ANSWER_CHECKS),)
SUBJECT_ACTIONS: dict[str, tuple[tuple[str, str | None, str | None], ...]] = {
    "low_disk_devices": _STORAGE_STEPS,
    "trend:disk": _STORAGE_STEPS,
    "memory_pressure_now": _MEMORY_STEPS,
    "trend:memory": _MEMORY_STEPS,
    # Only where the check is failing now: eligibility refuses a ticket for a check that passes.
    "macos_version_check": (("remediation_ticket", "check_id", "os_up_to_date"),),
    "compliance_failures_now": _TICKET_STEPS,
    "trend:compliance": _TICKET_STEPS,
    "trend:battery": (
        ("replacement_flag", None, None),
        ("employee_notification", "topic", "battery_service"),
    ),
}


def _subject(rec: ToolRecord) -> str:
    if rec.tool != "trend":
        return rec.tool
    metric = rec.args.get("metric")
    return f"trend:{metric if metric in ('memory', 'disk', 'compliance') else 'battery'}"


def _answer_checks(rec: ToolRecord) -> set[str]:
    if rec.tool == "trend":
        return {str(rec.args["check_id"])} if rec.args.get("check_id") else set()
    return {failing["check_id"] for f in rec.findings for failing in f.values.get("failing", [])}


def relevant_to(records: Sequence[ToolRecord]) -> Callable[[Suggestion], bool]:
    """Whether a suggestion belongs to the subject of the list answer these records make."""
    wanted: set[tuple[str, str | None, str | None]] = set()
    for rec in records:
        for action, name, value in SUBJECT_ACTIONS.get(_subject(rec), ()):
            if value == _ANSWER_CHECKS:
                wanted |= {(action, name, check) for check in _answer_checks(rec)}
            else:
                wanted.add((action, name, value))

    def accepts(suggestion: Suggestion) -> bool:
        return any(
            suggestion.action_type == action
            and (name is None or suggestion.params.get(name) == value)
            for action, name, value in wanted
        )

    return accepts


def _scope_only(rec: ToolRecord) -> ScopeParts:
    """Scope counts for a check that found nothing."""
    if rec.tool == "macos_version_check":
        unknown = _excluded_ids(rec, "unknown_version")
        return ScopeParts(
            rec.population,
            0,
            None if unknown else rec.population,
            "" if unknown else "up to date",
            _excluded_ids(rec, "out_of_scope"),
            _LATEST_SNAPSHOT,
        )
    if rec.tool == "software_inventory":
        view = rec.args.get("view") == "new_installs"
        days = rec.args.get("window_days") or policy.SOFTWARE_WINDOW_DAYS
        return _software_scope(
            rec, 0, f"last {days} days of snapshots" if view else _LATEST_SNAPSHOT
        )
    if rec.tool == "find_devices":
        return ScopeParts(rec.population, 0, None, "", 0, _LATEST_SNAPSHOT)
    if rec.tool == "fleet_health":
        return _fleet_health(rec).scope  # type: ignore[return-value]
    if rec.tool == "trend":
        metric = rec.args.get("metric")
        days = rec.args.get("window_days") or {
            "compliance": policy.COMPLIANCE_WINDOW_DAYS,
            "battery": policy.BATTERY_WINDOW_DAYS,
        }.get(metric, policy.TREND_WINDOW_DAYS)
        label = {"compliance": "passing", "battery": "with no wear signal"}.get(metric, "healthy")
        return _plain_scope(rec, label, basis=f"last {days} days of snapshots", thin=True)
    return _plain_scope(rec, "passing" if rec.tool == "compliance_failures_now" else "healthy")


def present(rec: ToolRecord) -> Presentation | None:
    """The presentation of one tool record, or None when the tool has no list view or the record is odd.

    A record that does not have the shape its tool produces is not an error here: the answer is
    complete without a table, so it is shown without one.
    """
    builder = _BUILDERS.get(rec.tool)
    if builder is None or rec.error:
        return None
    try:
        if not rec.findings:
            scope = _scope_only(rec)
            return Presentation(scope=scope, follow_ups=follow_ups_for(rec, scope, ()))
        built = builder(rec)
        built.follow_ups = follow_ups_for(rec, built.scope, built.follow_ups)
        return built
    except (KeyError, TypeError, ValueError, IndexError):
        return None
