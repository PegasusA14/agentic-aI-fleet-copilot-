"""Insights: one code-written card per device and finding, from the values a tool record carries.

Every word and number here comes from `FindingRecord.values` (themselves computed by the fleet queries).
An estimate is labelled as one, with its method; a threshold is labelled as OUR policy value. The telemetry
has no battery design capacity, so battery wear is always capacity against the battery's own first reading
in the window (index 100 = that reading), never a health percentage.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from fleet_copilot import policy
from fleet_copilot.actions.eligibility import display_hostname
from fleet_copilot.agent.format import fmt_day, fmt_pct, fmt_signed_pct, noun
from fleet_copilot.agent.records import FindingRecord
from fleet_copilot.contracts import (
    MAX_INSIGHT_TEXT_CHARS,
    MAX_SERIES_POINTS,
    ActionType,
    Insight,
    InsightFit,
    InsightProjection,
    InsightSeries,
    KeyValue,
    SeriesPoint,
    SeriesThreshold,
)
from fleet_copilot.fleet.health import CATEGORY_ORDER, SEVERITY_ORDER

MAX_REFS_PER_INSIGHT = 4

_BREACH_TEXT = {
    "sustained": "at least 80% of the window's snapshots breached the limit and the latest one does too",
    "onset": "the breach began inside the window and is still going",
    "intermittent": "the limit was breached on and off, with no sustained run",
}


def day_text(day: date) -> str:
    """`2026-06-20` -> `Jun 20`."""
    return f"{day:%b} {day.day}"


def _day(iso: str | None) -> date | None:
    return None if iso is None else date.fromisoformat(iso)


def estimate_phrase(projection: InsightProjection | None) -> str | None:
    """The date-range wording of a projected estimate, or None. The prose gate lets the model repeat it."""
    if projection is None or projection.status != "projected" or projection.estimate is None:
        return None
    earliest, latest = projection.earliest or projection.estimate, projection.latest
    if latest is None:
        return f"as early as {day_text(earliest)}"
    if earliest == latest:
        return f"around {day_text(earliest)}"
    return f"between {day_text(earliest)} and {day_text(latest)}"


def estimates_of(insights: Sequence[Insight]) -> list[str]:
    """The distinct estimate phrases the insights' findings contain, for the prose verifier."""
    return list(dict.fromkeys(p for p in (estimate_phrase(i.projection) for i in insights) if p))


def _clip(text: str, limit: int = MAX_INSIGHT_TEXT_CHARS) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _thin(points: list[dict], limit: int = MAX_SERIES_POINTS) -> list[dict]:
    """At most `limit` points, keeping the first and the last and spreading the rest evenly."""
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[i] for i in sorted({round(k * step) for k in range(limit)})]


def _points(raw: list[dict]) -> list[SeriesPoint]:
    return [
        SeriesPoint(at=datetime.fromisoformat(p["at"]), value=float(p["value"])) for p in _thin(raw)
    ]


def _refs(finding: FindingRecord) -> list[str]:
    unique = list(dict.fromkeys(finding.refs))
    if len(unique) <= MAX_REFS_PER_INSIGHT:
        return unique
    step = (len(unique) - 1) / (MAX_REFS_PER_INSIGHT - 1)
    return [unique[i] for i in sorted({round(k * step) for k in range(MAX_REFS_PER_INSIGHT)})]


# ---- battery ------------------------------------------------------------------------------------


def _battery_finding(label: str, v: dict, projection: InsightProjection | None) -> str:
    if projection is not None and projection.status == "projected":
        return (
            f"{label}'s battery is estimated to reach our service threshold "
            f"{estimate_phrase(projection)}."
        )
    if projection is not None and projection.status == "beyond_horizon":
        return (
            f"{label}'s battery is not estimated to reach our service threshold within "
            f"{policy.PROJECTION_HORIZON_DAYS} days."
        )
    fit = v.get("fit")
    if fit is None:
        return f"{label}'s battery has too few capacity readings for an estimate."
    if fit["n"] < policy.MIN_FIT_POINTS:
        return (
            f"{label}'s battery has {fit['n']} capacity readings, fewer than the "
            f"{policy.MIN_FIT_POINTS} an estimate needs."
        )
    return f"{label}'s battery capacity is not trending down, so no estimate is made."


def _battery_why(v: dict) -> str:
    streak, change = v["service_streak"], v["capacity_change_pct"]
    severity = v["severity"]
    drop = (
        ""
        if change is None
        else f" Capacity is {abs(change):.1f}% below its first reading in the window."
    )
    if streak >= policy.REPLACEMENT_MIN_SNAPSHOTS:
        return _clip(
            f"The vendor flagged Service Recommended on the last {streak} readings in a row, which "
            f"meets our rule of {policy.REPLACEMENT_MIN_SNAPSHOTS}.{drop}"
        )
    projection = v["projection"]
    if severity == "act_soon" and projection["status"] == "projected":
        return _clip(
            f"The straight-line estimate reaches our service threshold within "
            f"{policy.ACT_SOON_PROJECTION_DAYS} days.{drop}"
        )
    if streak > 0:
        return _clip(
            f"The vendor flagged Service Recommended on the last {streak} "
            f"{noun(streak, 'reading')}; our rule acts at {policy.REPLACEMENT_MIN_SNAPSHOTS}.{drop}"
        )
    return _clip(
        f"Capacity is down more than {policy.BATTERY_DECLINE_PCT:g}% from its first reading in the "
        "window, our watch limit."
        if change is not None
        else "Battery wear signals are present."
    )


def battery_insight(f: FindingRecord) -> Insight:
    v = f.values
    label = display_hostname(f.hostname, f.device_id)
    projection = InsightProjection(
        status=v["projection"]["status"],
        estimate=_day(v["projection"]["estimate"]),
        earliest=_day(v["projection"]["earliest"]),
        latest=_day(v["projection"]["latest"]),
    )
    fit = v.get("fit")
    index_series = v["index_series"]
    kv: list[KeyValue] = []
    if v["capacity_change_pct"] is not None:
        kv.append(
            KeyValue(
                label="Capacity vs its first reading",
                value=fmt_signed_pct(v["capacity_change_pct"]),
            )
        )
    if index_series:
        kv.append(
            KeyValue(
                label="Capacity index now",
                value=f"{index_series[-1]['value']:.1f} (first reading = 100)",
            )
        )
    if v["rate_per_day"] is not None:
        kv.append(KeyValue(label="Rate", value=f"{v['rate_per_day']:+.2f} index points per day"))
    if v["cycles_latest"] is not None:
        kv.append(KeyValue(label="Cycles", value=str(v["cycles_latest"])))
    if v["service_streak"]:
        kv.append(
            KeyValue(
                label="Vendor condition",
                value=f"Service Recommended since {fmt_day(v['first_service_at'])} ({v['service_streak']} readings)",
            )
        )
    elif v["latest_condition"]:
        kv.append(KeyValue(label="Vendor condition", value=str(v["latest_condition"])[:60]))
    estimated = fit is not None
    n = fit["n"] if fit else v["readings"]
    method = (
        f"Estimate · straight-line fit · {n} snapshots"
        if estimated
        else f"Observed · {n} snapshots"
    )
    actions: list[ActionType] = (
        ["replacement_flag", "employee_notification"]
        if v["service_streak"] >= policy.REPLACEMENT_MIN_SNAPSHOTS
        else ["employee_notification"]
    )
    return Insight(
        device_id=f.device_id,
        label=label,
        category="battery",
        severity=v["severity"],
        finding=_clip(_battery_finding(label, v, projection)),
        evidence_kv=kv,
        series=InsightSeries(
            unit="Capacity index (the battery's first reading in the window = 100)",
            points=_points(index_series),
            threshold=SeriesThreshold(
                value=policy.BATTERY_SERVICE_INDEX,
                label=f"Our service threshold ({policy.BATTERY_SERVICE_INDEX:g}% of its first reading)",
                source="policy",
            ),
        ),
        fit=None
        if fit is None
        else InsightFit(slope_per_day=fit["slope_per_day"], r2=fit["r2"], n=fit["n"]),
        projection=projection,
        method=method,
        why=_battery_why(v),
        refs=_refs(f),
        suggested_action_types=actions,
    )


# ---- disk and memory ----------------------------------------------------------------------------


def _breach_insight(f: FindingRecord, category: str) -> Insight:
    v = f.values
    label = display_hostname(f.hostname, f.device_id)
    thr, days = v["threshold"], v["window_days"]
    breached, snapshots, latest = v["breached"], v["snapshots"], v["latest_value"]
    trend = v["trend"]
    if category == "disk":
        limit = f"under {thr:g}% free"
        measure, unit = "disk space free", "Free disk space (% of the primary volume)"
        series_label = f"Low-disk limit ({thr:g}% free)"
        subject = "disk space"
        latest_label, actions = "Free space now", ["upgrade_order", "employee_notification"]
    else:
        limit = f"at or above {thr:g}%"
        measure, unit = "memory used", "Memory used (% of installed memory)"
        series_label = f"Memory-pressure limit ({thr:g}% used)"
        subject = "memory use"
        latest_label, actions = "Used now", ["upgrade_order"]
    now = f"{fmt_pct(latest)} {'free' if category == 'disk' else 'used'}"
    if trend == "sustained":
        finding = (
            f"{label} has had {subject} {limit} in {breached} of {snapshots} snapshots over the "
            f"last {days} days, and still does ({now})."
        )
    elif trend == "onset":
        finding = (
            f"{label} began having {subject} {limit} inside the last {days} days and still does "
            f"({breached} of {snapshots} snapshots; {now} now)."
        )
    else:
        finding = (
            f"{label} has had {subject} {limit} on and off over the last {days} days "
            f"({breached} of {snapshots} snapshots; {now} now)."
        )
    return Insight(
        device_id=f.device_id,
        label=label,
        category=category,  # type: ignore[arg-type]
        severity=v["severity"],
        finding=_clip(finding),
        evidence_kv=[
            KeyValue(label=latest_label, value=fmt_pct(latest)),
            KeyValue(label="Snapshots past the limit", value=f"{breached} of {snapshots}"),
            KeyValue(label="Pattern", value=trend.capitalize()),
            KeyValue(label="Window", value=f"{days} days"),
        ],
        series=InsightSeries(
            unit=unit,
            points=_points(v["series"]),
            threshold=SeriesThreshold(value=thr, label=series_label, source="policy"),
        ),
        method=f"Observed · {breached} of {snapshots} snapshots past the limit ({measure})",
        why=_clip(f"{trend.capitalize()}: {_BREACH_TEXT[trend]}."),
        refs=_refs(f),
        suggested_action_types=actions,  # type: ignore[arg-type]
    )


# ---- compliance ---------------------------------------------------------------------------------


def compliance_insight(f: FindingRecord) -> Insight:
    v = f.values
    label = display_hostname(f.hostname, f.device_id)
    check, kind, snapshots = v["check_id"], v["kind"], v["snapshots"]
    high = v["check_severity"] in ("high", "critical")
    if kind == "transition":
        finding = f"{label}'s {check} check changed from pass to fail on {fmt_day(v['change_at'])} and still fails."
        why = (
            f"The {v['check_severity']}-severity {check} check passed earlier in the window and fails at "
            "the latest snapshot."
        )
        when = KeyValue(label="Failing since", value=fmt_day(v["change_at"]))
    else:
        finding = f"{label}'s {check} check has failed in every snapshot in the window."
        why = f"A {v['check_severity']}-severity check is failing at the latest snapshot."
        when = KeyValue(label="Failing since", value="The start of the window")
    return Insight(
        device_id=f.device_id,
        label=label,
        category="compliance",
        severity=v["severity"],
        finding=_clip(finding),
        evidence_kv=[
            KeyValue(label="Check", value=check),
            KeyValue(label="Severity", value=str(v["check_severity"])),
            when,
        ],
        series=InsightSeries(
            unit="Check result per snapshot (1 = pass, 0 = fail)", points=_points(v["series"])
        ),
        method=f"Observed · {snapshots} snapshots",
        why=_clip(why),
        refs=_refs(f),
        suggested_action_types=["remediation_ticket", "employee_notification"]
        if high or kind == "transition"
        else ["remediation_ticket"],
    )


def insight_for(f: FindingRecord) -> Insight:
    """The insight of one finding of `fleet_health` (its values name the category)."""
    category = f.values["category"]
    if category == "battery":
        return battery_insight(f)
    if category == "compliance":
        return compliance_insight(f)
    return _breach_insight(f, category)


def rank(insights: Sequence[Insight]) -> list[Insight]:
    """Most urgent first, then category order, then device id (a stable sort keeps ties in order)."""
    return sorted(
        insights,
        key=lambda i: (
            SEVERITY_ORDER.index(i.severity),
            CATEGORY_ORDER.index(i.category),
            i.device_id,
        ),
    )
