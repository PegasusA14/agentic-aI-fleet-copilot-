import inspect
from datetime import timedelta

from fleet_copilot import policy
from fleet_copilot.domain.windows import STALE_AFTER
from fleet_copilot.fleet import queries as q


def test_policy_values_are_the_disclosed_defaults():
    assert policy.LOW_DISK_FREE_PCT == 10.0
    assert policy.MEMORY_USED_PCT == 85.0
    assert policy.MEMORY_USED_PCT_HIGH == 90.0
    assert policy.BATTERY_DECLINE_PCT == 1.0
    assert policy.STALE_HOURS == 48
    assert (
        policy.TREND_WINDOW_DAYS,
        policy.COMPLIANCE_WINDOW_DAYS,
        policy.BATTERY_WINDOW_DAYS,
    ) == (7, 30, 30)
    assert policy.SOFTWARE_WINDOW_DAYS == 30
    assert policy.MAX_WINDOW_DAYS == 60
    assert policy.MAX_REFS_PER_FINDING == 6
    assert (policy.BATTERY_SERVICE_INDEX, policy.MIN_FIT_POINTS) == (90.0, 7)
    assert (policy.PROJECTION_HORIZON_DAYS, policy.ACT_SOON_PROJECTION_DAYS) == (180, 30)


def test_stale_limit_comes_from_policy():
    assert STALE_AFTER == timedelta(hours=policy.STALE_HOURS)


def test_query_defaults_come_from_policy():
    def default(fn, name):
        return inspect.signature(fn).parameters[name].default

    assert default(q.low_disk, "threshold_pct") == policy.LOW_DISK_FREE_PCT
    assert default(q.disk_trend, "threshold_pct") == policy.LOW_DISK_FREE_PCT
    assert default(q.memory_trend, "threshold_pct") == policy.MEMORY_USED_PCT
    assert default(q.memory_state, "threshold_pct") == policy.MEMORY_USED_PCT
    assert default(q.memory_trend, "window_days") == policy.TREND_WINDOW_DAYS
    assert default(q.disk_trend, "window_days") == policy.TREND_WINDOW_DAYS
    assert default(q.compliance_trend, "window_days") == policy.COMPLIANCE_WINDOW_DAYS
    assert default(q.battery_overview, "window_days") == policy.BATTERY_WINDOW_DAYS


def test_disclosure_lists_every_default():
    assert policy.disclosure() == {
        "low_disk_free_pct": 10.0,
        "memory_used_pct": 85.0,
        "memory_used_pct_high": 90.0,
        "battery_decline_pct": 1.0,
        "stale_hours": 48,
        "trend_window_days": 7,
        "compliance_window_days": 30,
        "battery_window_days": 30,
        "software_window_days": 30,
        "replacement_min_snapshots": 3,
        "battery_service_index": 90.0,
        "min_fit_points": 7,
        "projection_horizon_days": 180,
        "act_soon_projection_days": 30,
        "memory_upgrade_factor": 2,
        "upgrade_trend_window_days": 7,
    }


def test_upgrade_and_replacement_policy():
    assert policy.REPLACEMENT_MIN_SNAPSHOTS == 3
    assert policy.MEMORY_UPGRADE_FACTOR == 2
    assert policy.STORAGE_SIZES_GIB == (256, 512, 1024, 2048)
    assert policy.UPGRADE_TREND_WINDOW_DAYS == 7


def test_agent_loop_limits():
    assert policy.MAX_TOOL_ROUNDS == 6
    assert policy.MAX_CALLS_PER_ROUND == 4
    assert policy.MAX_QUESTION_CHARS == 2000
    assert policy.MAX_HISTORY_TURNS == 6
    assert (policy.MAX_HEADLINE_CHARS, policy.MAX_REPLY_CHARS) == (160, 900)
    assert policy.MAX_REPLY_SENTENCES == 6
    assert policy.MAX_SUGGESTIONS == 6
