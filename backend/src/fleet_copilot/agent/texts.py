"""Fixed wording for outcomes the model must not word itself (refusals and failures).

Fixed text means the model cannot be talked into a message such as "that device belongs to
another company", and denials for foreign and nonexistent records read identically.
"""

from __future__ import annotations

NOT_FOUND = "I can't find that in your fleet."
TOOL_FAILED = "A data lookup failed, so I can't answer reliably. No changes were made."
OFF_TOPIC = "I can only answer questions about this company's managed devices."
CAPABILITY_NOTE = (
    "I can answer from hardware, OS, memory, disk, battery, installed software and compliance data."
)
_NOT_RECORDED = "aren't something the fleet data records."
# Fixed wording for a question about data the telemetry does not contain, by the planner's `missing_topic`.
MISSING_TOPIC_TEXT = {
    "network": f"Wi-Fi and network quality {_NOT_RECORDED}",
    "location": f"Where a device is or has been {_NOT_RECORDED}",
    "performance": f"CPU load, speed and temperature {_NOT_RECORDED}",
    "user_activity": f"Logins, usage and browsing activity {_NOT_RECORDED}",
    "peripherals": f"Monitors, printers and other peripherals {_NOT_RECORDED}",
    "other": "That isn't something the fleet data records.",
}
# What to offer instead of a refusal: (label, the message sent when it is chosen). Never model-written.
_ALTERNATIVES = (
    ("Fleet health summary", "Give me a fleet health summary"),
    ("Failing compliance checks", "Which devices are failing compliance checks?"),
    ("Devices low on disk", "Which devices are low on disk space?"),
)
_PERFORMANCE_ALTERNATIVE = ("Memory pressure", "Which devices are consistently RAM-constrained?")
UNSUPPORTED_ACTION = (
    "I can't do that, but I can recommend actions for devices you're looking at "
    "(ask 'what should I do?'), or propose one action for one device."
)
ACTION_NOT_ELIGIBLE = (
    "That action is not supported by the current evidence, so no proposal was made."
)
ACTION_INSUFFICIENT_EVIDENCE = (
    "There is not enough recent evidence to propose that action, so no proposal was made."
)
ACTION_ALREADY_DONE = "That action was already carried out recently, so no new proposal was made."
ACTION_TOO_MANY_PENDING = (
    "Too many proposals are waiting for a decision, so no new proposal was made."
)
ACTION_REFUSAL_HEADLINES = {
    "not_eligible": ACTION_NOT_ELIGIBLE,
    "insufficient_evidence": ACTION_INSUFFICIENT_EVIDENCE,
    "already_done": ACTION_ALREADY_DONE,
    "too_many_pending": ACTION_TOO_MANY_PENDING,
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
ADVISE_HEADLINE = "Recommended next steps from the current evidence"
ADVISE_RULE = (
    "An action is suggested only where the eligibility rules for a remediation ticket, a replacement "
    "flag, a hardware upgrade or an employee notification are met by current readings"
)


_ACTION_NOUNS = {
    "remediation_ticket": "a remediation ticket",
    "replacement_flag": "a replacement flag",
    "upgrade_order": "a hardware upgrade",
    "employee_notification": "an employee notification",
}


def alternatives_for(missing_topic: str | None = None) -> list[dict[str, str]]:
    """The fixed alternatives shown under an off-topic or missing-data refusal, at most four."""
    chosen = list(_ALTERNATIVES)
    if missing_topic == "performance":
        chosen.append(_PERFORMANCE_ALTERNATIVE)
    return [{"label": label, "request": request} for label, request in chosen]


def no_qualifying_note(action_type: str) -> str:
    """Why advice shows other actions than the one asked for: nothing qualifies for that one."""
    noun = _ACTION_NOUNS[action_type]
    return f"None of these devices qualifies for {noun}; here is what the evidence supports."


def entry_out_of_range(n: int) -> str:
    """Fixed question when an ordinal points past the end of the list that was shown."""
    return f"That list has {n} {'entry' if n == 1 else 'entries'}. Which one do you mean?"


def no_suggestion_note(hostname: str) -> str:
    """Why a device got no suggestion, in fixed words: nothing current meets an action's rules."""
    return f"{hostname}: no current reading meets the rules for a ticket, replacement, upgrade or notification."


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
