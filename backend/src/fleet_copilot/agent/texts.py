"""Fixed wording for outcomes the model must not word itself (refusals and failures).

Fixed text means the model cannot be talked into a message such as "that device belongs to
another company", and denials for foreign and nonexistent records read identically.
"""

from __future__ import annotations

NOT_FOUND = "I can't find that in your fleet."
TOOL_FAILED = "A data lookup failed, so I can't answer reliably. No changes were made."
OFF_TOPIC = "I can only answer questions about this company's managed devices."
UNSUPPORTED_ACTION = (
    "Creating or changing anything isn't available yet. I can only look things up for now."
)
PROMPT_INJECTION = (
    "I can't follow instructions that change how I work. I answer from your fleet's telemetry only."
)
STEP_LIMIT = "I couldn't gather enough evidence within the allowed number of steps."
INSUFFICIENT_HISTORY = "There are not enough snapshots in that window to report a trend."


def no_match(population: int) -> str:
    return f"No devices matched ({population} evaluated)"
