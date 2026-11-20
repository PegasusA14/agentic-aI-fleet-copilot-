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
MAX_WINDOW_DAYS = 60  # largest window a tool call may request
MAX_REFS_PER_FINDING = 6  # cited readings shown per device in an answer
MAX_TOOL_ROUNDS = 6  # model-driven rounds of tool calls in one turn
MAX_CALLS_PER_ROUND = 4  # tool calls the graph will run from one model message
TURN_TIMEOUT_S = (
    90  # wall-clock limit for one whole turn; per-call timeouts belong to the 2C gateways
)
MAX_QUESTION_CHARS = 2000  # longest question a turn accepts
MAX_HISTORY_TURNS = 6  # earlier turns the planner may see
MAX_HEADLINE_CHARS = 160  # longest model-written headline that passes verification
MAX_EXPLANATION_CHARS = 600  # longest model-written explanation that passes verification


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
    }
