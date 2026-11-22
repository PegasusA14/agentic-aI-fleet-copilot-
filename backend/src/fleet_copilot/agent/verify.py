"""The verify gate: nothing a model wrote reaches the user unless code can ground it.

Two checks, both deterministic:
  * prose (the model's reply and clarification text): every number, device id, hostname and date must
    already appear in code-written facts (this turn's, or the conversation's remembered ones), and the text
    must be plain and short. A reply is checked one sentence at a time: a sentence that fails is dropped
    and the rest is kept, as long as the first sentence survives;
  * evidence: every cited reading must exist for this tenant and read exactly as the data says.
Prose that fails is replaced by code-written text. Evidence that fails makes the whole answer a
`tool_error`, because an answer with unprovable evidence must not be shown.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fleet_copilot import policy
from fleet_copilot.agent import texts
from fleet_copilot.agent.compose import Composition, Resolver, evidence_kind, evidence_text
from fleet_copilot.agent.insights import estimates_of
from fleet_copilot.contracts import AnswerEnvelope, SuggestedAction
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import NotFound

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Device ids look like MT7PJB7N5LRE (upper-case with at least one digit and one letter).
_ID_LIKE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,16}\b")
_HOST_LIKE = re.compile(r"\b[a-z][a-z0-9]*-[a-z][a-z0-9]*-\d+\b")
# Anything that could render as markup, a link or an address: angle brackets, backticks, asterisks,
# square brackets, HTML entities, www. hosts, domain/path, and scheme: prefixes (mailto:, javascript:).
# A colon followed by a space is ordinary prose and is not matched.
_MARKUP = re.compile(
    r"[<>`*\[\]]|&#?\w+;|https?://|\bwww\.|\b[a-z0-9-]+\.[a-z]{2,}/|\b[a-z][a-z0-9+.-]*:\S",
    re.IGNORECASE,
)
# Unicode categories that hide, reorder or imitate what a reader sees: format (soft hyphen, zero-width,
# bidi controls, invisible operators), control, private use, unassigned, line/paragraph separators and
# every space but U+0020. Letters, ASCII digits, punctuation and symbols are fine.
_REJECTED_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cn", "Zl", "Zp", "Zs"})
# Characters that render as blank or join glyphs invisibly although they are letters, symbols or marks.
_BLANK_LOOKING = frozenset(
    "\u2800"  # braille pattern blank
    "\u3164\uffa0\u115f\u1160"  # hangul fillers
    "\u034f"  # combining grapheme joiner
    "\u17b4\u17b5"  # khmer inherent vowels
    "\u180b\u180c\u180d\u180e"  # mongolian free variation selectors
)
_WORDS: dict[str, float] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
    "million": 1_000_000,
    "half": 0.5,
    "dozen": 12,
    "twice": 2,
}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)

# Words that make a claim no number can check: negation ("no devices"), universals ("all", "every"),
# and reassurance ("healthy", "safe"). On an answer, code has already said what was found; a model that
# adds any of these is asserting something code did not write, so its prose is replaced. This is a
# lexical backstop for the obvious cases, not a reading of meaning.
CLAIM_WORDS = (
    "no", "none", "nothing", "nobody", "nowhere", "never",
    "all", "every", "everything", "always", "zero",
    "healthy", "fine", "safe", "secure", "compliant", "ok", "okay",
)  # fmt: skip
_CLAIM_WORD = re.compile(r"\b(" + "|".join(CLAIM_WORDS) + r")\b", re.IGNORECASE)

# The telemetry carries no approval, licence or risk label for installed software, so prose about software
# may not add one. This is checked only on answers that cite software readings, on the folded text. Every
# stem takes a `\w*` suffix so inflections are caught ("malwares", "suspiciously", "illegally"), and the
# negated forms ("not approved", "not allowed") are matched with flexible whitespace. Known limit, shared
# with the claim-word check: cross-script look-alikes (for example Cyrillic letters) are not folded.
_JUDGMENT_WORD = re.compile(
    r"\b(?:malicious\w*|malware\w*|virus\w*|trojan\w*|spyware\w*|ransomware\w*|rogue\w*"
    r"|unapprov\w*|disapprov\w*|unauthori[sz]\w*|unsanction\w*|unlicens\w*|unvetted\w*|untrust\w*"
    r"|illegal\w*|illicit\w*|pirat\w*|crack(?:ed|s)\b|suspic\w*|risk\w*|danger\w*|unsafe\w*"
    r"|insecure\w*|vulnerab\w*|harm\w*|threat\w*|ban(?:ned|s)\b"
    r"|un-?approv\w*|disallow\w*|sketchy|dodgy|unwanted"
    r"|not\s+(?:approved|authori[sz]ed|allowed|permitted)"
    r"|(?:isn|wasn|aren|weren)['\u2019]?t\s+(?:approved|authori[sz]ed|allowed|permitted))\b",
    re.IGNORECASE,
)

# The telemetry records readings, not causes, futures or how a machine feels to use. Prose that says why
# something happens, what will happen, or that a machine is slow or crashing is asserting something no
# number can check. Causal and experience wording is allowed only when the sentence carries a hedge marker
# (see `HEDGES`); future-facing wording is never allowed, hedged or not, unless it equals a code-written
# estimate (see `_without_estimates`). Matched on word boundaries over the folded text, with flexible
# whitespace so "will  fail" does not slip through. Lexical backstop, not a reading of meaning.
CAUSAL_PHRASES = (
    "because", "due to", "caused by", "the reason", "reason why", "leads to", "results in",
    "is why", "explains", "may cause", "could cause",
)  # fmt: skip
PREDICTIVE_PHRASES = (
    "will fail", "will run out", "will likely", "will probably", "will soon", "is going to",
    "are going to", "likely to", "expected to", "soon", "within days", "within weeks",
)  # fmt: skip
EXPERIENCE_WORDS = (
    "slow", "slowness", "sluggish", "lag", "laggy", "crash", "crashing", "performance",
)  # fmt: skip
# A sentence that says it is only a possibility. "may" is not a hedge when it is the month ("May 12").
HEDGES = (
    "could", "might", "may", "possibly", "perhaps", "one possible", "a possible", "worth checking",
    "it's worth", "can be a sign", "can indicate",
)  # fmt: skip


def _flexible(phrase: str) -> str:
    """A regex for `phrase`: whitespace between words is flexible and an apostrophe may be curly."""
    words = (re.escape(w).replace("'", "['\u2019]") for w in phrase.split())
    return r"\s+".join(words)


def _phrase_pattern(phrases: tuple[str, ...], suffix: str = "") -> re.Pattern[str]:
    body = "|".join(_flexible(p) for p in phrases)
    return re.compile(rf"\b(?:{body}){suffix}\b", re.IGNORECASE)


_CAUSAL = _phrase_pattern(CAUSAL_PHRASES)
_PREDICTIVE = _phrase_pattern(PREDICTIVE_PHRASES)
_HEDGE = re.compile(
    r"\b(?:" + "|".join(_flexible(h) if h != "may" else r"may(?!\s+\d)" for h in HEDGES) + r")\b",
    re.IGNORECASE,
)
# "will likely fail", "probably run out", "is likely to ...": a likelihood is a prediction only when it
# is attached to a future event. "A likely reason could be" is a hedged cause, not a prediction.
_FUTURE_EVENT = r"(?:fail|run\s+out|crash|break|die|fill|stop|reach|hit|drop|degrade|worsen)"
_FUTURE_LIKELY = re.compile(
    rf"\b(?:will|won['\u2019]t)\s+(?:(?:likely|probably|soon|eventually|also)\s+)*{_FUTURE_EVENT}\b"
    rf"|\b(?:probably|likely)\s+{_FUTURE_EVENT}\b",
    re.IGNORECASE,
)
# Inflections of the experience words ("slower", "crashes", "lagging") are the same claim.
_EXPERIENCE = re.compile(
    r"\b(?:slow(?:ness|er|est|ly|ing|ed|s)?|sluggish\w*|lag(?:gy|ging|ged|s)?"
    r"|crash(?:es|ed|ing)?|performance)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])(\s+)")


def _without_estimates(text: str, estimates: Sequence[str]) -> str:
    """`text` with each code-written estimate blanked out, so a duration the code computed is not a prediction."""
    for estimate in sorted((_fold(e).strip() for e in estimates), key=len, reverse=True):
        if estimate:
            text = re.sub(re.escape(estimate), " ", text, flags=re.IGNORECASE)
    return text


def claim_problems(text: str, *, estimates: Sequence[str] = ()) -> list[str]:
    """Which speculation rules `text` breaks, hedged or not: `causal`, `predictive`, `experience`.

    Strict and pure: it is for text code wrote (headlines, refusals), where no speculation belongs at all.
    A model's reply is judged by `confident_claims`, which lets a hedged sentence use causal or experience
    wording. `estimates` are phrases the code wrote into the facts (for example a battery estimate); they
    may appear verbatim, nothing else future-facing may.
    """
    folded = _without_estimates(_fold(text), estimates)
    found: list[str] = []
    if _CAUSAL.search(folded):
        found.append("causal")
    if is_predictive(text, estimates=estimates):
        found.append("predictive")
    if _EXPERIENCE.search(folded):
        found.append("experience")
    return found


def has_hedge(sentence: str) -> bool:
    """True if `sentence` carries a hedge marker (`HEDGES`): it says "could", "might", "worth checking" and so on."""
    return _HEDGE.search(_fold(sentence)) is not None


def is_predictive(sentence: str, *, estimates: Sequence[str] = ()) -> bool:
    """True if `sentence` says something will happen: a future phrase, a likelihood attached to a future event,
    or a dated prediction. A hedge does not excuse it; only a phrase equal to a code estimate is exempt."""
    folded = _without_estimates(_fold(sentence), estimates)
    return bool(
        _PREDICTIVE.search(folded)
        or _FUTURE_LIKELY.search(folded)
        or _DATED_PREDICTION.search(folded)
    )


def unhedged_claims(sentence: str, *, estimates: Sequence[str] = ()) -> list[str]:
    """`causal` and/or `experience` when `sentence` uses that wording without a hedge marker; none when hedged."""
    if has_hedge(sentence):
        return []
    folded = _without_estimates(_fold(sentence), estimates)
    found: list[str] = []
    if _CAUSAL.search(folded):
        found.append("causal")
    if _EXPERIENCE.search(folded):
        found.append("experience")
    return found


def confident_claims(text: str, *, estimates: Sequence[str] = ()) -> list[str]:
    """The speculation a reader would take as stated fact in `text`: sentence by sentence, an unhedged causal
    or experience sentence is `causal` / `experience`, and any predictive sentence is `predictive`. Pure and
    shared with the eval evaluator."""
    found: list[str] = []
    for sentence in split_sentences(text):
        found += unhedged_claims(sentence, estimates=estimates)
        if is_predictive(sentence, estimates=estimates):
            found.append("predictive")
    return list(dict.fromkeys(found))


def split_sentences(text: str) -> list[str]:
    """The sentences of `text` (cut after . ! ? and whitespace), without the whitespace between them."""
    return [part.strip() for part in _SENTENCE_END.split(text.strip()) if part.strip()]


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_NAME = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTH_DAY = re.compile(rf"\b({_MONTH_NAME})\.?\s+(\d{{1,2}})\b", re.IGNORECASE)
# A date given as the time of a prediction: "will ... Jun 20", "by Jun 20", "expected Jun 20".
_DATED_PREDICTION = re.compile(
    rf"\b(?:will|by|before|until|expected)\b(?:\s+[\w'\u2019-]+){{0,4}}?\s+{_MONTH_NAME}\.?\s+\d{{1,2}}\b",
    re.IGNORECASE,
)
_ISO_DAY = re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b")


def _month_days(text: str) -> set[tuple[str, int]]:
    return {(m.group(1).lower()[:3], int(m.group(2))) for m in _MONTH_DAY.finditer(_fold(text))}


def stated_month_days(text: str) -> set[tuple[str, int]]:
    """The calendar days `text` states, as ISO dates ("2026-06-12") or month and day ("Jun 12")."""
    days = _month_days(text)
    for match in _ISO_DAY.finditer(text):
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            days.add((_MONTHS[month - 1], day))
    return days


def allowed_month_days(comp: Composition, estimates: Sequence[str]) -> set[tuple[str, int]]:
    """The calendar days prose may name: those in a code-written estimate, or the same day as a date the facts state."""
    allowed: set[tuple[str, int]] = set()
    for estimate in estimates:
        allowed |= _month_days(estimate)
    stated = [c.text for c in comp.claims] + list(comp.notes)
    stated += [e.label for e in comp.evidence] + [e.value for e in comp.evidence]
    for text in stated:
        allowed |= stated_month_days(text)
    return allowed


def date_problems(text: str, allowed: set[tuple[str, int]]) -> list[str]:
    """`date` when prose names a month and day that no code-written estimate or stated date contains."""
    return ["date"] if _month_days(text) - allowed else []


def sentence_count(text: str) -> int:
    return len([part for part in _SENTENCE_END.split(text.strip()) if part.strip()])


def serials_in(text: str, serials: Collection[str]) -> bool:
    """True if any device serial appears in `text` as a whole token (case-insensitive)."""
    folded = _fold(text)
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])", folded, re.IGNORECASE)
        for s in serials
        if s
    )


_MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me"})


def _next_to_ascii_digit(text: str, index: int) -> bool:
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return ("0" <= before <= "9" and before != "") or ("0" <= after <= "9" and after != "")


def has_markup(text: str) -> bool:
    """True if `text` could render as markup, a link or an address."""
    return _MARKUP.search(text) is not None


def bad_chars(text: str) -> bool:
    """True if `text` has a character outside what a plain sentence needs.

    Allowed: letters, ASCII digits, punctuation, symbols, U+0020 and a newline. Rejected rather than
    removed, so the string a reader sees is exactly the string that was checked.
    """
    for index, char in enumerate(text):
        if char in " \n":
            continue
        if (
            char in _BLANK_LOOKING
            or "\ufe00" <= char <= "\ufe0f"
            or "\U000e0100" <= char <= "\U000e01ef"
        ):
            return True
        category = unicodedata.category(char)
        if category in _REJECTED_CATEGORIES:
            return True
        if category == "Nd" and not "0" <= char <= "9":
            return True
        if category in _MARK_CATEGORIES and _next_to_ascii_digit(text, index):
            return (
                True  # a joiner or mark between digits would split a number the reader sees whole
            )
    return False


def _fold(text: str) -> str:
    """The text as the scans read it: look-alike characters (circled, fullwidth) folded to plain ones.

    Only ever used for checking, never for what is shown.
    """
    return unicodedata.normalize("NFKC", text)


def _norm(token: str) -> str:
    """Canonical form so 2, 2.0, 2.00 and 1,157 compare equal to their plain spellings."""
    cleaned = token.replace(",", "")
    try:
        return format(Decimal(cleaned).normalize(), "f")
    except InvalidOperation:
        return cleaned


def number_tokens(text: str) -> set[str]:
    """Every number in `text`, digits and number-words alike, in canonical form."""
    text = _fold(text)
    found = {_norm(m) for m in _NUMBER.findall(text)}
    found |= {_norm(str(_WORDS[w.lower()])) for w in _WORD.findall(text)}
    return found


def allowed_numbers(comp: Composition, as_of: datetime) -> set[str]:
    """Numbers prose may use: those in code-written text, list positions, the as-of date and policy."""
    texts = [c.text for c in comp.claims] + list(comp.notes) + list(comp.rules)
    texts += [comp.fallback_headline]
    texts += [e.label for e in comp.evidence] + [e.value for e in comp.evidence]
    texts += [entity_list.title for entity_list in comp.entity_lists]
    texts += [a.label for a in comp.suggested_actions]
    allowed: set[str] = set()
    for text in texts:
        allowed |= {_norm(m) for m in _NUMBER.findall(text)}
    if comp.coverage is not None:
        allowed.add(str(comp.coverage.population))
    for entity_list in comp.entity_lists:
        allowed |= {str(i) for i in range(1, len(entity_list.items) + 1)}
    allowed |= {str(as_of.year), str(as_of.month), str(as_of.day), str(as_of.hour)}
    allowed |= {str(as_of.minute)}
    allowed |= {_norm(str(v)) for v in policy.disclosure().values()}
    allowed |= {_norm(str(v)) for v in (policy.MAX_WINDOW_DAYS, policy.MAX_REFS_PER_FINDING)}
    return allowed


def composition_of(envelope: AnswerEnvelope) -> Composition:
    """The composition an envelope was built from, so the graph can verify it without keeping objects in state."""
    return Composition(
        outcome=envelope.outcome,
        reason_code=envelope.reason_code,
        claims=list(envelope.claims),
        evidence=list(envelope.evidence),
        coverage=envelope.coverage,
        entity_lists=list(envelope.entity_lists),
        notes=list(envelope.notes),
        fallback_headline=envelope.headline,
        rules=[envelope.scope.rule] if envelope.scope.rule else [],
        suggested_actions=list(envelope.suggested_actions),
        proposal=envelope.proposal,
        insights=list(envelope.insights),
        estimates=estimates_of(envelope.insights),
    )


def known_labels(comp: Composition) -> set[str]:
    return {item.label for entity_list in comp.entity_lists for item in entity_list.items}


def known_ids(comp: Composition) -> set[str]:
    ids = {d for c in comp.claims for d in c.device_ids}
    ids |= {i.device_id for entity_list in comp.entity_lists for i in entity_list.items}
    if comp.coverage is not None:
        ids |= set(comp.coverage.stale) | set(comp.coverage.missing_latest)
        ids |= {d for devices in comp.coverage.excluded.values() for d in devices}
    return ids


def check_prose(
    text: str, *, allowed: set[str], ids: set[str], labels: set[str], max_chars: int
) -> list[str]:
    """Problems with `text`, as codes (`number:<n>` names the offending number)."""
    text = text.strip()
    if not text:
        return ["empty"]
    problems: list[str] = []
    if len(text) > max_chars:
        problems.append("too_long")
    if bad_chars(text):
        problems.append("bad_chars")
    text = _fold(text)
    if _MARKUP.search(text):
        problems.append("markup")
    if any(found not in ids for found in _ID_LIKE.findall(text)):
        problems.append("unknown_id")
    if any(found not in labels for found in _HOST_LIKE.findall(text)):
        problems.append("unknown_host")
    # Digits inside a device id or hostname are judged by the id and host checks, not as numbers.
    bare = _HOST_LIKE.sub(" ", _ID_LIKE.sub(" ", text))
    problems += [f"number:{token}" for token in sorted(number_tokens(bare) - allowed)]
    return problems


# Why a sentence of a reply was dropped, by rule code. Fact rules mean the sentence stated something no code
# fact backs (or showed something it must not); hedge rules mean it spoke with more confidence than a reading
# allows; length rules mean it was too long to show. Only the fact rules say the model invented a fact.
FACT_RULES = frozenset({"number", "unknown_id", "unknown_host", "date", "serial_in_prose"})
HEDGE_RULES = frozenset({"unhedged_causal", "unhedged_experience", "predictive"})
LENGTH_RULES = frozenset({"too_long"})
# Action rules mean the sentence offered or judged an action code did not back: an action no suggestion of
# this turn carries (`unbacked_action`), or a verdict on how well an action would work (`effectiveness`).
# They are action-quality errors, so they are neither hedge nor length repairs.
ACTION_RULES = frozenset({"unbacked_action", "effectiveness"})
REPLY_TRIMMED = "reply:trimmed"
REPLY_FALLBACK = "reply:fallback"
_PROBLEM = re.compile(r"^reply:([a-z_]+)@(\d+)$")
_DROPPED = re.compile(r"^reply:dropped=(\d+)$")


def parse_reply_problems(problems: Sequence[str]) -> list[tuple[str, int]]:
    """The `(rule, sentence index)` pairs among `problems` (`reply:<rule>@<index>`); other entries are skipped."""
    found: list[tuple[str, int]] = []
    for problem in problems:
        match = _PROBLEM.match(str(problem))
        if match:
            found.append((match.group(1), int(match.group(2))))
    return found


def dropped_count(problems: Sequence[str]) -> int:
    """How many sentences the trace says were dropped (`reply:dropped=<n>`); zero when none."""
    for problem in problems:
        match = _DROPPED.match(str(problem))
        if match:
            return int(match.group(1))
    return 0


@dataclass(frozen=True)
class ProseVerdict:
    """What the graph may show. `problems` are codes only: safe to put in a trace.

    `explanation` is the checked reply: the sentences that passed, or None when the lead did not stand.
    `problems` read `reply:<rule>@<sentence index>` for each failed rule, then `reply:dropped=<n>`, then
    `reply:fallback` when there is no reply and `reply:trimmed` when the caps cut it. Never sentence text.
    """

    headline: str
    explanation: str | None
    problems: tuple[str, ...]


def _code(problem: str) -> str:
    return problem.split(":", 1)[0]


def shown_serials(comp: Composition) -> set[str]:
    """Every device serial the answer lists or cites. The reply may use hostnames, never these."""
    serials = known_ids(comp)
    for item in comp.evidence:
        try:
            serials.add(EvidenceRef.parse(item.ref).device_id)
        except ValueError:
            continue
    return serials


@dataclass(frozen=True)
class Grounding:
    """Everything a sentence of a reply may rest on, and what it must not name."""

    numbers: frozenset[str]
    ids: frozenset[str]
    labels: frozenset[str]
    days: frozenset[tuple[str, int]]
    serials: frozenset[str]
    estimates: tuple[str, ...] = ()
    claim_words: re.Pattern[str] | None = None
    judges_software: bool = False
    # The `(action_type, hostname)` pairs this turn's facts back (suggestions, the proposal card, and what
    # the conversation already holds). None switches the action check off (a refusal's reply).
    actions: frozenset[tuple[str, str]] | None = None
    # A chat turn may describe what the four actions are; it may not tie one to a device without backing.
    describes_capabilities: bool = False


# On a chat turn "no" and "none" are ordinary answers ("no, nothing was approved"); the words that vouch for
# the whole fleet stay banned.
_CHAT_CLAIM_WORD = re.compile(
    r"\b(all|every|everything|always|healthy|fine|safe|secure|compliant)\b", re.IGNORECASE
)


def _memory_set(memory: Mapping[str, Collection[str]] | None, key: str) -> frozenset[str]:
    return frozenset(memory.get(key, ())) if memory else frozenset()


def _memory_days(memory: Mapping[str, Collection[str]] | None) -> set[tuple[str, int]]:
    """The remembered calendar days, stored by `memory_facts` as "jun 12"."""
    days: set[tuple[str, int]] = set()
    for entry in _memory_set(memory, "dates"):
        month, _, day = entry.partition(" ")
        if day.isdigit():
            days.add((month, int(day)))
    return days


def grounding_for(
    comp: Composition,
    as_of: datetime,
    *,
    estimates: Sequence[str] | None = None,
    memory: Mapping[str, Collection[str]] | None = None,
    suggestions: Sequence[SuggestedAction] = (),
) -> Grounding:
    """The facts a reply to `comp` may use: this turn's facts plus the conversation's remembered ones.

    Remembered facts (`memory_facts`) are numbers, hostnames, device ids and dates from earlier turns; the
    user's notes are never among them. A chat turn has no facts of its own: it may use only what is
    remembered, the numbers in the capability card, and the disclosed policy numbers.

    The actions a reply may offer are this turn's suggestions (`comp.suggested_actions`, or `suggestions` for
    the next steps of a list answer, which join the envelope only after the reply is checked), its proposal
    card, and the actions the conversation already holds (`memory["actions"]`). A refusal's reply is not
    checked for actions: it speaks only of the alternatives the refusal itself lists.
    """
    remembered_numbers = {_norm(n) for n in _memory_set(memory, "numbers")}
    remembered_ids = _memory_set(memory, "ids")
    remembered_labels = _memory_set(memory, "labels")
    disclosed = {_norm(str(v)) for v in policy.disclosure().values()}
    actions: frozenset[tuple[str, str]] | None = None
    if comp.outcome in ("answer", "proposal", "chat"):
        offered = action_pairs(
            comp.suggested_actions or list(suggestions),
            (comp.proposal.action_type, comp.proposal.target_label) if comp.proposal else None,
        )
        held = {
            (kind, host)
            for entry in _memory_set(memory, "actions")
            for kind, _, host in [entry.partition("|")]
        }
        actions = offered | held
    if comp.outcome == "chat":
        return Grounding(
            numbers=frozenset(
                remembered_numbers | number_tokens(texts.CAPABILITY_CARD) | disclosed
            ),
            ids=remembered_ids,
            labels=remembered_labels,
            days=frozenset(_memory_days(memory)),
            serials=remembered_ids,
            claim_words=_CHAT_CLAIM_WORD,
            actions=actions,
            describes_capabilities=True,
        )
    if estimates is None:
        estimates = tuple(getattr(comp, "estimates", None) or ())
    numbers = allowed_numbers(comp, as_of) | remembered_numbers
    for estimate in estimates:
        numbers |= {_norm(m) for m in _NUMBER.findall(_fold(estimate))}
    is_answer = comp.outcome == "answer"
    return Grounding(
        numbers=frozenset(numbers),
        ids=frozenset(known_ids(comp) | remembered_ids),
        labels=frozenset(known_labels(comp) | remembered_labels),
        days=frozenset(allowed_month_days(comp, estimates) | _memory_days(memory)),
        serials=frozenset(shown_serials(comp) | remembered_ids),
        estimates=tuple(estimates),
        claim_words=_CLAIM_WORD if is_answer else None,
        judges_software=is_answer and any("#software[" in item.ref for item in comp.evidence),
        actions=actions,
    )


def sentence_problems(sentence: str, grounding: Grounding) -> list[str]:
    """The rule codes `sentence` breaks, in a fixed order; empty when it may be shown.

    Facts: `number`, `unknown_id`, `unknown_host`, `date`, `serial_in_prose`. Plain text: `markup`,
    `bad_chars`. Claims: `claim_word` (universal reassurance), `judgment_word` (a label for software the
    data does not carry). Hedging: `unhedged_causal`, `unhedged_experience`, `predictive`. Actions:
    `effectiveness` (a verdict on how well an action works), `unbacked_action` (an action this turn's facts do
    not back). Length: `too_long`. Pure; the codes carry no text.
    """
    found = [
        _code(p)
        for p in check_prose(
            sentence,
            allowed=set(grounding.numbers),
            ids=set(grounding.ids),
            labels=set(grounding.labels),
            max_chars=policy.MAX_REPLY_CHARS,
        )
    ]
    found += date_problems(sentence, set(grounding.days))
    if serials_in(sentence, grounding.serials):
        found.append("serial_in_prose")
    folded = _fold(sentence)
    if grounding.claim_words is not None and grounding.claim_words.search(folded):
        found.append("claim_word")
    if grounding.judges_software and _JUDGMENT_WORD.search(folded):
        found.append("judgment_word")
    found += [f"unhedged_{c}" for c in unhedged_claims(sentence, estimates=grounding.estimates)]
    if is_predictive(sentence, estimates=grounding.estimates):
        found.append("predictive")
    if judges_effectiveness(sentence):
        found.append("effectiveness")
    if grounding.actions is not None and unbacked_actions(
        sentence, grounding.actions, capabilities=grounding.describes_capabilities
    ):
        found.append("unbacked_action")
    return list(dict.fromkeys(found))


# --- actions the reply may offer ---------------------------------------------------------------------------------
# A reply may only offer or mention an action that code put in this turn's facts: a suggestion, the proposal
# card, or one the conversation already holds. The words below find a sentence that talks about doing
# something; a sentence that names an action must match a backed (action type, hostname) pair. Lexical
# backstop, not a reading of meaning: it errs towards dropping a sentence, never towards showing an offer.
_ACTION_MENTIONS: tuple[tuple[str | None, re.Pattern[str]], ...] = (
    (
        "employee_notification",
        re.compile(
            r"\bnotif\w*|\bnudg\w*"
            r"|\b(?:ask|tell|remind|message|email|alert|contact|ping)\w*\s+"
            r"(?:(?:the|those|these|all|their|each|your|our)\s+)?"
            r"(?:users?|owners?|employees?|people|staff|assignees?|them|him|her)\b"
            r"|\b(?:message|email|reminder)s?\s+to\b",
            re.IGNORECASE,
        ),
    ),
    ("remediation_ticket", re.compile(r"\btickets?\b|\bremediation\b", re.IGNORECASE)),
    (
        "upgrade_order",
        re.compile(
            r"\bupgrad\w*|\border(?:s|ed|ing)?\s+(?:a|an|the|more|new|extra|some)\b", re.IGNORECASE
        ),
    ),
    (
        "replacement_flag",
        re.compile(
            r"\breplac\w*|\bflag(?:s|ged|ging)?\s+"
            r"(?:it|them|that|this|the|a|an|[a-z][a-z0-9]*-[a-z][a-z0-9]*-\d+)\b",
            re.IGNORECASE,
        ),
    ),
    (None, re.compile(r"\bpropos(?:e|es|ed|ing|al|als)\b", re.IGNORECASE)),
)
# An operating-system upgrade is a compliance matter, not the hardware upgrade order.
_OS_UPGRADE = re.compile(
    r"\b(?:mac\s*os|os|operating\s+system|windows)\s*(?:x\s*)?[\d.]*\s*(?:update|upgrade)s?\b"
    r"|\bupgrad\w*\s+to\s+(?:mac\s*os|windows|os)\b",
    re.IGNORECASE,
)


def action_pairs(
    suggestions: Sequence[SuggestedAction], proposal_action: tuple[str, str] | None = None
) -> frozenset[tuple[str, str]]:
    """The `(action_type, lower-case hostname)` pairs a turn's suggestions (and proposal card) back."""
    pairs = {(s.action_type, s.target_label.lower()) for s in suggestions[: policy.MAX_SUGGESTIONS]}
    if proposal_action is not None:
        pairs.add((proposal_action[0], proposal_action[1].lower()))
    return frozenset(pairs)


def named_actions(sentence: str) -> list[tuple[int, str | None, set[str]]]:
    """The actions `sentence` talks about: `(position, action type or None, hostnames it is tied to)`.

    An action type is one of the four action types, or None for a bare "propose". Each hostname in the
    sentence is tied to the nearest action word (the one before it on a tie), so "an upgrade for host-a or a
    replacement for host-b" ties each host to its own action.
    """
    folded = _OS_UPGRADE.sub(" ", _fold(sentence))
    mentions = sorted(
        (match.start(), kind)
        for kind, pattern in _ACTION_MENTIONS
        for match in pattern.finditer(folded)
    )
    tied: list[tuple[int, str | None, set[str]]] = [(pos, kind, set()) for pos, kind in mentions]
    if tied:
        for host in _HOST_LIKE.finditer(folded):
            nearest = min(tied, key=lambda m: (abs(m[0] - host.start()), m[0] > host.start()))
            nearest[2].add(host.group(0).lower())
    return tied


def unbacked_actions(
    sentence: str, backed: Collection[tuple[str, str]], *, capabilities: bool = False
) -> bool:
    """True when `sentence` offers or mentions an action that no `(action type, hostname)` in `backed` covers.

    Pure. Every action the sentence names must be backed: with hostnames, each (type, hostname) pair must be
    in `backed`; with none, at least one backed pair must have that type; a bare "propose" needs any backed
    action. A sentence that names no action is never unbacked. With `capabilities` (a chat turn) an action
    named with no hostname describes what the assistant can do ("I can propose four actions") and is allowed;
    tying an action to a device still needs a backing suggestion, proposal or remembered turn.
    """
    for _, kind, hosts in named_actions(sentence):
        if capabilities and not hosts:
            continue
        if kind is None:
            ok = bool(backed) and (not hosts or any(h in {b[1] for b in backed} for h in hosts))
        elif hosts:
            ok = all((kind, host) in backed for host in hosts)
        else:
            ok = any(kind == action for action, _ in backed)
        if not ok:
            return True
    return False


# Phrases that judge how well an action would work. The data shows readings, never what an action would do.
EFFECTIVENESS_PHRASES = (
    "only help a little", "only help a bit", "help only a little", "won't help", "will not help",
    "wouldn't help", "would not help", "won't fix", "will not fix", "wouldn't fix", "would not fix",
    "won't solve", "will not solve", "will fix", "will solve", "would solve", "would fix",
    "will resolve", "would resolve", "not enough to",
)  # fmt: skip
_EFFECTIVENESS = _phrase_pattern(EFFECTIVENESS_PHRASES)


def judges_effectiveness(sentence: str) -> bool:
    """True when `sentence` says how well or how little an action would work ("may only help a little",
    "will fix it", "not enough to"). Pure; hedging does not excuse it."""
    return _EFFECTIVENESS.search(_fold(sentence)) is not None


def _pieces(text: str) -> list[tuple[str, str]]:
    """`text` as `(whitespace before, sentence)` pairs, so kept sentences keep their paragraph breaks."""
    parts = _SENTENCE_SPLIT.split(text.strip())
    pieces = [("", parts[0])]
    pieces += [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return [(gap, sentence.strip()) for gap, sentence in pieces if sentence.strip()]


def _joined(pieces: Sequence[tuple[str, str]]) -> str:
    return "".join(
        (gap if index else "") + sentence for index, (gap, sentence) in enumerate(pieces)
    )


def trimmed_to_reply_caps(pieces: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """The leading whole sentences that fit `policy.MAX_REPLY_SENTENCES` and `policy.MAX_REPLY_CHARS`."""
    kept: list[tuple[str, str]] = []
    for piece in pieces:
        if (
            len(kept) >= policy.MAX_REPLY_SENTENCES
            or len(_joined([*kept, piece])) > policy.MAX_REPLY_CHARS
        ):
            break
        kept.append(piece)
    return kept


def check_reply(text: str | None, grounding: Grounding) -> tuple[str | None, tuple[str, ...]]:
    """The reply as it may be shown, and the trace problems, after checking it sentence by sentence.

    A sentence that breaks any rule is dropped and the others are kept. The first sentence is the lead and
    must stand: if it fails, or nothing is left, there is no reply (None) and the code headline and summary
    stand alone. The kept sentences are then cut to the reply caps at a sentence boundary. Problems are
    `reply:<rule>@<index>` (the sentence's place in the model's reply), `reply:dropped=<n>`,
    `reply:fallback` and `reply:trimmed`, never the text.
    """
    if text is None or not text.strip():
        return None, ()
    pieces = _pieces(text)
    problems: list[str] = []
    failed: set[int] = set()
    for index, (_, sentence) in enumerate(pieces):
        for rule in sentence_problems(sentence, grounding):
            problems.append(f"reply:{rule}@{index}")
            failed.add(index)
    if failed:
        problems.append(f"reply:dropped={len(failed)}")
    kept = [piece for index, piece in enumerate(pieces) if index not in failed]
    if not kept or 0 in failed:
        problems.append(REPLY_FALLBACK)
        return None, tuple(problems)
    cut = trimmed_to_reply_caps(kept)
    if len(cut) < len(kept):
        problems.append(REPLY_TRIMMED)
    return _joined(cut), tuple(problems)


def verify_prose(
    comp: Composition,
    as_of: datetime,
    *,
    explanation: str | None,
    estimates: Sequence[str] | None = None,
    memory: Mapping[str, Collection[str]] | None = None,
    suggestions: Sequence[SuggestedAction] = (),
) -> ProseVerdict:
    """Accept the model's reply sentence by sentence; the headline is always the code-written one.

    The grounding is this turn's facts plus `memory` (the conversation's remembered facts, see
    `memory_facts`); `estimates` (default: the composition's) are code-written future-facing phrases such as
    a battery estimate, which a sentence may repeat verbatim. See `check_reply` for how failures are repaired.
    """
    shown, problems = check_reply(
        explanation,
        grounding_for(comp, as_of, estimates=estimates, memory=memory, suggestions=suggestions),
    )
    return ProseVerdict(comp.fallback_headline, shown, problems)


MAX_CLARIFICATION_CHARS = 120
MAX_CLARIFICATION_OPTIONS = 6
MAX_OPTION_CHARS = 80


def verify_clarification(question: str, options: list[str]) -> tuple[str, list[str]] | None:
    """A model-written clarification is shown only if it is a short plain question with short options.

    It must end in "?" (an instruction such as "enter your password" is not a question), carry no
    numbers or ids, and fit the caps; anything over a cap is rejected, never cut. None means the caller
    shows the fixed default question instead.
    """
    question = question.strip()
    if not question.endswith("?") or len(options) > MAX_CLARIFICATION_OPTIONS:
        return None
    limits = [(question, MAX_CLARIFICATION_CHARS)] + [(o, MAX_OPTION_CHARS) for o in options]
    for text, limit in limits:
        if check_prose(text, allowed=set(), ids=set(), labels=set(), max_chars=limit):
            return None
    return question, [option.strip() for option in options]


def verify_evidence(envelope: AnswerEnvelope, resolve: Resolver) -> list[str]:
    """Problems with the evidence of an assembled answer. An empty list means every reading checks out."""
    problems: set[str] = set()
    shown = {item.ref for item in envelope.evidence}
    if any(ref not in shown for claim in envelope.claims for ref in claim.refs):
        problems.add("claim_without_evidence")
    for item in envelope.evidence:
        try:
            ref = EvidenceRef.parse(item.ref)
        except ValueError:
            problems.add("malformed_ref")
            continue
        if ref.company_id != envelope.scope.company_id:
            problems.add("foreign_ref")
            continue
        try:
            reading = resolve(ref)
        except NotFound:
            problems.add("unresolvable_ref")
            continue
        if evidence_text(evidence_kind(ref.path), reading.value) != item.value:
            problems.add("evidence_mismatch")
    return sorted(problems)
