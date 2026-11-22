"""Clarifications that code builds from the company's own data, and the checks around company names.

Three things live here, none of which lets the model write what the user reads:

* `device_clarification`: a phrase such as "the macbook" matches several of this company's devices. The
  options are those devices by display hostname, plus "All of them", and each option carries the whole
  request to send (the user's question with that device named), not just its label.
* `company_typo_clarification`: the user wrote a name that is a near miss of their OWN company ("Acmee").
* `names_specific_identifier`: whether a question names one particular device id, hostname or employee id.
  A question that does is always answered with the uniform "not found", never with the company card.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from fleet_copilot.actions.eligibility import display_hostname
from fleet_copilot.agent import texts
from fleet_copilot.contracts import Alternative
from fleet_copilot.repository import Device

MAX_DEVICE_CHOICES = 4
ALL_OF_THEM = "All of them"
_MAX_REQUEST = 160  # `Alternative.request`
_MAX_LABEL = 60
_MAX_QUOTED_PHRASE = 24
_MIN_PHRASE = 3
_MIN_COMPANY_NAME = 4
_MAX_TYPO_DISTANCE = 2

_PLAIN_PHRASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]*")
_ARTICLE = r"(?:(?:the|my|our|that|this|a|an)\s+)?"
# A singular verb right next to the phrase, and the plural that fits "all <phrase>s".
_PLURAL_VERB = {"is": "are", "does": "do", "has": "have", "was": "were"}


@dataclass(frozen=True)
class Clarify:
    """A code-written clarification: the question and the answers to it, each with its full request."""

    question: str
    choices: tuple[Alternative, ...]


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def matching_devices(phrase: str, devices: Sequence[Device]) -> list[Device]:
    """Devices whose hostname contains every word of `phrase`, ordered by display hostname."""
    words = _tokens(phrase)
    if not words or len(phrase.strip()) < _MIN_PHRASE:
        return []
    found = [d for d in devices if all(w in d.hostname.lower() for w in words)]
    return sorted(found, key=lambda d: display_hostname(d.hostname, d.device_id).lower())


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"\b{_ARTICLE}{re.escape(phrase.strip())}s?\b", re.IGNORECASE)


def _substitute(question: str, phrase: str, replacement: str) -> str | None:
    """The question with its one mention of `phrase` replaced, or None when that is not safe.

    Not safe means the phrase is missing, appears more than once, or the result is too long to send.
    """
    pattern = _phrase_pattern(phrase)
    hits = pattern.findall(question)
    if len(hits) != 1:
        return None
    result = pattern.sub(lambda _: replacement, question, count=1)
    return result if len(result) <= _MAX_REQUEST else None


def _all_of_them(question: str, phrase: str) -> str | None:
    """The question asking about every match: "all <phrase>s", with the verb next to it made plural."""
    plural = f"all {phrase.strip().lower()}s"
    swapped = _substitute(question, phrase, plural)
    if swapped is None:
        return None
    verb = re.compile(
        rf"\b({'|'.join(_PLURAL_VERB)})(\s+){re.escape(plural)}|{re.escape(plural)}(\s+)({'|'.join(_PLURAL_VERB)})\b",
        re.IGNORECASE,
    )

    def fix(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{_PLURAL_VERB[match.group(1).lower()]}{match.group(2)}{plural}"
        return f"{plural}{match.group(3)}{_PLURAL_VERB[match.group(4).lower()]}"

    return verb.sub(fix, swapped, count=1)


def _with_suffix(question: str, suffix: str) -> str | None:
    request = f"{question.strip()} ({suffix})"
    return request if len(request) <= _MAX_REQUEST else None


def device_clarification(question: str, phrase: str, devices: Sequence[Device]) -> Clarify | None:
    """Ask which of several devices a phrase means, or None when it does not match at least two.

    The phrase must be words of the question itself; the options are built only from this company's devices.
    """
    phrase = phrase.strip()
    if not phrase or phrase.lower() not in question.lower():
        return None
    matches = matching_devices(phrase, devices)
    if len(matches) < 2:
        return None
    choices: list[Alternative] = []
    for device in matches[:MAX_DEVICE_CHOICES]:
        hostname = display_hostname(device.hostname, device.device_id)
        request = _substitute(question, phrase, hostname) or _with_suffix(
            question, f"device: {hostname}"
        )
        if request is None or len(hostname) > _MAX_LABEL:
            return None
        choices.append(Alternative(label=hostname, request=request))
    every = _all_of_them(question, phrase) or _with_suffix(question, "all of them")
    if every is None:
        return None
    choices.append(Alternative(label=ALL_OF_THEM, request=every))
    count = len(matches)
    plain = len(phrase) <= _MAX_QUOTED_PHRASE and _PLAIN_PHRASE.fullmatch(phrase) is not None
    return Clarify(
        texts.device_choice_question(phrase.lower() if plain else None, count), tuple(choices)
    )


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two short strings."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def _normal_name(name: str) -> str:
    return re.sub(r"'s$|’s$", "", name.strip().lower())


def is_own_company(name: str, own_name: str) -> bool:
    return bool(own_name) and _normal_name(name) == own_name.strip().lower()


def is_company_typo(name: str, own_name: str) -> bool:
    """True when `name` is a near miss of the user's own company: one or two edits away, never exact."""
    own = own_name.strip().lower()
    wrote = _normal_name(name)
    if len(own) < _MIN_COMPANY_NAME or len(wrote) < _MIN_COMPANY_NAME or wrote == own:
        return False
    return edit_distance(wrote, own) <= _MAX_TYPO_DISTANCE


def company_typo_clarification(question: str, written: str, own_name: str) -> Clarify | None:
    """ "Did you mean <own company>?" with one choice that resends the question with the name corrected."""
    written = _normal_name(written)
    if not written or not is_company_typo(written, own_name):
        return None
    pattern = re.compile(rf"\b{re.escape(written)}(?:['’]s)?\b", re.IGNORECASE)
    if len(pattern.findall(question)) != 1:
        return None
    request = pattern.sub(lambda _: own_name, question, count=1)
    if len(request) > _MAX_REQUEST:
        return None
    return Clarify(
        texts.company_typo_question(own_name),
        (Alternative(label=texts.company_typo_label(own_name), request=request),),
    )


# A device id is 12 letters and digits, a hostname ends in a number after a hyphen (acme-macbook-2), and an
# employee id starts with "emp-". Anything shaped like one counts, whether or not it is a real identifier.
_DEVICE_ID = re.compile(r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{12}\b", re.IGNORECASE)
_HOSTNAME = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d+\b", re.IGNORECASE)
_EMPLOYEE_ID = re.compile(r"\bemp-[a-z0-9-]+", re.IGNORECASE)


def names_specific_identifier(question: str) -> bool:
    """Whether the question names one particular device id, hostname or employee id.

    Such a question about another company is refused as plain "not found", exactly like a device that does
    not exist: only a general question about another company may be told which company the session is in.
    """
    return any(p.search(question) for p in (_DEVICE_ID, _HOSTNAME, _EMPLOYEE_ID))
