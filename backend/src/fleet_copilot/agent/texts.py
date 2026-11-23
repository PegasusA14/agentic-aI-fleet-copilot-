"""Fixed wording for outcomes the model must not word itself (refusals and failures).

Fixed text means the model cannot be talked into a message such as "that device belongs to
another company", and denials for foreign and nonexistent records read identically.
"""

from __future__ import annotations

NOT_FOUND = "I can't find that in your fleet."
TOOL_FAILED = "A data lookup failed, so I can't answer reliably. No changes were made."
NOT_RECORDED = "That question is not in the recorded answers, so replay mode can't answer it. No changes were made."
NOT_RECORDED_NOTE = (
    "Replay mode answers only questions that were recorded, worded as they were recorded. Set "
    "FLEET_MODEL_MODE=live with a model provider to ask anything."
)
OFF_TOPIC = "I can only answer questions about this company's managed devices."
CAPABILITY_NOTE = (
    "I can answer from hardware, OS, memory, disk, battery, installed software and compliance data."
)
# The capability card: what the assistant can and cannot do, written once here. The reply step reads it
# for small talk and capability questions, so it never describes the assistant from memory of its own.
CAPABILITY_CARD = (
    "What the data holds: each device's hardware and model, OS and OS version, memory, primary disk space, "
    "battery health, installed software and three compliance checks (screen lock, OS up to date, disk "
    "encryption), as readings taken over time for this one company. "
    "Questions it answers: lists such as low disk, memory pressure, devices behind on OS updates or failing a "
    "compliance check, newly installed software and hardware inventory by model; trends over a window for "
    "disk, memory, battery and compliance; ranked insights with hedged estimates; an overall fleet health "
    "summary; and what to do next for the devices on screen. "
    "Actions: four, each only a proposal that a person must approve before anything happens: open a "
    "remediation ticket, flag a device for replacement, order a memory or storage upgrade, and notify the "
    "assigned employee. One device per proposal. "
    "What it cannot see: network and Wi-Fi quality, device location, CPU load or speed, what users do on a "
    "device, and peripherals such as monitors and printers."
)
# What a chat turn shows as its headline; the reply the model wrote goes under it.
CHAT_HEADLINE = "Happy to help with your fleet."
# "Forget that" is answered by code, so the words never claim a note is still held: the model's reply
# to it used to say "Understood, I'll focus on laptops only" right after the note was dropped.
NOTE_FORGOTTEN = "Done, I've forgotten that. Later answers won't use it."
NO_NOTE_TO_FORGET = "There was nothing to forget in this conversation."
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
OTHER_COMPANY_NOTE = (
    "I can't read or act on another company's devices from this session. "
    "To look at a different company, switch to it first."
)
OTHER_COMPANY_NO_NAME = "You're working in this company, so I can only see this company."


def other_company_headline(own_company_name: str) -> str:
    """The reply to a general question about another company. It names only the user's own company."""
    name = own_company_name.strip()
    if not name:
        return OTHER_COMPANY_NO_NAME
    return f"You're working in {name}, so I can only see {name}."


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
PARTIAL_NOT_FOUND = "Part of the request referred to a device that could not be found, so that part was not answered."


ROUND_LIMIT_NOTE = "I stopped gathering evidence after the maximum number of tool rounds, so this answer may be incomplete."
INCOMPLETE_NOTE = "Part of this question could not be answered; some requested checks did not run."
NEED_REFERENCE = "I don't have a previous list to refer to. Which devices do you mean?"
WHICH_LIST = "Which list do you mean?"
DEFAULT_CLARIFICATION = "I need one more detail to answer that. What would you like me to look at?"
CLARIFICATION_HEADLINE = "I need one detail to answer that"
NO_SUGGESTIONS = "None of these devices supports one of the four actions right now."
ADVISE_NEEDS_DEVICES = "Which devices do you want recommendations for?"
ADVISE_HEADLINE = "Here are the next steps the current readings support."
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


def device_choice_question(phrase: str | None, count: int) -> str:
    """Ask which of `count` devices is meant; `phrase` is quoted only when it is short plain text."""
    if phrase is None:
        return f"That matches {count} devices. Which one do you mean?"
    return f"'{phrase}' matches {count} devices. Which one do you mean?"


def company_typo_question(own_company_name: str) -> str:
    return f"Did you mean {own_company_name}?"


def company_typo_label(own_company_name: str) -> str:
    return f"Yes, {own_company_name}"


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
