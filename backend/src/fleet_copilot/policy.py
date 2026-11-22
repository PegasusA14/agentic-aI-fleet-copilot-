"""Single source of truth for the disclosed policy defaults (design spec section 2).

Pure constants. Queries, tools and the composer import these so a threshold is never
written down twice, and every answer can disclose the rule it used.
"""

from __future__ import annotations

LOW_DISK_FREE_PCT = 10.0  # primary volume free space below this is "low"
MEMORY_USED_PCT = 85.0  # memory used at or above this is "pressure"
MEMORY_USED_PCT_HIGH = 90.0  # the stricter threshold the golden suite also exercises
BATTERY_DECLINE_PCT = 1.0  # capacity drop vs the device's own first reading that counts
STALE_HOURS = 48  # a reading older than this before as-of is stale
TREND_WINDOW_DAYS = 7  # default window for memory and disk trends
COMPLIANCE_WINDOW_DAYS = 30
BATTERY_WINDOW_DAYS = 30
SOFTWARE_WINDOW_DAYS = 30  # "newly installed" looks this far back from the as-of time
REPLACEMENT_MIN_SNAPSHOTS = (
    3  # consecutive "Service Recommended" battery readings to propose a flag
)
BATTERY_SERVICE_INDEX = (
    90.0  # OUR service threshold: 90% of the battery's own first reading in the window
)
MIN_FIT_POINTS = 7  # fewest capacity readings a straight-line projection is made from
PROJECTION_HORIZON_DAYS = (
    180  # a crossing later than this many days out is "not within the horizon"
)
ACT_SOON_PROJECTION_DAYS = (
    30  # a battery projected to reach the service threshold this soon is act soon
)
MEMORY_UPGRADE_FACTOR = 2  # a memory upgrade order multiplies the current total by this
STORAGE_SIZES_GIB = (256, 512, 1024, 2048)  # the standard sizes a storage upgrade steps through
UPGRADE_TREND_WINDOW_DAYS = (
    7  # a sustained memory or disk trend over this window supports an upgrade
)
MAX_WINDOW_DAYS = 60  # largest window a tool call may request
MAX_REFS_PER_FINDING = 6  # cited readings shown per device in an answer
MAX_TOOL_ROUNDS = 6  # model-driven rounds of tool calls in one turn
MAX_CALLS_PER_ROUND = 4  # tool calls the graph will run from one model message
TURN_TIMEOUT_S = (
    120  # wall-clock limit for one whole turn, so a hung gateway cannot hold the conversation lock
)
MODEL_CALL_TIMEOUT_S = 30.0  # one attempt at one model call
MODEL_ATTEMPTS = 3  # attempts per model call (rate limit, timeout, connection and 5xx errors only)
MODEL_BACKOFF_S = (1.0, 3.0)  # pause before the 2nd and 3rd attempt
MAX_QUESTION_CHARS = 2000  # longest question a turn accepts
MAX_HISTORY_TURNS = 6  # earlier turns the planner may see
MAX_HEADLINE_CHARS = 160  # longest model-written headline that passes verification
MAX_EXPLANATION_CHARS = 280  # longest model-written explanation that passes verification
MAX_EXPLANATION_SENTENCES = 2  # most sentences a model-written explanation may have
MAX_SUGGESTIONS = 6  # suggested actions one advise reply offers
ACTION_REPEAT_WINDOW_DAYS = 30  # an action already executed this recently is not proposed again
MAX_PENDING_PER_TENANT = 20  # proposals one company may have waiting for a decision at once


def disclosure() -> dict[str, float | int]:
    """The defaults as a plain mapping, for the API and for tests."""
    return {
        "low_disk_free_pct": LOW_DISK_FREE_PCT,
        "memory_used_pct": MEMORY_USED_PCT,
        "memory_used_pct_high": MEMORY_USED_PCT_HIGH,
        "battery_decline_pct": BATTERY_DECLINE_PCT,
        "stale_hours": STALE_HOURS,
        "trend_window_days": TREND_WINDOW_DAYS,
        "compliance_window_days": COMPLIANCE_WINDOW_DAYS,
        "battery_window_days": BATTERY_WINDOW_DAYS,
        "software_window_days": SOFTWARE_WINDOW_DAYS,
        "replacement_min_snapshots": REPLACEMENT_MIN_SNAPSHOTS,
        "battery_service_index": BATTERY_SERVICE_INDEX,
        "min_fit_points": MIN_FIT_POINTS,
        "projection_horizon_days": PROJECTION_HORIZON_DAYS,
        "act_soon_projection_days": ACT_SOON_PROJECTION_DAYS,
        "memory_upgrade_factor": MEMORY_UPGRADE_FACTOR,
        "upgrade_trend_window_days": UPGRADE_TREND_WINDOW_DAYS,
    }
