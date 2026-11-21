"""Fixed wording for outcomes the model must not word itself (refusals and failures).

Fixed text means the model cannot be talked into a message such as "that device belongs to
another company", and denials for foreign and nonexistent records read identically.
"""

from __future__ import annotations

NOT_FOUND = "I can't find that in your fleet."
TOOL_FAILED = "A data lookup failed, so I can't answer reliably. No changes were made."
OFF_TOPIC = "I can only answer questions about this company's managed devices."
UNSUPPORTED_ACTION = (
    "I can propose one device at a time: a remediation ticket, a replacement flag, a memory or "
    "storage upgrade, or an employee notification. Approvals happen in the review screen."
)
ACTION_NOT_ELIGIBLE = (
    "That action is not supported by the current evidence, so no proposal was made."
)
ACTION_INSUFFICIENT_EVIDENCE = (
    "There is not enough recent evidence to propose that action, so no proposal was made."
)
ACTION_REFUSAL_HEADLINES = {
    "not_eligible": ACTION_NOT_ELIGIBLE,
    "insufficient_evidence": ACTION_INSUFFICIENT_EVIDENCE,
}
ACTION_LABELS = {
    "remediation_ticket": "open a remediation ticket",
    "replacement_flag": "flag the device for replacement",
    "upgrade_order": "order a hardware upgrade",
    "employee_notification": "notify the assigned employee",
}
PROMPT_INJECTION = (
    "I can't follow instructions that change how I work. I answer from your fleet's telemetry only."
)
STEP_LIMIT = "I couldn't gather enough evidence within the allowed number of steps."
INSUFFICIENT_HISTORY = "There are not enough snapshots in that window to report a trend."
PARTIAL_NOT_FOUND = "Part of the request referred to a device that could not be found, so that part was not answered."


ROUND_LIMIT_NOTE = "I stopped gathering evidence after the maximum number of tool rounds, so this answer may be incomplete."
INCOMPLETE_NOTE = "Part of this question could not be answered; some requested checks did not run."
NEED_REFERENCE = "I don't have a previous list to refer to. Which devices do you mean?"
WHICH_LIST = "Which list do you mean?"
DEFAULT_CLARIFICATION = "I need one more detail to answer that. What would you like me to look at?"
CLARIFICATION_HEADLINE = "I need one detail to answer that"
NO_SUGGESTIONS = "None of these devices supports one of the four actions right now."
ADVISE_NEEDS_DEVICES = "Which devices do you want recommendations for?"


def entry_out_of_range(n: int) -> str:
    """Fixed question when an ordinal points past the end of the list that was shown."""
    return f"That list has {n} {'entry' if n == 1 else 'entries'}. Which one do you mean?"


def unresolved_note(n: int) -> str:
    """How many requested device ids were left out. Foreign and nonexistent ids are counted alike."""
    noun, was = ("device", "was") if n == 1 else ("devices", "were")
    return f"{n} requested {noun} could not be found in your fleet and {was} not included."


def proposal_headline(action_label: str, target_label: str) -> str:
    """The fixed headline of a proposal card: it says plainly that nothing has happened yet."""
    return (
        f"Proposed: {action_label} for {target_label}. "
        "It waits for your approval; nothing has changed yet."
    )


def no_match(population: int) -> str:
    return f"No devices matched ({population} evaluated)"
