from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fleet_copilot import policy
from fleet_copilot.domain.fit import NO_PROJECTION, LineFit, Projection, fit_line, project_crossing
from fleet_copilot.domain.trends import MIN_SNAPSHOTS

SERVICE_RECOMMENDED = "Service Recommended"


@dataclass(frozen=True)
class BatteryReading:
    collected_at: datetime
    condition: str | None
    cycle_count: int | None
    full_charge_capacity: int | None


@dataclass(frozen=True)
class BatterySignals:
    """What the telemetry supports saying. There is no design capacity, so no health percentage."""

    readings: int
    # change between the first and last readings that report a capacity (readings without one are skipped)
    capacity_change_pct: float | None
    cycles_latest: (
        int | None
    )  # taken from the strictly latest reading, even if that reading has no capacity
    latest_condition: str | None
    service_streak: int  # consecutive latest readings with the vendor 'Service Recommended' flag
    first_service_at: datetime | None  # start of that streak


def battery_signals(readings: Sequence[BatteryReading]) -> BatterySignals:
    ordered = sorted(readings, key=lambda r: r.collected_at)
    if not ordered:
        return BatterySignals(0, None, None, None, 0, None)
    capacities = [r.full_charge_capacity for r in ordered if r.full_charge_capacity is not None]
    change = None
    if len(capacities) >= 2 and capacities[0] > 0:
        change = (capacities[-1] - capacities[0]) / capacities[0] * 100.0
    streak = 0
    first_service_at = None
    for r in reversed(ordered):
        if r.condition != SERVICE_RECOMMENDED:
            break
        streak += 1
        first_service_at = r.collected_at
    return BatterySignals(
        readings=len(ordered),
        capacity_change_pct=change,
        cycles_latest=ordered[-1].cycle_count,
        latest_condition=ordered[-1].condition,
        service_streak=streak,
        first_service_at=first_service_at,
    )


# ---- capacity index, fit and severity -----------------------------------------------------------

Severity = Literal["act_soon", "watch", "ok"]


def capacity_index(readings: Sequence[BatteryReading]) -> list[tuple[datetime, float]]:
    """Each capacity reading as a percentage of the battery's OWN first capacity reading in the window.

    100.0 is that first reading. There is no design capacity in the telemetry, so this is not a health
    percentage. Readings without a capacity are skipped; an empty list if the first capacity is not positive.
    """
    ordered = sorted(
        (r for r in readings if r.full_charge_capacity is not None), key=lambda r: r.collected_at
    )
    if not ordered or not ordered[0].full_charge_capacity or ordered[0].full_charge_capacity <= 0:
        return []
    first = ordered[0].full_charge_capacity
    return [(r.collected_at, r.full_charge_capacity / first * 100.0) for r in ordered]


@dataclass(frozen=True)
class BatteryFit:
    """The straight-line fit over a capacity index series, and where it reaches our service threshold."""

    fit: LineFit | None
    projection: Projection
    rate_per_day: float | None  # index points per day (the slope); None without a fit


def fit_capacity_index(index: Sequence[tuple[datetime, float]], as_of: datetime) -> BatteryFit:
    """Fit the index series (needs `MIN_SNAPSHOTS` points) and project it to `policy.BATTERY_SERVICE_INDEX`."""
    if len(index) < MIN_SNAPSHOTS:
        return BatteryFit(None, NO_PROJECTION, None)
    origin = index[0][0]
    fit = fit_line([((at - origin).total_seconds() / 86400.0, value) for at, value in index])
    projection = project_crossing(
        fit, threshold=policy.BATTERY_SERVICE_INDEX, origin=origin, as_of=as_of
    )
    return BatteryFit(fit, projection, None if fit is None else fit.slope)


def battery_severity(
    *, service_streak: int, capacity_change_pct: float | None, projection: Projection
) -> Severity:
    """How soon a battery needs attention.

    act_soon: the vendor's Service Recommended flag on at least `REPLACEMENT_MIN_SNAPSHOTS` consecutive
      latest readings, OR the estimate reaches our service threshold within `ACT_SOON_PROJECTION_DAYS`.
    watch: capacity down more than `BATTERY_DECLINE_PCT` from its first reading in the window, or a
      shorter Service Recommended streak, without being act_soon.
    ok: neither.
    """
    if service_streak >= policy.REPLACEMENT_MIN_SNAPSHOTS:
        return "act_soon"
    if (
        projection.status == "projected"
        and projection.days_to_estimate is not None
        and projection.days_to_estimate <= policy.ACT_SOON_PROJECTION_DAYS
    ):
        return "act_soon"
    declining = (
        capacity_change_pct is not None and capacity_change_pct < -policy.BATTERY_DECLINE_PCT
    )
    if declining or service_streak > 0:
        return "watch"
    return "ok"
