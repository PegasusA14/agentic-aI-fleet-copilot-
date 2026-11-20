import dataclasses
from datetime import UTC, datetime

import pytest
from agent_support import ACME, repo_for, run

from fleet_copilot import policy
from fleet_copilot.agent.compose import Composition, compose
from fleet_copilot.agent.verify import (
    allowed_numbers,
    check_prose,
    composition_of,
    known_ids,
    known_labels,
    number_tokens,
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

    assert check("\u2460 device", {"2"}) == ["number:1"]  # circled digit one
    assert check("fi\u200bfty devices", {"2"}) == ["number:50"]  # zero-width split
    assert check("fi\u2060fty\ufeff devices", {"2"}) == ["number:50"]
    assert check("\uff12 devices", {"2"}) == []  # fullwidth two equals an allowed 2
    assert check("\uff13\uff17 devices", {"2"}) == ["number:37"]
    assert check("twice as many", set()) == ["number:2"]
    assert check("twice as many", {"2"}) == []
    assert check("a dozen devices", {"2"}) == ["number:12"]
    assert check("half of them", {"2"}) == ["number:0.5"]


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
        explanation="MT7PJB7N5LRE and 8NM23J95R5I6 are the two lowest, at 2.0% and 2.6% free.",
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
        comp, as_of, headline="Two devices are low", explanation="Fix them by 99 tomorrow."
    )
    assert verdict.headline == "Two devices are low"
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
    verdict = verify_prose(with_cycles, as_of, headline="Up to 1,157 cycles seen", explanation=None)
    assert verdict.problems == ()
    assert verdict.headline == "Up to 1,157 cycles seen"
    assert verify_prose(comp, as_of, headline="Up to 1,157 cycles", explanation=None).problems == (
        "headline:number",
    )


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
