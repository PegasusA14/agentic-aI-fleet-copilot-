from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

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
