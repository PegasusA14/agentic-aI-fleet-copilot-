"""The verify gate: nothing a model wrote reaches the user unless code can ground it.

Two checks, both deterministic:
  * prose (headline, explanation, clarification text): every number, device id and hostname must
    already appear in code-written facts, and the text must be plain and short;
  * evidence: every cited reading must exist for this tenant and read exactly as the data says.
Prose that fails is replaced by code-written text. Evidence that fails makes the whole answer a
`tool_error`, because an answer with unprovable evidence must not be shown.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fleet_copilot import policy
from fleet_copilot.agent.compose import Composition, Resolver, evidence_kind, evidence_text
from fleet_copilot.agent.insights import estimates_of
from fleet_copilot.contracts import AnswerEnvelope
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
# number can check, so it is rejected whatever the outcome. Matched on word boundaries over the folded
# text; flexible whitespace so "will  fail" does not slip through. A phrase equal to a code-written
# estimate (see `_without_estimates`) is exempt. Lexical backstop, not a reading of meaning.
CAUSAL_PHRASES = (
    "because", "due to", "caused by", "the reason", "reason why", "leads to", "results in",
    "is why", "explains",
)  # fmt: skip
PREDICTIVE_PHRASES = (
    "will fail", "will run out", "is going to", "likely to", "expected to", "soon",
    "within days", "within weeks", "probably", "may cause", "could cause",
)  # fmt: skip
EXPERIENCE_WORDS = (
    "slow", "slowness", "sluggish", "lag", "laggy", "crash", "crashing", "performance",
)  # fmt: skip


def _phrase_pattern(phrases: tuple[str, ...], suffix: str = "") -> re.Pattern[str]:
    body = "|".join(r"\s+".join(re.escape(w) for w in p.split()) for p in phrases)
    return re.compile(rf"\b(?:{body}){suffix}\b", re.IGNORECASE)


_CAUSAL = _phrase_pattern(CAUSAL_PHRASES)
_PREDICTIVE = _phrase_pattern(PREDICTIVE_PHRASES)
# Inflections of the experience words ("slower", "crashes", "lagging") are the same claim.
_EXPERIENCE = re.compile(
    r"\b(?:slow(?:ness|er|est|ly|ing|ed|s)?|sluggish\w*|lag(?:gy|ging|ged|s)?"
    r"|crash(?:es|ed|ing)?|performance)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _without_estimates(text: str, estimates: Sequence[str]) -> str:
    """`text` with each code-written estimate blanked out, so a duration the code computed is not a prediction."""
    for estimate in sorted((_fold(e).strip() for e in estimates), key=len, reverse=True):
        if estimate:
            text = re.sub(re.escape(estimate), " ", text, flags=re.IGNORECASE)
    return text


def claim_problems(text: str, *, estimates: Sequence[str] = ()) -> list[str]:
    """Which speculation rules `text` breaks: `causal`, `predictive`, `experience` (codes only, never the text).

    Pure and shared with the eval evaluator. `estimates` are phrases the code wrote into the facts
    (for example a battery estimate); they may appear in prose verbatim, nothing else future-facing may.
    """
    folded = _without_estimates(_fold(text), estimates)
    found: list[str] = []
    if _CAUSAL.search(folded):
        found.append("causal")
    if _PREDICTIVE.search(folded):
        found.append("predictive")
    if _EXPERIENCE.search(folded):
        found.append("experience")
    return found


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_DAY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.IGNORECASE
)
_ISO_DAY = re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b")


def _month_days(text: str) -> set[tuple[str, int]]:
    return {(m.group(1).lower(), int(m.group(2))) for m in _MONTH_DAY.finditer(_fold(text))}


def allowed_month_days(comp: Composition, estimates: Sequence[str]) -> set[tuple[str, int]]:
    """The calendar days prose may name: those in a code-written estimate, or the same day as a date the facts state."""
    allowed: set[tuple[str, int]] = set()
    for estimate in estimates:
        allowed |= _month_days(estimate)
    stated = [c.text for c in comp.claims] + list(comp.notes)
    stated += [e.label for e in comp.evidence] + [e.value for e in comp.evidence]
    for text in stated:
        for match in _ISO_DAY.finditer(text):
            month, day = int(match.group(1)), int(match.group(2))
            if 1 <= month <= 12:
                allowed.add((_MONTHS[month - 1], day))
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


@dataclass(frozen=True)
class ProseVerdict:
    """What the graph may show. `problems` are codes only: safe to put in a trace."""

    headline: str
    explanation: str | None
    problems: tuple[str, ...]


def _code(problem: str) -> str:
    return problem.split(":", 1)[0]


def shown_serials(comp: Composition) -> set[str]:
    """Every device serial the answer lists or cites. The explanation may use hostnames, never these."""
    serials = known_ids(comp)
    for item in comp.evidence:
        try:
            serials.add(EvidenceRef.parse(item.ref).device_id)
        except ValueError:
            continue
    return serials


def verify_prose(
    comp: Composition,
    as_of: datetime,
    *,
    headline: str | None,
    explanation: str | None,
    estimates: Sequence[str] | None = None,
) -> ProseVerdict:
    """Accept the model's headline and explanation only if they ground; else use code-written text.

    `estimates` (default: the composition's `estimates` list, if it has one) are code-written future-facing
    phrases such as a battery estimate; prose may repeat them verbatim and their numbers count as grounded.
    """
    if estimates is None:
        estimates = tuple(getattr(comp, "estimates", None) or ())
    allowed, ids, labels = allowed_numbers(comp, as_of), known_ids(comp), known_labels(comp)
    for estimate in estimates:
        allowed |= {_norm(m) for m in _NUMBER.findall(_fold(estimate))}
    days = allowed_month_days(comp, estimates)
    is_answer = comp.outcome == "answer"
    judges_software = is_answer and any("#software[" in item.ref for item in comp.evidence)
    required = number_tokens(comp.fallback_headline)
    problems: list[str] = []
    final_headline = comp.fallback_headline
    if headline is not None:
        found = check_prose(
            headline,
            allowed=allowed,
            ids=ids,
            labels=labels,
            max_chars=policy.MAX_HEADLINE_CHARS,
        )
        found += claim_problems(headline, estimates=estimates)
        found += date_problems(headline, days)
        if is_answer and _CLAIM_WORD.search(_fold(headline)):
            found.append("claim_word")
        if judges_software and _JUDGMENT_WORD.search(_fold(headline)):
            found.append("judgment_word")
        # The code headline states the result; the model's may reword it but must keep its numbers.
        if not required <= number_tokens(headline):
            found.append("restates_nothing")
        if found:
            problems += [f"headline:{_code(p)}" for p in found]
        else:
            final_headline = headline.strip()
    final_explanation: str | None = None
    if explanation is not None and explanation.strip():
        found = check_prose(
            explanation,
            allowed=allowed,
            ids=ids,
            labels=labels,
            max_chars=policy.MAX_EXPLANATION_CHARS,
        )
        found += claim_problems(explanation, estimates=estimates)
        found += date_problems(explanation, days)
        if sentence_count(explanation) > policy.MAX_EXPLANATION_SENTENCES:
            found.append("too_many_sentences")
        if serials_in(explanation, shown_serials(comp)):
            found.append("serial_in_prose")
        if is_answer and _CLAIM_WORD.search(_fold(explanation)):
            found.append("claim_word")
        if judges_software and _JUDGMENT_WORD.search(_fold(explanation)):
            found.append("judgment_word")
        if found:
            problems += [f"explanation:{_code(p)}" for p in found]
        else:
            final_explanation = explanation.strip()
    return ProseVerdict(final_headline, final_explanation, tuple(dict.fromkeys(problems)))


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
