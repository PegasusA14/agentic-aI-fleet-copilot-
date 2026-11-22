"""Richer refusals and clarifications: what a refused action would need, the other-company card, and
clarifications that code builds (ambiguous device phrase, a typo of the user's own company name)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from graph_support import (
    ACME_ADMIN,
    GLOBEX_ADMIN,
    INITECH_ADMIN,
    answer_plan,
    make_service,
)
from pydantic import ValidationError

from fleet_copilot import policy
from fleet_copilot.actions import guidance
from fleet_copilot.agent import disambiguation as dis
from fleet_copilot.agent import outcomes, texts
from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.llm import (
    Plan,
    ScriptedLLM,
    final_message,
    tool_calls_message,
)
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.contracts import Alternative, AnswerEnvelope, Clarification, ScopeInfo
from fleet_copilot.evals.identifiers import foreign_identifiers
from fleet_copilot.repository import Device

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
KW = {"company_id": "acme-001", "as_of": AS_OF, "trace_id": "t-1"}


def refused_record(action_type, reason="not_eligible", device_id="D1", **args):
    return ToolRecord(
        tool="x",
        args={"action_type": action_type, "device_id": device_id, **args},
        action_refusal={"reason": reason, "detail": "Only the last 1 battery readings say so."},
    )


# ---- A: a refused action says what it would need ---------------------------------------------------------


def test_replacement_needs_come_from_the_policy_constants():
    needs = guidance.needs_for("replacement_flag", "not_eligible")
    assert needs[0] == (
        f"Battery reports 'Service Recommended' in {policy.REPLACEMENT_MIN_SNAPSHOTS} or more "
        "consecutive readings"
    )
    assert any(str(policy.STALE_HOURS) in line for line in needs)


@pytest.mark.parametrize(
    ("kwargs", "fragments"),
    [
        (
            {"action_type": "upgrade_order", "component": "memory"},
            [f"{policy.MEMORY_USED_PCT:g}%", f"last {policy.UPGRADE_TREND_WINDOW_DAYS} days"],
        ),
        (
            {"action_type": "upgrade_order", "component": "storage"},
            [
                f"under {policy.LOW_DISK_FREE_PCT:g}%",
                f"last {policy.UPGRADE_TREND_WINDOW_DAYS} days",
            ],
        ),
        ({"action_type": "remediation_ticket"}, ["compliance check failing at the latest reading"]),
        (
            {"action_type": "remediation_ticket", "check_id": "screen_lock"},
            ["screen_lock check failing"],
        ),
        (
            {"action_type": "employee_notification", "topic": "low_disk"},
            [f"under {policy.LOW_DISK_FREE_PCT:g}%"],
        ),
        (
            {"action_type": "employee_notification", "topic": "memory_pressure"},
            [f"{policy.MEMORY_USED_PCT:g}%"],
        ),
        (
            {"action_type": "employee_notification", "topic": "failing_check"},
            ["compliance check failing"],
        ),
        (
            {"action_type": "employee_notification", "topic": "battery_service"},
            ["Service Recommended"],
        ),
    ],
)
def test_each_action_type_lists_what_would_make_it_possible(kwargs, fragments):
    needs = guidance.needs_for(reason="insufficient_evidence", **kwargs)
    assert 1 <= len(needs) <= 4 and all(len(line) <= 120 for line in needs)
    text = " | ".join(needs)
    for fragment in fragments:
        assert fragment in text


def test_an_upgrade_without_a_named_component_lists_both_and_a_notification_without_a_topic_lists_all():
    both = guidance.needs_for("upgrade_order", "not_eligible")
    assert len(both) == 2 and "Memory" in both[0] and "disk" in both[1]
    assert len(guidance.needs_for("employee_notification", "not_eligible")) == 4


def test_already_done_and_too_many_pending_say_what_would_clear_them():
    done = guidance.needs_for("replacement_flag", "already_done")
    assert str(policy.ACTION_REPEAT_WINDOW_DAYS) in done[0]
    pending = guidance.needs_for("replacement_flag", "too_many_pending")
    assert str(policy.MAX_PENDING_PER_TENANT) in pending[0]


def test_alternatives_name_the_device_and_fit_the_action():
    host = "initech-mac-4"
    replacement = guidance.alternatives_for("replacement_flag", host)
    assert [(a.label, a.request) for a in replacement] == [
        ("Show its 30-day health", "Show the 30-day history of initech-mac-4"),
        ("Check battery wear", "Which batteries are wearing?"),
    ]
    memory = guidance.alternatives_for("upgrade_order", host, component="memory")
    assert memory[1].request == "Which devices are consistently RAM-constrained?"
    assert guidance.alternatives_for("upgrade_order", host, component="storage")[1].label == (
        "Devices low on disk"
    )
    assert guidance.alternatives_for("remediation_ticket", host)[1].label == (
        "Failing compliance checks"
    )
    assert (
        len(guidance.alternatives_for("employee_notification", host, topic="battery_service")) == 2
    )


def test_the_composer_attaches_needs_alternatives_and_the_reason_line_to_a_refused_action():
    record = refused_record("replacement_flag", "insufficient_evidence", device_id="D1")
    comp = compose([record], resolve=lambda ref: None, device_label=lambda i: "initech-mac-4")
    envelope = comp.to_envelope(**KW)
    assert (envelope.outcome, envelope.reason_code) == ("denied", "insufficient_evidence")
    assert envelope.notes == ["Only the last 1 battery readings say so."]
    assert envelope.needs == guidance.needs_for("replacement_flag", "insufficient_evidence")
    assert envelope.alternatives[0].request == "Show the 30-day history of initech-mac-4"


def test_without_a_label_lookup_the_device_id_names_the_device():
    comp = compose([refused_record("replacement_flag", device_id="D9")], resolve=lambda ref: None)
    assert comp.alternatives[0].request == "Show the 30-day history of D9"


async def test_a_replacement_request_for_a_healthy_device_is_refused_with_needs(
    real_db_path, tmp_path
):
    llm = ScriptedLLM(
        plans=[
            Plan(intent="propose", summary="flag for replacement", action_type="replacement_flag")
        ],
        acts=[
            tool_calls_message(("flag_device_for_replacement", {"device_id": "DXUCJ272DCYX"})),
            final_message(),
        ],
    )
    service = make_service(real_db_path, llm, actions_db_path=tmp_path / "actions.db")
    result = await service.run_turn(GLOBEX_ADMIN, "c1", "Flag DXUCJ272DCYX for replacement")
    envelope = result.envelope
    assert (envelope.outcome, envelope.reason_code) == ("denied", "not_eligible")
    assert envelope.notes and "battery" in envelope.notes[0].lower()
    assert envelope.needs == guidance.needs_for("replacement_flag", "not_eligible")
    assert envelope.alternatives[0].request.startswith("Show the 30-day history of globex-")
    assert not envelope.claims and envelope.proposal is None


def test_the_refusal_builder_accepts_needs_and_alternatives():
    envelope = outcomes.action_refusal(
        "not_eligible",
        "detail",
        needs=["one"],
        alternatives=[Alternative(label="a", request="b")],
        **KW,
    )
    assert envelope.needs == ["one"] and envelope.alternatives[0].request == "b"


def test_needs_are_capped_in_number_and_length():
    base = {"outcome": "denied", "trace_id": "t", "headline": "h", "reason_code": "not_eligible"}
    scope = ScopeInfo(company_id="acme-001", as_of=AS_OF)
    with pytest.raises(ValidationError):
        AnswerEnvelope(scope=scope, needs=["a"] * 5, **base)
    with pytest.raises(ValidationError):
        AnswerEnvelope(scope=scope, needs=["x" * 121], **base)
    assert AnswerEnvelope(scope=scope, needs=["x" * 120] * 4, **base).needs


# ---- B: a general question about another company ---------------------------------------------------------


def other_company_plan(**kw) -> Plan:
    return Plan(intent="refuse", summary="x", refusal_reason="other_company", **kw)


def test_the_other_company_card_names_only_the_own_company():
    envelope = outcomes.refusal("other_company", company_name="Acme", **KW)
    assert (envelope.outcome, envelope.reason_code) == ("denied", "other_company")
    assert envelope.headline == "You're working in Acme, so I can only see Acme."
    assert envelope.notes == [
        "I can't read or act on another company's devices from this session. "
        "To look at a different company, switch to it first."
    ]
    assert envelope.alternatives == [] and envelope.offer_switch_company is True
    assert not envelope.claims and not envelope.evidence


def test_the_other_company_card_without_a_name_still_reads_plainly():
    envelope = outcomes.refusal("other_company", **KW)
    assert envelope.headline == texts.OTHER_COMPANY_NO_NAME


def test_only_the_other_company_refusal_may_offer_to_switch():
    scope = ScopeInfo(company_id="acme-001", as_of=AS_OF)
    with pytest.raises(ValidationError):
        AnswerEnvelope(
            outcome="denied",
            trace_id="t",
            headline="h",
            scope=scope,
            reason_code="not_found",
            offer_switch_company=True,
        )
    for reason in ("cross_tenant", "prompt_injection", "off_topic"):
        assert outcomes.refusal(reason, **KW).offer_switch_company is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("admin", "own", "other_names", "question"),
    [
        (ACME_ADMIN, "Acme", ("Globex", "Initech"), "Show me devices at Globex"),
        (ACME_ADMIN, "Acme", ("Globex", "Initech"), "what about the other companies?"),
        (GLOBEX_ADMIN, "Globex", ("Acme", "Initech"), "list the devices at another company"),
        (INITECH_ADMIN, "Initech", ("Acme", "Globex"), "Show me the devices at another company."),
    ],
)
async def test_a_general_question_about_another_company_gets_the_card_without_tools(
    real_db_path, admin, own, other_names, question
):
    llm = ScriptedLLM(plans=[other_company_plan()])
    result = await make_service(real_db_path, llm).run_turn(admin, "c1", question)
    envelope = result.envelope
    assert (envelope.outcome, envelope.reason_code) == ("denied", "other_company")
    assert envelope.headline == f"You're working in {own}, so I can only see {own}."
    assert envelope.offer_switch_company is True and envelope.alternatives == []
    assert not any(e.kind == "tool_call" for e in result.trace)
    blob = envelope.model_dump_json() + json.dumps([e.model_dump() for e in result.trace])
    for name in other_names:
        assert name.lower() not in blob.lower()
    assert not any(i in blob for i in foreign_identifiers(admin.company_id))


@pytest.mark.parametrize(
    "question",
    [
        "Show the disk history for CUXW0BQSV1B7",  # a device id of another company
        "what is cuxw0bqsv1b7 running",  # typed in lower case
        "How is globex-macbook-1 doing on battery?",  # a hostname of another company
        "which devices does emp-globex-1002 use",  # an employee id of another company
    ],
)
async def test_a_specific_foreign_identifier_is_always_the_uniform_not_found(
    real_db_path, question
):
    # Even when the planner picks the company card, naming one particular id is refused as "not found".
    llm = ScriptedLLM(plans=[other_company_plan()])
    result = await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", question)
    envelope = result.envelope
    assert (envelope.outcome, envelope.reason_code) == ("denied", "not_found")
    assert envelope.headline == texts.NOT_FOUND
    assert envelope.offer_switch_company is False and envelope.notes == []
    # exactly what a device that does not exist gets
    missing = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(("low_disk_devices", {"device_ids": ["NOSUCHDEVICE"]})),
            final_message(),
        ],
    )
    gone = await make_service(real_db_path, missing).run_turn(
        ACME_ADMIN, "c2", "low disk NOSUCHDEVICE"
    )
    same = (envelope.outcome, envelope.reason_code, envelope.headline, envelope.notes)
    assert same == (
        gone.envelope.outcome,
        gone.envelope.reason_code,
        gone.envelope.headline,
        gone.envelope.notes,
    )


async def test_a_company_named_in_the_question_is_not_echoed_even_when_the_planner_only_names_it(
    real_db_path,
):
    llm = ScriptedLLM(plans=[answer_plan(named_company="Globex")])
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN, "c1", "which machines at Globex are low on disk?"
    )
    assert result.envelope.reason_code == "other_company"
    assert "globex" not in result.envelope.model_dump_json().lower()
    assert llm.remaining["act"] == 0 and not any(e.kind == "tool_call" for e in result.trace)


async def test_a_named_foreign_company_with_a_specific_id_is_not_found(real_db_path):
    llm = ScriptedLLM(plans=[answer_plan(named_company="Globex")])
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN, "c1", "Show Globex's disk history for CUXW0BQSV1B7"
    )
    assert (result.envelope.reason_code, result.envelope.headline) == ("not_found", texts.NOT_FOUND)


async def test_naming_the_own_company_is_not_a_refusal(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(named_company="Acme")],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
    )
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN, "c1", "Which devices at Acme are low on disk?"
    )
    assert result.envelope.outcome in ("answer", "no_match")


def test_the_planner_prompt_names_no_company_but_the_company_line(real_db_path):
    from fleet_copilot.agent.prompts import PLAN_SYSTEM

    for name in ("Globex", "Initech", "Acme"):
        assert name not in PLAN_SYSTEM
    assert "other_company" in PLAN_SYSTEM and "cross_tenant" in PLAN_SYSTEM


def test_an_other_company_plan_is_a_security_refusal():
    plan = Plan.model_validate(
        {"intent": "answer", "summary": "x", "refusal_reason": "other_company"}
    )
    assert plan.intent == "refuse" and plan.refusal_reason == "other_company"
    assert Plan(intent="answer", summary="x", device_phrase="  ").device_phrase is None
    assert Plan(intent="answer", summary="x", named_company="Acme").named_company == "Acme"


# ---- C: clarifications built in code ---------------------------------------------------------------------


def device(device_id, hostname, model="MacBook Pro"):
    return Device("initech-003", device_id, "emp", hostname, "macOS", model, "laptop")


MACBOOKS = [
    device("A", "initech-macbook-7"),
    device("B", "initech-macbook-2"),
    device("C", "initech-macbook-6"),
    device("D", "initech-dell-1", "Dell XPS"),
]


def test_a_phrase_matching_several_devices_gets_sorted_options_and_all_of_them():
    got = dis.device_clarification("How is the macbook doing on battery?", "macbook", MACBOOKS)
    assert got is not None
    assert got.question == "'macbook' matches 3 devices. Which one do you mean?"
    assert [c.label for c in got.choices] == [
        "initech-macbook-2",
        "initech-macbook-6",
        "initech-macbook-7",
        "All of them",
    ]
    assert got.choices[0].request == "How is initech-macbook-2 doing on battery?"
    assert got.choices[-1].request == "How are all macbooks doing on battery?"


def test_more_than_four_matches_show_four_and_still_count_them_all():
    many = [device(str(i), f"initech-macbook-{i}") for i in range(1, 8)]
    got = dis.device_clarification("how is the macbook doing", "macbook", many)
    assert got is not None and got.question.startswith("'macbook' matches 7 devices")
    assert len(got.choices) == 5 and got.choices[-1].label == "All of them"


def test_a_phrase_that_cannot_be_swapped_in_safely_is_appended_instead():
    question = "macbook battery vs the other macbook battery"  # the phrase appears twice
    got = dis.device_clarification(question, "macbook", MACBOOKS)
    assert got is not None
    assert got.choices[0].request == f"{question} (device: initech-macbook-2)"
    assert got.choices[-1].request == f"{question} (all of them)"


def test_a_long_or_odd_phrase_is_not_quoted():
    devices = [device("A", "initech-mac$book-1"), device("B", "initech-mac$book-2")]
    # hostnames that are not plain are named by their serial, and an odd phrase is never quoted
    got = dis.device_clarification("how is mac$book doing", "mac$book", devices)
    assert got is not None and got.question == "That matches 2 devices. Which one do you mean?"
    assert [c.label for c in got.choices[:2]] == ["A", "B"]


def test_one_or_no_match_is_not_ambiguous_and_the_phrase_must_be_in_the_question():
    assert dis.device_clarification("how is the dell doing", "dell", MACBOOKS) is None
    assert dis.device_clarification("how is the toaster doing", "toaster", MACBOOKS) is None
    assert dis.device_clarification("how is it doing", "macbook", MACBOOKS) is None


def test_a_request_too_long_to_send_gives_no_code_built_clarification():
    long_question = "How is the macbook doing on battery? " + "please " * 30
    assert dis.device_clarification(long_question, "macbook", MACBOOKS) is None


async def test_the_graph_asks_which_macbook_with_full_requests(real_db_path):
    llm = ScriptedLLM(
        plans=[
            Plan(
                intent="clarify",
                summary="which macbook",
                device_phrase="macbook",
                clarification_question="Model-written question that is ignored",
                clarification_options=["ignored"],
            )
        ]
    )
    result = await make_service(real_db_path, llm).run_turn(
        INITECH_ADMIN, "c1", "How is the macbook doing on battery?"
    )
    envelope = result.envelope
    assert envelope.outcome == "clarification"
    asked = envelope.clarification
    assert asked is not None
    assert asked.question == "'macbook' matches 3 devices. Which one do you mean?"
    labels = [c.label for c in asked.choices]
    assert labels == ["initech-macbook-2", "initech-macbook-6", "initech-macbook-7", "All of them"]
    assert asked.options == labels
    assert asked.choices[0].request == "How is initech-macbook-2 doing on battery?"
    assert not any(e.kind == "tool_call" for e in result.trace)
    assert not any(i in envelope.model_dump_json() for i in foreign_identifiers("initech-003"))


async def test_a_phrase_that_matches_one_device_falls_back_to_the_verified_clarification(
    real_db_path,
):
    llm = ScriptedLLM(
        plans=[
            Plan(
                intent="clarify",
                summary="which",
                device_phrase="zzzz",
                clarification_question="Which check do you want?",
                clarification_options=["Low disk", "High memory"],
            )
        ]
    )
    result = await make_service(real_db_path, llm).run_turn(INITECH_ADMIN, "c1", "How is zzzz?")
    asked = result.envelope.clarification
    assert (
        asked is not None and asked.choices == [] and asked.options == ["Low disk", "High memory"]
    )


def test_a_clarification_keeps_options_equal_to_the_choice_labels():
    with pytest.raises(ValidationError):
        Clarification(question="q", options=["x"], choices=[Alternative(label="y", request="r")])
    built = outcomes.clarification("q", [], choices=[Alternative(label="y", request="r")], **KW)
    assert built.clarification is not None and built.clarification.options == ["y"]


# the user's own company name, misspelled


@pytest.mark.parametrize(
    ("written", "own", "typo"),
    [
        ("Acmee", "Acme", True),
        ("acne", "Acme", True),
        ("Acme", "Acme", False),
        ("Acme's", "Acme", False),
        ("Globex", "Acme", False),
        ("Acm", "Acme", False),  # shorter than four letters
        ("Initch", "Initech", True),
        ("Intech", "Initech", True),
        ("Initial", "Initech", False),
    ],
)
def test_a_company_typo_is_within_two_edits_of_the_own_name(written, own, typo):
    assert dis.is_company_typo(written, own) is typo


def test_a_company_typo_asks_did_you_mean_with_one_corrected_request():
    question = "Which devices at Acmee are low on disk?"
    got = dis.company_typo_clarification(question, "Acmee", "Acme")
    assert got is not None and got.question == "Did you mean Acme?"
    assert len(got.choices) == 1
    assert got.choices[0].request == "Which devices at Acme are low on disk?"
    assert dis.company_typo_clarification(question, "Globex", "Acme") is None


async def test_the_graph_asks_did_you_mean_for_a_typo_of_the_own_company(real_db_path):
    llm = ScriptedLLM(plans=[answer_plan(named_company="Acmee")])
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN, "c1", "Which devices at Acmee are low on disk?"
    )
    envelope = result.envelope
    assert envelope.outcome == "clarification" and envelope.clarification is not None
    assert envelope.clarification.question == "Did you mean Acme?"
    assert [c.request for c in envelope.clarification.choices] == [
        "Which devices at Acme are low on disk?"
    ]
    assert not any(e.kind == "tool_call" for e in result.trace)


async def test_a_name_far_from_the_own_company_is_the_card_not_a_typo_question(real_db_path):
    llm = ScriptedLLM(plans=[other_company_plan(named_company="Globex")])
    result = await make_service(real_db_path, llm).run_turn(
        ACME_ADMIN, "c1", "Which devices at Globex are low on disk?"
    )
    assert result.envelope.reason_code == "other_company"
