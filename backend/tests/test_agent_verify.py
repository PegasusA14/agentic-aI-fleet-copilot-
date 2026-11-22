import dataclasses
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
    known_ids,
    known_labels,
    number_tokens,
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
def test_a_headline_with_a_hiding_character_falls_back_to_the_code_headline(low_disk, char):
    comp, as_of = low_disk
    text = f"1{char}0 of 1{char}0 devices are low on disk"
    verdict = verify_prose(comp, as_of, headline=text, explanation=text)
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert "headline:bad_chars" in verdict.problems and "explanation:bad_chars" in verdict.problems
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
    verdict = verify_prose(
        comp,
        as_of,
        headline="  Two of 10 devices\u2026 \u201clow\u201d  ",
        explanation="Two devices\u2014under 10%.",
    )
    assert verdict.problems == ()
    assert verdict.headline == "Two of 10 devices\u2026 \u201clow\u201d"
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


def test_a_grounded_headline_and_explanation_are_accepted(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(
        comp,
        as_of,
        headline="Two devices are below the 10% free-space threshold",
        explanation="acme-macbook-4 and acme-thinkpad-8 are the two lowest, at 2.0% and 2.6% free.",
    )
    assert verdict.problems == ()
    assert verdict.headline == "Two devices are below the 10% free-space threshold"
    assert verdict.explanation is not None and "2.6% free" in verdict.explanation


@pytest.mark.parametrize(
    ("headline", "code"),
    [
        ("37 devices are low on disk", "headline:number"),
        ("About fifty devices are affected", "headline:number"),
        ("ZZZ9QQQ88XYZ is also low", "headline:unknown_id"),
        ("See <b>this</b>", "headline:markup"),
        ("x" * (policy.MAX_HEADLINE_CHARS + 1), "headline:too_long"),
    ],
)
def test_an_ungrounded_headline_is_replaced_by_the_code_headline(low_disk, headline, code):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, headline=headline, explanation=None)
    assert verdict.headline == comp.fallback_headline
    assert code in verdict.problems


def test_an_ungrounded_explanation_is_dropped_but_the_headline_can_stand(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(
        comp, as_of, headline="Two of 10 devices are low", explanation="Fix them by 99 tomorrow."
    )
    assert verdict.headline == "Two of 10 devices are low"
    assert verdict.explanation is None
    assert verdict.problems == ("explanation:number",)


def test_no_model_prose_means_the_code_headline_and_no_explanation(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, headline=None, explanation=None)
    assert (verdict.headline, verdict.explanation, verdict.problems) == (
        comp.fallback_headline,
        None,
        (),
    )


def test_a_number_written_with_a_thousands_separator_is_accepted_by_value(low_disk):
    comp, as_of = low_disk
    with_cycles = dataclasses.replace(comp, notes=[*comp.notes, "Battery cycles: 1157"])
    wording = "Up to 1,157 cycles seen on two of 10 devices"
    verdict = verify_prose(with_cycles, as_of, headline=wording, explanation=None)
    assert verdict.problems == ()
    assert verdict.headline == wording
    assert verify_prose(comp, as_of, headline=wording, explanation=None).problems == (
        "headline:number",
    )


PROBE_HEADLINE = "No devices are low on disk"
PROBE_EXPLANATION = "Everything is healthy and none of your devices need attention."


def test_qualitative_claims_that_contradict_the_answer_are_rejected(low_disk):
    comp, as_of = low_disk  # a real answer: 2 of 10 devices are low on disk
    verdict = verify_prose(comp, as_of, headline=PROBE_HEADLINE, explanation=PROBE_EXPLANATION)
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert set(verdict.problems) == {
        "headline:restates_nothing",
        "headline:claim_word",
        "explanation:claim_word",
    }


def test_a_headline_must_restate_every_number_of_the_code_headline(low_disk):
    comp, as_of = low_disk
    assert comp.fallback_headline == "2 of your 10 devices are under 10% free disk space."
    faithful = verify_prose(
        comp,
        as_of,
        headline="Two of your 10 devices are under 10% free disk space.",
        explanation=None,
    )
    assert faithful.problems == ()
    assert faithful.headline == "Two of your 10 devices are under 10% free disk space."
    # Grounded numbers alone are not enough: a headline that drops the count or the population says less
    # than the answer, and a vague one can be wrong without containing a single wrong number.
    for vague in (
        "Two devices are low",
        "Several devices are low on disk",
        "10 devices were checked",
    ):
        verdict = verify_prose(comp, as_of, headline=vague, explanation=None)
        assert verdict.headline == comp.fallback_headline
        assert verdict.problems == ("headline:restates_nothing",), vague


@pytest.mark.parametrize(
    "word",
    ["no", "None", "nothing", "nobody", "nowhere", "never", "ALL", "every", "Everything", "always"]
    + ["zero", "healthy", "fine", "safe", "secure", "compliant", "ok", "Okay"],
)
def test_reassurance_and_universal_words_are_rejected_on_an_answer(low_disk, word):
    comp, as_of = low_disk
    explanation = f"Two of 10 devices are low, {word} problem here."
    verdict = verify_prose(comp, as_of, headline=None, explanation=explanation)
    assert verdict.explanation is None
    assert verdict.problems == ("explanation:claim_word",)
    headline = f"{word} - 2 of 10 devices are under 10% free"
    assert verify_prose(comp, as_of, headline=headline, explanation=None).problems == (
        "headline:claim_word",
    )


@pytest.mark.parametrize(
    ("headline", "explanation"),
    [
        (
            "\uff25\uff56\uff45\uff52\uff59\uff54\uff48\uff49\uff4e\uff47 is ok",
            "\uff25\uff56\uff45\uff52\uff59\uff54\uff48\uff49\uff4e\uff47 is "
            "\uff48\uff45\uff41\uff4c\uff54\uff48\uff59 and \uff4e\uff4f\uff4e\uff45 need attention.",
        ),
        ("\U0001d40d\U0001d428 devices are low: 2 of 10", "\U0001d40d\U0001d428 devices are low."),
    ],
)
def test_the_deny_list_sees_folded_look_alike_letters(low_disk, headline, explanation):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, headline=headline, explanation=explanation)
    assert verdict.headline == comp.fallback_headline
    assert verdict.explanation is None
    assert "headline:claim_word" in verdict.problems
    assert "explanation:claim_word" in verdict.problems


def test_the_deny_list_matches_whole_words_only(low_disk):
    comp, as_of = low_disk
    text = "Two of 10 devices: a known token, a small install, notes and nodes, allocation, tokens."
    assert verify_prose(comp, as_of, headline=None, explanation=text).problems == ()
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
    ok = verify_prose(
        no_match, as_of, headline="None of the 10 devices matched", explanation="No device is low."
    )
    assert ok.problems == ()
    assert (ok.headline, ok.explanation) == ("None of the 10 devices matched", "No device is low.")
    dropped = verify_prose(no_match, as_of, headline="Nothing matched", explanation=None)
    assert dropped.problems == ("headline:restates_nothing",)
    # insufficient_data: a code headline with no numbers imposes no numbers on the model's headline.
    thin = dataclasses.replace(
        comp,
        outcome="insufficient_data",
        reason_code="insufficient_history",
        fallback_headline="There are not enough snapshots in that window to report a trend.",
    )
    verdict = verify_prose(thin, as_of, headline="No trend is available yet", explanation=None)
    assert verdict.problems == () and verdict.headline == "No trend is available yet"


def test_trace_problem_codes_never_contain_the_offending_text(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(comp, as_of, headline="ZZZ9QQQ88XYZ has 4242 problems", explanation=None)
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


# --- speculation, serials and length (T6) -------------------------------------------------------------


def _explain(low_disk, text, **kwargs):
    comp, as_of = low_disk
    return verify_prose(comp, as_of, headline=None, explanation=text, **kwargs)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Low disk is probably the reason your fleet feels slow.", "causal"),
        ("These devices will fail within days.", "predictive"),
        ("Two devices are low because they are full.", "causal"),
        ("This is due to a full disk.", "causal"),
        ("It was caused by the update.", "causal"),
        ("That is the reason why.", "causal"),
        ("Low disk leads to trouble.", "causal"),
        ("A full disk results in errors.", "causal"),
        ("Low disk is why.", "causal"),
        ("That explains it.", "causal"),
        ("They will run out of space.", "predictive"),
        ("The disk is going to fill.", "predictive"),
        ("They are likely to fill.", "predictive"),
        ("Expected to fill.", "predictive"),
        ("More will follow soon.", "predictive"),
        ("Full within weeks.", "predictive"),
        ("Low disk may cause problems.", "predictive"),
        ("Low disk could cause problems.", "predictive"),
        ("Two devices are sluggish.", "experience"),
        ("Two devices show slowness.", "experience"),
        ("Two devices are laggy.", "experience"),
        ("Two devices lag.", "experience"),
        ("Two devices crash.", "experience"),
        ("Two devices keep crashing.", "experience"),
        ("Performance is down on two devices.", "experience"),
    ],
)
def test_speculative_prose_is_dropped_and_the_trace_names_the_rule(low_disk, text, rule):
    verdict = _explain(low_disk, text)
    assert verdict.explanation is None
    assert f"explanation:{rule}" in verdict.problems
    assert all(p.split(":")[1].isidentifier() for p in verdict.problems)  # codes, never prose


def test_the_two_audit_sentences_are_rejected(low_disk):
    for text in (
        "Low disk is probably the reason your fleet feels slow.",
        "These devices will fail within days.",
    ):
        assert _explain(low_disk, text).explanation is None


def test_speculation_is_matched_case_insensitively_on_word_boundaries(low_disk):
    assert "explanation:causal" in _explain(low_disk, "BECAUSE of it.").problems
    assert "explanation:causal" in _explain(low_disk, "Due   to it.").problems
    assert claim_problems("Slower and slowly are inflections of slow.") == ["experience"]
    assert claim_problems("Soonest, laggard, sloth, performant, reasons and explained.") == []
    assert _explain(low_disk, "Two of 10 devices are low on disk.").problems == ()


def test_the_speculation_rules_apply_to_a_model_headline_and_to_every_outcome(low_disk):
    comp, as_of = low_disk
    verdict = verify_prose(
        comp,
        as_of,
        headline="2 of your 10 devices are under 10% free disk space, probably because of updates",
        explanation=None,
    )
    assert verdict.headline == comp.fallback_headline
    assert {"headline:causal", "headline:predictive"} <= set(verdict.problems)
    no_match = dataclasses.replace(
        comp, outcome="no_match", fallback_headline="None of your 10 devices matched."
    )
    assert (
        "explanation:causal"
        in verify_prose(
            no_match, as_of, headline=None, explanation="Nothing matched because of the filter."
        ).problems
    )


def test_a_code_written_estimate_is_the_only_future_facing_phrase_allowed(low_disk):
    estimate = "about 41 days left"
    text = "acme-macbook-4 has about 41 days left."
    assert _explain(low_disk, text).problems == ("explanation:number",)  # 41 is not grounded yet
    ok = _explain(low_disk, text, estimates=[estimate])
    assert ok.problems == () and ok.explanation == text
    # A different future phrase is still rejected, and so is the estimate wrapped in a prediction.
    assert (
        "explanation:predictive"
        in _explain(low_disk, "It will fail within days.", estimates=[estimate]).problems
    )
    soon = "empty within days"
    assert (
        _explain(low_disk, "acme-macbook-4 is empty within days.", estimates=[soon]).problems == ()
    )
    assert (
        "explanation:predictive"
        in _explain(
            low_disk, "acme-macbook-4 is empty within days.", estimates=["a different phrase"]
        ).problems
    )


def test_a_composition_estimates_list_is_read_when_present(low_disk):
    comp, as_of = low_disk
    with_estimates = dataclasses.make_dataclass("C", [], bases=(type(comp),))(**vars(comp))
    with_estimates.estimates = ["empty within days"]
    assert (
        verify_prose(
            with_estimates, as_of, headline=None, explanation="acme-macbook-4 is empty within days."
        ).problems
        == ()
    )


def test_a_device_serial_in_the_explanation_is_rejected_but_a_hostname_is_not(low_disk):
    ok = _explain(low_disk, "acme-macbook-4 is lowest at 2.0% free.")
    assert ok.problems == () and ok.explanation is not None
    for text in (
        "MT7PJB7N5LRE is lowest at 2.0% free.",
        "mt7pjb7n5lre is lowest at 2.0% free.",
        "acme-macbook-4 (8NM23J95R5I6) is low.",
    ):
        verdict = _explain(low_disk, text)
        assert verdict.explanation is None
        assert "explanation:serial_in_prose" in verdict.problems
    assert all("MT7P" not in p for p in _explain(low_disk, "MT7PJB7N5LRE.").problems)


def test_the_headline_may_still_carry_a_serial_it_is_grounded(low_disk):
    comp, as_of = low_disk
    wording = "2 of your 10 devices are under 10% free disk space, led by MT7PJB7N5LRE"
    assert verify_prose(comp, as_of, headline=wording, explanation=None).problems == ()


def test_the_explanation_is_capped_at_two_sentences_and_280_characters(low_disk):
    assert policy.MAX_EXPLANATION_SENTENCES == 2 and policy.MAX_EXPLANATION_CHARS == 280
    assert sentence_count("One. Two!") == 2
    assert sentence_count("Two devices are at 2.6% free. Look at acme-macbook-4?") == 2
    assert sentence_count("A. B. C.") == 3
    two = "acme-macbook-4 is lowest at 2.0% free. acme-thinkpad-8 follows at 2.6% free."
    assert _explain(low_disk, two).problems == ()
    three = two + " Both are under 10% free."
    assert _explain(low_disk, three).problems == ("explanation:too_many_sentences",)
    exactly = "Two of 10 devices are low. " + "x" * (policy.MAX_EXPLANATION_CHARS - 27)
    assert len(exactly) == policy.MAX_EXPLANATION_CHARS
    assert "explanation:too_long" not in _explain(low_disk, exactly).problems
    assert "explanation:too_long" in _explain(low_disk, exactly + "x").problems
