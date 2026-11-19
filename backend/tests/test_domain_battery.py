from datetime import UTC, datetime

import pytest

from fleet_copilot.domain.battery import BatteryReading, BatterySignals, battery_signals


def reading(day, condition="Normal", capacity=4000, cycles=100):
    return BatteryReading(datetime(2026, 5, day, 9, tzinfo=UTC), condition, cycles, capacity)


def test_decline_and_trailing_service_streak():
    readings = [reading(d, "Normal", 4000 - d) for d in range(1, 11)] + [
        reading(d, "Service Recommended", 4000 - d, cycles=1000 + d) for d in range(11, 21)
    ]
    signals = battery_signals(readings)
    assert signals.readings == 20
    assert signals.capacity_change_pct == pytest.approx(-19 / 3999 * 100)
    assert signals.service_streak == 10
    assert signals.first_service_at == datetime(2026, 5, 11, 9, tzinfo=UTC)
    assert signals.latest_condition == "Service Recommended"
    assert signals.cycles_latest == 1020


def test_flat_healthy_battery():
    signals = battery_signals([reading(d) for d in range(1, 6)])
    assert signals.capacity_change_pct == 0.0
    assert signals.service_streak == 0
    assert signals.first_service_at is None


def test_empty_and_single_reading_make_no_trend_claim():
    assert battery_signals([]) == BatterySignals(0, None, None, None, 0, None)
    assert battery_signals([reading(1)]).capacity_change_pct is None


def test_capacity_change_uses_first_and_last_readings_that_report_a_capacity():
    trailing_gap = [reading(1, capacity=4000), reading(2, capacity=3800), reading(3, capacity=None)]
    assert battery_signals(trailing_gap).capacity_change_pct == pytest.approx(-5.0)
    leading_gap = [reading(1, capacity=None), reading(2, capacity=4000), reading(3, capacity=3800)]
    assert battery_signals(leading_gap).capacity_change_pct == pytest.approx(-5.0)


def test_a_single_reported_capacity_makes_no_trend_claim():
    readings = [reading(1, capacity=None), reading(2, capacity=4000), reading(3, capacity=None)]
    assert battery_signals(readings).capacity_change_pct is None


def test_cycles_latest_comes_from_the_strictly_latest_reading():
    readings = [reading(1, cycles=100), reading(2, cycles=None)]
    assert battery_signals(readings).cycles_latest is None


def test_streak_resets_when_the_latest_reading_is_normal():
    readings = [reading(1, "Service Recommended"), reading(2, "Service Recommended"), reading(3, "Normal")]
    assert battery_signals(readings).service_streak == 0


def test_readings_are_ordered_before_analysis():
    readings = [reading(3, "Service Recommended", 3900), reading(1, "Normal", 4000), reading(2, "Normal", 3950)]
    signals = battery_signals(readings)
    assert signals.capacity_change_pct == pytest.approx(-2.5)
    assert signals.service_streak == 1
