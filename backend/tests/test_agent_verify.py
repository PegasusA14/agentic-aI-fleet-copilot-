import dataclasses
import re
from datetime import UTC, datetime

import pytest
from agent_support import ACME, repo_for, run

from fleet_copilot import policy
from fleet_copilot.agent import verify
from fleet_copilot.agent.compose import Composition, compose
from fleet_copilot.agent.verify import (
    allowed_numbers,
    check_prose,
    claim_problems,
    composition_of,
    dropped_count,
    known_ids,
    known_labels,
    number_tokens,
    parse_reply_problems,
    sentence_count,
    verify_clarification,
    verify_evidence,
    verify_prose,
)
from fleet_copilot.contracts import EvidenceItem
from fleet_copilot.repository import dataset_as_of

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)


def test_numbers_are_compared_by_value_not_spelling():
    assert number_tokens("2, 2.0 and 2.00") == {"2"}
    assert number_tokens("1,157 cycles") == {"1157"}
    assert number_tokens("about fifty, then Two hundred") == {"50", "2", "100"}
    assert number_tokens("no digits here") == set()


def test_ids_and_hostnames_are_not_read_as_numbers():
    allowed: set[str] = set()
    problems = check_prose(
        "MT7PJB7N5LRE and acme-macbook-4 are low",
        allowed=allowed,
        ids={"MT7PJB7N5LRE"},
        labels={"acme-macbook-4"},
        max_chars=100,
    )
    assert problems == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ["empty"]),
        ("   ", ["empty"]),
        ("x" * 101, ["too_long"]),
        ("see <b>this</b>", ["markup"]),
        ("go to https://evil.example/x", ["markup"]),
        ("[click](http://x)", ["markup"]),
        ("`code`", ["markup"]),
        ("see www.evil.example", ["markup"]),
        ("go to evil.example/login", ["markup"]),
        ("write to mailto:a@b.c", ["markup"]),
        ("run javascript:void", ["markup"]),
        ("open data:text", ["markup"]),
        ("this is **bold**", ["markup"]),
        ("&lt;b&gt; is escaped", ["markup"]),
        ("see [the note]", ["markup"]),
        ("ZZZ9QQQ88XYZ is low", ["unknown_id"]),
        ("acme-macbook-9 is low", ["unknown_host"]),
        ("37 devices", ["number:37"]),
        ("fifty devices", ["number:50"]),
    ],
)
def test_check_prose_reports_each_problem_class(text, expected):
    assert check_prose(text, allowed={"2"}, ids=set(), labels=set(), max_chars=100) == expected


def test_check_prose_reports_unicode_tricks_as_the_numbers_they_spell():
    def check(text: str, allowed: set[str]) -> list[str]:
        return check_prose(text, allowed=allowed, ids=set(), labels=set(), max_chars=100)

    # Look-alike digits (circled, superscript) are still read by their value for the number scan.
    assert check("\u2460 device", {"2"}) == ["number:1"]  # circled digit one
    assert check("\u00b2 devices", {"1"}) == ["number:2"]  # superscript two
    assert check("twice as many", set()) == ["number:2"]
    assert check("twice as many", {"2"}) == []
    assert check("a dozen devices", {"2"}) == ["number:12"]
    assert check("half of them", {"2"}) == ["number:0.5"]


def test_invisible_characters_are_rejected_not_stripped():
    # Previously zero-width characters were silently removed and the rest was judged. Now the text is
    # rejected as-is, so what the user sees is exactly what was checked.
    def check(text: str, allowed: set[str]) -> list[str]:
        return check_prose(text, allowed=allowed, ids=set(), labels=set(), max_chars=100)

    assert check("fi\u200bfty devices", {"2"}) == ["bad_chars"]  # zero-width split
    assert check("fi\u2060fty\ufeff devices", {"2"}) == ["bad_chars"]
    # Non-ASCII digits are never allowed, even when their value is an allowed number.
    assert check("\uff12 devices", {"2"}) == ["bad_chars"]
    assert check("\uff13\uff17 devices", {"2"}) == ["bad_chars", "number:37"]


# Characters that hide, reorder or imitate what a reader sees.
HIDING_CHARS = [
    pytest.param("\u00ad", id="soft-hyphen"),
    pytest.param("\u2062", id="invisible-times"),
    pytest.param("\u2066", id="bidi-isolate"),
    pytest.param("\u2800", id="braille-blank"),
    pytest.param("\u3164", id="hangul-filler"),
    pytest.param("\u202e", id="bidi-override"),
    pytest.param("\u200e", id="left-to-right-mark"),
    pytest.param("\u200b", id="zero-width-space"),
    pytest.param("\ufeff", id="byte-order-mark"),
    pytest.param("\uff12", id="fullwidth-digit"),
    pytest.param("\u00a0", id="no-break-space"),
    pytest.param("\u2028", id="line-separator"),
    pytest.param("\u0009", id="tab"),
    pytest.param("\ue000", id="private-use"),
    pytest.param("\u0378", id="unassigned"),
    pytest.param("\u034f", id="combining-grapheme-joiner"),
    pytest.param("\ufe0f", id="variation-selector"),
    pytest.param("\u0663", id="arabic-indic-digit"),
]


@pytest.mark.parametrize("char", HIDING_CHARS)
def test_check_prose_rejects_every_hiding_character_with_a_class_code(char):
    problems = check_prose(
        f"1{char}0 of 1{char}0 devices are low",
        allowed={"1", "0", "10"},
        ids=set(),
        labels=set(),
        max_chars=100,
    )
    assert "bad_chars" in problems
    assert all(char not in p for p in problems)


@pytest.mark.parametrize("char", HIDING_CHARS)
def test_an_explanation_with_a_hiding_character_is_dropped_and_the_headline_stays_code_written(
    low_disk, char
):
    comp, as_of = low_disk
    text = f"1{char}0 of 1{char}0 devices are low on disk"
    verdict = verify_prose(comp, as_of, explanation=text)
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert "reply:bad_chars@0" in verdict.problems
    assert all(char not in p for p in verdict.problems)


@pytest.mark.parametrize(
    "joined",
    ["1\u2d7f5", "1\u17d25", "1\u03015", "1\u0301", "5\u20dd", "\u09bc1", "1\u093e"],
    ids=[
        "tifinagh-joiner",
        "khmer-coeng",
        "acute-between",
        "acute-after",
        "enclosing",
        "before",
        "spacing",
    ],
)
def test_marks_next_to_a_digit_are_rejected(joined):
    problems = check_prose(
        f"{joined} devices", allowed={"1", "5", "15"}, ids=set(), labels=set(), max_chars=100
    )
    assert "bad_chars" in problems


def test_accents_on_letters_are_still_fine():
    for text in ("caf\u00e9", "cafe\u0301", "na\u00efve", "nai\u0308ve", "Z\u00fcrich 2 devices"):
        assert check_prose(text, allowed={"2"}, ids=set(), labels=set(), max_chars=100) == [], text


def test_ordinary_typography_is_not_a_bad_character(low_disk):
    comp, as_of = low_disk

    def check(text: str) -> list[str]:
        return check_prose(text, allowed={"2", "10"}, ids=set(), labels=set(), max_chars=200)

    assert check("It\u2019s \u201cfine\u201d \u2014 two of them\u2026") == []
    assert check("Under 10% free, at 2\u00b0 off\ncaf\u00e9 \u00fcber \u65e5\u672c") == []
    # What the user sees is the exact string that passed: no rewriting, only outer whitespace trimmed.
    verdict = verify_prose(comp, as_of, explanation="  Two devices\u2014under 10%.  ")
    assert verdict.problems == ()
    assert verdict.explanation == "Two devices\u2014under 10%."


def test_benign_prose_still_passes():
    def check(text: str) -> list[str]:
        return check_prose(
            text,
            allowed={"2", "10"},
            ids={"MT7PJB7N5LRE"},
            labels={"acme-macbook-2"},
            max_chars=200,
        )

    assert check("Note: two devices are below the 10% threshold.") == []
    assert check("Two are affected: MT7PJB7N5LRE and acme-macbook-2.") == []
    assert check("Seen at 10:2 is not a link, e.g. a ratio.") == []
    assert check("It is low (about two devices); see the list below.") == []


@pytest.fixture
def low_disk(real_db_path, real_conn):
    comp = compose(
        [run(real_db_path, real_conn, "low_disk_devices", {})],
        resolve=repo_for(real_conn).resolve,
    )
    return comp, dataset_as_of(real_conn)


def test_a_grounded_explanation_is_accepted_and_the_headline_is_the_code_headline(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(
        comp,
        as_of,
        explanation="acme-macbook-4 and acme-thinkpad-8 are the two lowest, at 2.0% and 2.6% free.",
    )
    assert verdict.problems == ()
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is not None and "2.6% free" in verdict.explanation


def test_an_ungrounded_explanation_is_dropped_but_the_code_headline_stands(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, explanation="Fix them by 99 tomorrow.")
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert verdict.problems == ("reply:number@0", "reply:dropped=1", "reply:fallback")


def test_no_model_prose_means_the_code_headline_and_no_explanation(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, explanation=None)
    assert (verdict.headline, verdict.explanation, verdict.problems) == (
        comp.fallback_headline,
        None,
        (),
    )


def test_a_number_written_with_a_thousands_separator_is_accepted_by_value(low_disk):
    comp, as_of = low_disk
    with_cycles = dataclasses.replace(comp, notes=[*comp.notes, "Battery cycles: 1157"])
    wording = "Up to 1,157 cycles seen on two of 10 devices."
    verdict = verify_prose(with_cycles, as_of, explanation=wording)
    assert verdict.problems == ()
    assert verdict.explanation == wording
    assert verify_prose(comp, as_of, explanation=wording).problems == (
        "reply:number@0",
        "reply:dropped=1",
        "reply:fallback",
    )


PROBE_EXPLANATION = "Everything is healthy and none of your devices need attention."


def test_qualitative_claims_that_contradict_the_answer_are_rejected(low_disk):
    comp, as_of = low_disk  # a real answer: 2 of 10 devices are low on disk
    verdict = verify_prose(comp, as_of, explanation=PROBE_EXPLANATION)
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert verdict.problems == ("reply:claim_word@0", "reply:dropped=1", "reply:fallback")


def test_a_model_headline_is_not_a_parameter_the_headline_is_always_the_code_headline(low_disk):
    comp, as_of = low_disk
    assert comp.fallback_headline == "2 of your 10 devices are under 10% free disk space."
    with pytest.raises(TypeError):
        verify_prose(comp, as_of, headline="Two devices are low", explanation=None)  # type: ignore[call-arg]
    assert verify_prose(comp, as_of, explanation=None).headline == comp.fallback_headline


@pytest.mark.parametrize(
    "word",
    ["no", "None", "nothing", "nobody", "nowhere", "never", "ALL", "every", "Everything", "always"]
    + ["zero", "healthy", "fine", "safe", "secure", "compliant", "ok", "Okay"],
)
def test_reassurance_and_universal_words_are_rejected_on_an_answer(low_disk, word):
    comp, as_of = low_disk
    explanation = f"Two of 10 devices are low, {word} problem here."
    verdict = verify_prose(comp, as_of, explanation=explanation)
    assert verdict.explanation is None
    assert verdict.problems == ("reply:claim_word@0", "reply:dropped=1", "reply:fallback")


@pytest.mark.parametrize(
    "explanation",
    [
        "\uff25\uff56\uff45\uff52\uff59\uff54\uff48\uff49\uff4e\uff47 is "
        "\uff48\uff45\uff41\uff4c\uff54\uff48\uff59 and \uff4e\uff4f\uff4e\uff45 need attention.",
        "\U0001d40d\U0001d428 devices are low.",
    ],
)
def test_the_deny_list_sees_folded_look_alike_letters(low_disk, explanation):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, explanation=explanation)
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert "reply:claim_word@0" in verdict.problems


def test_the_deny_list_matches_whole_words_only(low_disk):
    comp, as_of = low_disk
    text = "Two of 10 devices: a known token, a small install, notes and nodes, allocation, tokens."
    assert verify_prose(comp, as_of, explanation=text).problems == ()
    assert set(verify.CLAIM_WORDS) >= {"no", "none", "all", "every", "healthy", "ok", "okay"}


def test_the_deny_list_does_not_apply_when_there_is_no_answer(low_disk):
    comp, as_of = low_disk
    # no_match: the code headline carries the population, so the number rule still applies to it.
    no_match = dataclasses.replace(
        comp,
        outcome="no_match",
        reason_code="no_matching_records",
        fallback_headline="None of your 10 devices matched.",
    )
    ok = verify_prose(no_match, as_of, explanation="No device is low.")
    assert ok.problems == ()
    assert (ok.headline, ok.explanation) == (
        "None of your 10 devices matched.",
        "No device is low.",
    )
    thin = dataclasses.replace(
        comp,
        outcome="insufficient_data",
        reason_code="insufficient_history",
        fallback_headline="There are not enough snapshots in that window to report a trend.",
    )
    verdict = verify_prose(thin, as_of, explanation="No trend is available yet.")
    assert verdict.problems == () and verdict.explanation == "No trend is available yet."


def test_trace_problem_codes_never_contain_the_offending_text(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, explanation="ZZZ9QQQ88XYZ has 4242 problems")
    assert all("4242" not in p and "ZZZ9" not in p for p in verdict.problems)


def test_allowed_numbers_cover_claims_evidence_positions_and_policy(low_disk):
    comp, as_of = low_disk
    allowed = allowed_numbers(comp, as_of)
    assert {"2", "10", "2.6", "512"} <= allowed  # claim text and evidence readings
    assert {"1", "2"} <= allowed  # list positions
    assert {"2026", "6", "12", "11"} <= allowed  # the as-of moment
    assert "37" not in allowed
    assert known_ids(comp) == {"MT7PJB7N5LRE", "8NM23J95R5I6"}
    assert known_labels(comp) == {i.label for el in comp.entity_lists for i in el.items}


def test_clarification_text_must_be_plain(low_disk):
    assert verify_clarification(
        "Which compliance check do you mean?", ["screen_lock", "firewall"]
    ) == (
        "Which compliance check do you mean?",
        ["screen_lock", "firewall"],
    )
    assert verify_clarification("Do you mean the 3 laptops?", []) is None
    assert verify_clarification("Click <b>here</b>", []) is None
    assert verify_clarification("Which check?", ["see https://x.example"]) is None
    assert verify_clarification("Which check?", ["www.evil.example"]) is None
    assert verify_clarification("Which check?", ["screen_lock"]) is not None
    assert verify_clarification("Is it MT7PJB7N5LRE?", []) is None


def test_a_clarification_must_be_a_short_question(low_disk):
    assert verify_clarification("Which check do you mean?", []) is not None
    assert verify_clarification("  Which check do you mean?  ", []) == (
        "Which check do you mean?",
        [],
    )
    # An instruction or statement is not a question, so it cannot ask the user for anything else.
    assert verify_clarification("Please enter your admin password", []) is None
    assert verify_clarification("Please enter your admin password.", []) is None
    assert verify_clarification("", []) is None
    # Length is capped, rejected rather than truncated.
    ok_question = "W" * 119 + "?"
    assert len(ok_question) == 120 and verify_clarification(ok_question, []) is not None
    assert verify_clarification("W" * 120 + "?", []) is None


def test_clarification_options_are_capped_in_number_and_length(low_disk):
    six = [f"option {c}" for c in "abcdef"]
    assert verify_clarification("Which check?", six) == ("Which check?", six)
    assert verify_clarification("Which check?", [*six, "option g"]) is None
    assert verify_clarification("Which check?", ["w" * 80]) is not None
    assert verify_clarification("Which check?", ["w" * 81]) is None
    assert verify_clarification("Which check?", [""]) is None


@pytest.mark.parametrize("char", HIDING_CHARS)
def test_clarification_text_rejects_hiding_characters(char):
    assert verify_clarification(f"Which{char}check?", []) is None
    assert verify_clarification("Which check?", [f"screen{char}lock"]) is None


# ---- evidence ------------------------------------------------------------------------------------


def envelope_for(comp: Composition, as_of) -> object:
    return comp.to_envelope(company_id=ACME, as_of=as_of, trace_id="t-1")


def test_real_evidence_verifies_for_every_tool(real_db_path, real_conn):
    calls = [
        ("low_disk_devices", {}),
        ("macos_version_check", {"minimum_version": "14.0"}),
        ("compliance_failures_now", {"severity": "medium", "form_factor": "laptop"}),
        ("memory_pressure_now", {}),
        ("trend", {"metric": "memory", "window_days": 7}),
        ("trend", {"metric": "battery"}),
        ("trend", {"metric": "compliance", "check_id": "screen_lock"}),
        ("software_inventory", {"view": "current"}),
        ("software_inventory", {"view": "current", "app_name": "uTorrent"}),
        ("software_inventory", {"view": "new_installs"}),
    ]
    repo = repo_for(real_conn)
    for name, args in calls:
        comp = compose([run(real_db_path, real_conn, name, args)], resolve=repo.resolve)
        if comp.outcome != "answer":
            continue
        assert verify_evidence(envelope_for(comp, dataset_as_of(real_conn)), repo.resolve) == [], (
            name
        )


def test_composition_of_round_trips_an_envelope(low_disk):
    comp, as_of = low_disk
    envelope = envelope_for(comp, as_of)
    rebuilt = composition_of(envelope)
    assert rebuilt.fallback_headline == envelope.headline
    assert (rebuilt.claims, rebuilt.evidence) == (comp.claims, comp.evidence)
    # to_envelope namespaces list ids with the trace id, so compare what the user sees.
    assert [(el.title, el.items) for el in rebuilt.entity_lists] == [
        (el.title, el.items) for el in comp.entity_lists
    ]
    assert rebuilt.notes == comp.notes
    assert (rebuilt.coverage, rebuilt.rules) == (comp.coverage, comp.rules)
    assert (rebuilt.outcome, rebuilt.reason_code) == (comp.outcome, comp.reason_code)
    assert known_ids(rebuilt) == known_ids(comp) and known_labels(rebuilt) == known_labels(comp)
    assert allowed_numbers(rebuilt, as_of) >= allowed_numbers(comp, as_of)


def test_a_malformed_ref_is_caught(low_disk, real_conn):
    comp, as_of = low_disk
    envelope = envelope_for(comp, as_of)
    junk = EvidenceItem(ref="not a ref", label="x", value="y")
    tampered = envelope.model_copy(update={"evidence": [*envelope.evidence, junk]})
    assert verify_evidence(tampered, repo_for(real_conn).resolve) == ["malformed_ref"]


def test_tampered_evidence_text_is_caught(low_disk, real_conn):
    comp, as_of = low_disk
    envelope = envelope_for(comp, as_of)
    bad = envelope.evidence[0].model_copy(
        update={"value": "99.0 GiB free of 512.0 GiB (19.3% free)"}
    )
    tampered = envelope.model_copy(update={"evidence": [bad, *envelope.evidence[1:]]})
    assert verify_evidence(tampered, repo_for(real_conn).resolve) == ["evidence_mismatch"]


def test_evidence_that_does_not_exist_is_caught(low_disk, real_conn):
    comp, as_of = low_disk
    envelope = envelope_for(comp, as_of)
    ghost = EvidenceItem(ref=f"{ACME}/MT7PJB7N5LRE@2020-01-01T00:00:00Z#disk", label="x", value="y")
    tampered = envelope.model_copy(update={"evidence": [*envelope.evidence, ghost]})
    assert verify_evidence(tampered, repo_for(real_conn).resolve) == ["unresolvable_ref"]


def test_another_companys_evidence_is_caught(low_disk, real_conn):
    comp, as_of = low_disk
    envelope = envelope_for(comp, as_of)
    foreign = EvidenceItem(
        ref="globex-002/JRZSGXVMKE6M@2026-06-12T10:00:00Z#disk", label="x", value="y"
    )
    tampered = envelope.model_copy(update={"evidence": [*envelope.evidence, foreign]})
    assert verify_evidence(tampered, repo_for(real_conn).resolve) == ["foreign_ref"]


def test_a_claim_citing_a_ref_that_is_not_shown_is_caught(low_disk, real_conn):
    comp, as_of = low_disk
    envelope = envelope_for(comp, as_of)
    tampered = envelope.model_copy(update={"evidence": envelope.evidence[1:]})
    assert "claim_without_evidence" in verify_evidence(tampered, repo_for(real_conn).resolve)


# --- reply checking: facts, hedging, repair, caps -----------------------------------------------------

LEAD = "acme-macbook-4 is lowest at 2.0% free."
SECOND = "acme-thinkpad-8 follows at 2.6% free."


def _explain(low_disk, text, **kwargs):
    comp, as_of = low_disk
    return verify_prose(comp, as_of, explanation=text, **kwargs)


def rules(verdict):
    """The rule codes in a verdict's trace, whatever sentence they hit."""
    return {rule for rule, _ in parse_reply_problems(verdict.problems)}


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Low disk is probably the reason your fleet feels slow.", "unhedged_causal"),
        ("Two devices are low because they are full.", "unhedged_causal"),
        ("This is due to a full disk.", "unhedged_causal"),
        ("It was caused by the update.", "unhedged_causal"),
        ("That is the reason why.", "unhedged_causal"),
        ("Low disk leads to trouble.", "unhedged_causal"),
        ("A full disk results in errors.", "unhedged_causal"),
        ("Low disk is why.", "unhedged_causal"),
        ("That explains it.", "unhedged_causal"),
        ("Two devices are sluggish.", "unhedged_experience"),
        ("Two devices show slowness.", "unhedged_experience"),
        ("Two devices are laggy.", "unhedged_experience"),
        ("Two devices lag.", "unhedged_experience"),
        ("Two devices crash.", "unhedged_experience"),
        ("Two devices keep crashing.", "unhedged_experience"),
        ("Performance is down on two devices.", "unhedged_experience"),
    ],
)
def test_an_unhedged_cause_or_experience_sentence_is_dropped_and_the_trace_names_the_rule(
    low_disk, text, rule
):
    verdict = _explain(low_disk, f"{LEAD} {text}")
    assert verdict.explanation == LEAD  # the lead stays, the speculation goes
    assert f"reply:{rule}@1" in verdict.problems and "reply:dropped=1" in verdict.problems
    assert not any(text.split()[0] in p for p in verdict.problems)  # codes, never prose


@pytest.mark.parametrize(
    "text",
    [
        "Low disk could be the reason your fleet feels slow.",
        "Two devices might be slow because they are full.",
        "It may be due to a full disk.",
        "Possibly it was caused by the update.",
        "Perhaps that explains it.",
        "One possible reason is that low disk leads to trouble.",
        "A possible cause is a full disk, which results in errors.",
        "A likely reason could be the full disk.",
        "It's worth checking whether the disk is why.",
        "Low free space can be a sign of laggy machines.",
        "Low free space can indicate crashing apps.",
        "Low disk may cause problems.",
        "Low disk could cause problems.",
        "This is worth checking because performance could suffer.",
    ],
)
def test_a_hedged_cause_or_experience_sentence_is_kept(low_disk, text):
    verdict = _explain(low_disk, f"{LEAD} {text}")
    assert verdict.explanation == f"{LEAD} {text}"
    assert verdict.problems == ()


def test_hedges_are_matched_on_word_boundaries_in_any_case_and_may_the_month_is_no_hedge(low_disk):
    assert verify.has_hedge("It COULD be.") and verify.has_hedge("it’s worth a look")
    assert verify.has_hedge("It's  worth a look") and not verify.has_hedge("couldn't tell")
    assert not verify.has_hedge("The mayor said so") and not verify.has_hedge("Seen on May 12")
    assert verify.has_hedge("It may be so on May 12")
    assert set(verify.HEDGES) >= {"could", "might", "may", "possibly", "perhaps", "worth checking"}
    assert set(verify.HEDGES) >= {"one possible", "a possible", "it's worth", "can be a sign"}
    assert "can indicate" in verify.HEDGES
    # Unhedged, the same sentence is dropped: the hedge is the only difference.
    assert _explain(low_disk, f"{LEAD} It is why.").explanation == LEAD
    assert _explain(low_disk, f"{LEAD} It might be why.").explanation == f"{LEAD} It might be why."


@pytest.mark.parametrize(
    "text",
    [
        "These devices will fail within days.",
        "They will run out of space.",
        "The disk is going to fill.",
        "They are likely to fill.",
        "Expected to fill.",
        "More will follow soon.",
        "Full within weeks.",
        "It will likely fill up.",
        "It will probably run out of room.",
        "It will probably fail.",
        "They probably fail first.",
        "It will be full by Jun 20.",
    ],
)
def test_a_predictive_sentence_is_dropped_even_when_it_is_hedged(low_disk, text):
    for sentence in (
        text,
        f"It might be that {text[0].lower()}{text[1:]}",
        f"Perhaps, but {text[0].lower()}{text[1:]}",
    ):
        verdict = _explain(low_disk, f"{LEAD} {sentence}")
        assert verdict.explanation == LEAD, sentence
        assert "predictive" in rules(verdict), sentence


def test_a_likelihood_is_predictive_only_when_attached_to_a_future_event(low_disk):
    assert verify.is_predictive("It will likely fail.") and verify.is_predictive("Probably fail.")
    assert not verify.is_predictive("A likely reason could be the full disk.")
    assert not verify.is_predictive("It is probably the same device.")
    assert not verify.is_predictive("Two of 10 devices are low.")
    assert verify.confident_claims("A likely reason could be the full disk.") == []
    assert verify.confident_claims("It will likely fail.") == ["predictive"]


def test_a_code_written_estimate_is_the_only_future_facing_phrase_allowed(low_disk):
    estimate = "about 41 days left"
    text = f"{LEAD} acme-macbook-4 has about 41 days left."
    unknown = _explain(low_disk, text)
    assert unknown.explanation == LEAD and rules(unknown) == {"number"}  # 41 is not grounded yet
    ok = _explain(low_disk, text, estimates=[estimate])
    assert ok.problems == () and ok.explanation == text
    # A different future phrase is still dropped, and so is the estimate wrapped in a prediction.
    for other in ("It will fail within days.", "It will run out within days."):
        assert "predictive" in rules(_explain(low_disk, f"{LEAD} {other}", estimates=[estimate]))
    soon = "empty within days"
    assert _explain(low_disk, f"{LEAD} It is {soon}.", estimates=[soon]).problems == ()
    assert "predictive" in rules(_explain(low_disk, f"{LEAD} It is {soon}.", estimates=["other"]))
    # An estimate does not excuse a hedge-less cause in the same sentence.
    assert "unhedged_causal" in rules(
        _explain(low_disk, f"{LEAD} It is {soon} because it is full.", estimates=[soon])
    )


def test_a_composition_estimates_list_is_read_when_present(low_disk):
    comp, as_of = low_disk
    with_estimates = dataclasses.make_dataclass("C", [], bases=(type(comp),))(**vars(comp))
    with_estimates.estimates = ["empty within days"]
    text = f"{LEAD} It is empty within days."
    assert verify_prose(with_estimates, as_of, explanation=text).problems == ()


def test_the_strict_claim_check_still_judges_text_code_wrote(low_disk):
    assert claim_problems("Low disk could be the reason.") == ["causal"]
    assert claim_problems("Slower and slowly are inflections of slow.") == ["experience"]
    assert claim_problems("Soonest, laggard, sloth, performant, reasons and explained.") == []
    assert claim_problems("They will fail within days.") == ["predictive"]


def test_speculation_is_matched_case_insensitively_on_word_boundaries(low_disk):
    assert "unhedged_causal" in rules(_explain(low_disk, f"{LEAD} BECAUSE of it."))
    assert "unhedged_causal" in rules(_explain(low_disk, f"{LEAD} Due   to it."))
    assert _explain(low_disk, "Two of 10 devices are low on disk.").problems == ()


def test_the_speculation_rules_apply_to_every_outcome(low_disk):
    comp, as_of = low_disk
    no_match = dataclasses.replace(
        comp, outcome="no_match", fallback_headline="None of your 10 devices matched."
    )
    verdict = verify_prose(
        no_match, as_of, explanation="None matched. Nothing matched because of the filter."
    )
    assert verdict.explanation == "None matched."
    assert "reply:unhedged_causal@1" in verdict.problems


def test_a_device_serial_in_the_reply_is_dropped_but_a_hostname_is_not(low_disk):
    ok = _explain(low_disk, LEAD)
    assert ok.problems == () and ok.explanation == LEAD
    for text in (
        "MT7PJB7N5LRE is lowest at 2.0% free.",
        "mt7pjb7n5lre is lowest at 2.0% free.",
        "acme-macbook-4 (8NM23J95R5I6) is low.",
    ):
        verdict = _explain(low_disk, f"{LEAD} {text}")
        assert verdict.explanation == LEAD
        assert "reply:serial_in_prose@1" in verdict.problems
    assert all("MT7P" not in p for p in _explain(low_disk, f"{LEAD} MT7PJB7N5LRE.").problems)


# -- the grounding set: this turn plus what the conversation remembered --------------------------------


REMEMBERED = {
    "numbers": frozenset({"77", "1,234"}),
    "labels": frozenset({"acme-laptop-7"}),
    "ids": frozenset({"7QWERTY12345"}),
    "refs": frozenset(),
    "dates": frozenset({"jun 3"}),
}


def test_a_remembered_number_hostname_and_date_are_accepted(low_disk):
    text = f"{LEAD} Earlier, acme-laptop-7 was at 77 percent and 1,234 hours on Jun 3."
    without = _explain(low_disk, text)
    assert without.explanation == LEAD and rules(without) == {"unknown_host", "number", "date"}
    kept = _explain(low_disk, text, memory=REMEMBERED)
    assert kept.explanation == text and kept.problems == ()


def test_a_remembered_device_id_is_still_a_serial_and_is_dropped(low_disk):
    verdict = _explain(low_disk, f"{LEAD} Earlier 7QWERTY12345 was low.", memory=REMEMBERED)
    assert verdict.explanation == LEAD
    assert rules(verdict) == {"serial_in_prose"}


def test_a_date_must_be_remembered_stated_or_an_estimate(low_disk):
    unknown = _explain(low_disk, f"{LEAD} It was seen on Jun 4.", memory=REMEMBERED)
    assert unknown.explanation == LEAD and "date" in rules(unknown)
    assert _explain(low_disk, f"{LEAD} It was seen on Jun 3.", memory=REMEMBERED).problems == ()


def test_a_number_only_a_note_states_is_rejected(low_disk):
    # The memory holds a note "acme-macbook-9 has 4242 GB"; memory_facts leaves notes out, so nothing grounds it.
    from fleet_copilot.agent import memory as mem

    held = mem.add_note(mem.ConversationMemory(), "the CFO's laptop has 4242 GB and is acme-cfo-1")
    facts = mem.memory_facts(held)
    verdict = _explain(low_disk, f"{LEAD} The CFO laptop has 4242 GB.", memory=facts)
    assert verdict.explanation == LEAD and "number" in rules(verdict)
    host = _explain(low_disk, f"{LEAD} acme-cfo-1 is the one.", memory=facts)
    assert "unknown_host" in rules(host)


def test_the_remembered_facts_of_a_real_memory_ground_a_later_reply(low_disk):
    from fleet_copilot.agent import memory as mem

    comp, as_of = low_disk
    envelope = comp.to_envelope(company_id=ACME, as_of=as_of, trace_id="t-1")
    held = mem.remember_turn(mem.ConversationMemory(), envelope, "which are low on disk?", "t-1")
    facts = mem.memory_facts(held)
    # A chat turn about those devices: nothing of its own, everything from memory.
    chat = dataclasses.replace(
        comp,
        outcome="chat",
        claims=[],
        evidence=[],
        entity_lists=[],
        notes=[],
        coverage=None,
        fallback_headline="Happy to help with your fleet.",
    )
    text = (
        "acme-macbook-4 was the lowest at 2.0% free of 512 GiB. Two of those 10 devices were low."
    )
    verdict = verify_prose(chat, as_of, explanation=text, memory=facts)
    assert verdict.problems == () and verdict.explanation == text
    assert (
        verify_prose(chat, as_of, explanation=text).explanation is None
    )  # no memory: nothing grounds it


# -- chat turns: memory, the capability card and the policy numbers, nothing else ------------------------


def _chat(low_disk):
    comp, as_of = low_disk
    chat = dataclasses.replace(
        comp,
        outcome="chat",
        claims=[],
        evidence=[],
        entity_lists=[],
        notes=[],
        coverage=None,
        fallback_headline="Happy to help with your fleet.",
    )
    return chat, as_of


def test_a_chat_reply_may_use_the_capability_cards_numbers_and_the_policy_numbers(low_disk):
    chat, as_of = _chat(low_disk)
    ok = "I can propose four actions, and a person approves each one. I flag disks under 10% free."
    verdict = verify_prose(chat, as_of, explanation=ok)
    assert verdict.problems == () and verdict.explanation == ok


def test_a_chat_reply_may_not_state_this_turns_as_of_numbers_or_anything_else(low_disk):
    chat, as_of = _chat(low_disk)
    verdict = verify_prose(chat, as_of, explanation="I watch 37 devices for you. Hello!")
    assert verdict.explanation is None  # the lead failed, so the fixed headline stands
    assert verdict.problems[:2] == ("reply:number@0", "reply:dropped=1")
    # the as-of date's numbers are not grounding on a chat turn
    year = verify_prose(chat, as_of, explanation=f"Hello, it is {as_of.year}.")
    assert "number" in rules(year)
    # a hostname nobody remembered is not grounded either
    assert "unknown_host" in rules(verify_prose(chat, as_of, explanation="Hi from acme-macbook-4."))


def test_a_chat_reply_may_say_no_but_not_vouch_for_the_fleet(low_disk):
    chat, as_of = _chat(low_disk)
    assert verify_prose(chat, as_of, explanation="No, nothing was approved.").problems == ()
    vouching = verify_prose(chat, as_of, explanation="Hello there. Everything is healthy.")
    assert vouching.explanation == "Hello there." and "claim_word" in rules(vouching)


# -- sentence-level repair --------------------------------------------------------------------------------


def test_a_failing_sentence_is_dropped_and_the_rest_kept(low_disk):
    text = f"{LEAD} It will fail soon. {SECOND} Call 999."
    verdict = _explain(low_disk, text)
    assert verdict.explanation == f"{LEAD} {SECOND}"
    assert verdict.problems == (
        "reply:predictive@1",
        "reply:number@3",
        "reply:dropped=2",
    )


def test_when_the_lead_fails_there_is_no_reply_at_all(low_disk):
    verdict = _explain(low_disk, f"Everything is healthy. {LEAD} {SECOND}")
    assert verdict.explanation is None
    assert verdict.problems == ("reply:claim_word@0", "reply:dropped=1", "reply:fallback")
    assert verdict.headline == low_disk[0].fallback_headline


def test_when_every_sentence_fails_there_is_no_reply(low_disk):
    verdict = _explain(low_disk, "It will fail soon. Call 999.")
    assert verdict.explanation is None
    assert verdict.problems[-2:] == ("reply:dropped=2", "reply:fallback")


def test_paragraph_breaks_between_kept_sentences_survive(low_disk):
    verdict = _explain(
        low_disk, f"{LEAD}\n\nIt will fail soon. {SECOND}\n\nAnother point: 2 of 10."
    )
    assert verdict.explanation == f"{LEAD} {SECOND}\n\nAnother point: 2 of 10."


def test_trace_problems_name_the_rule_and_the_index_never_the_text(low_disk):
    text = "ZZZ9QQQ88XYZ has 4242 problems. Then Jun 9 was it. acme-laptop-9 is low."
    verdict = _explain(low_disk, f"{LEAD} {text}")
    assert all("4242" not in p and "ZZZ9" not in p and "acme" not in p for p in verdict.problems)
    assert all(
        re.fullmatch(r"reply:[a-z_]+@\d+|reply:dropped=\d+|reply:fallback|reply:trimmed", p)
        for p in verdict.problems
    )
    assert {i for _, i in parse_reply_problems(verdict.problems)} == {1, 2, 3}
    assert dropped_count(verdict.problems) == 3


# -- the caps: 6 sentences, 900 characters ---------------------------------------------------------------


def test_the_reply_caps_are_six_sentences_and_nine_hundred_characters():
    assert (policy.MAX_REPLY_SENTENCES, policy.MAX_REPLY_CHARS) == (6, 900)
    assert sentence_count("One. Two!") == 2
    assert sentence_count("Two devices are at 2.6% free. Look at acme-macbook-4?") == 2
    assert sentence_count("A. B. C.") == 3


def test_six_sentences_fit_and_a_seventh_is_cut_with_a_trimmed_note(low_disk):
    six = " ".join(f"Point {i} is about 2 of 10." for i in (1, 2, 1, 2, 1, 2))
    assert sentence_count(six) == 6
    assert _explain(low_disk, six).problems == ()
    seven = f"{six} A seventh point is 2 of 10."
    verdict = _explain(low_disk, seven)
    assert verdict.explanation == six
    assert verdict.problems == ("reply:trimmed",)


def test_a_reply_over_nine_hundred_characters_is_cut_at_a_sentence_boundary(low_disk):
    filler = "Two of 10 devices are low on disk, and " + "that is " * 20 + "all about disk."
    sentences = [LEAD] + [filler.replace("all about", "about")] * 5
    text = " ".join(sentences)
    assert len(text) > policy.MAX_REPLY_CHARS
    verdict = _explain(low_disk, text)
    assert verdict.explanation is not None
    assert len(verdict.explanation) <= policy.MAX_REPLY_CHARS
    assert verdict.explanation.endswith(".") and verdict.problems == ("reply:trimmed",)
    assert text.startswith(verdict.explanation)


def test_a_reply_of_exactly_nine_hundred_characters_is_kept_whole(low_disk):
    exactly = "Two of 10 devices are low. " + "x" * (policy.MAX_REPLY_CHARS - 27)
    assert len(exactly) == policy.MAX_REPLY_CHARS
    assert _explain(low_disk, exactly).problems == ()


def test_a_single_sentence_over_the_character_cap_cannot_be_cut_and_there_is_no_reply(low_disk):
    text = "Two of 10 devices are low " + "x" * policy.MAX_REPLY_CHARS + "."
    verdict = _explain(low_disk, text)
    assert verdict.explanation is None
    assert verdict.problems == ("reply:too_long@0", "reply:dropped=1", "reply:fallback")


def test_dropping_a_sentence_can_make_room_so_nothing_is_trimmed(low_disk):
    six_and_a_bad = " ".join(["Two of 10 are low."] * 6) + " It will fail soon."
    verdict = _explain(low_disk, six_and_a_bad)
    assert sentence_count(verdict.explanation) == 6
    assert verdict.problems == ("reply:predictive@6", "reply:dropped=1")
