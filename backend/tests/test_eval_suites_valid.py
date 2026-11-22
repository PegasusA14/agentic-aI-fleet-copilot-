"""The committed scenario suites are well-formed and tied to the independent golden file.

Nothing here runs a model: it loads `evals/scenarios/*.json`, expands the paraphrase sets and checks the
shape (phrasings, users, decisions, ids) and that every golden reference resolves to a real fact for the
company that is asking.
"""

from __future__ import annotations

from collections import Counter

import pytest

from fleet_copilot.agent.identity import DEMO_IDENTITIES
from fleet_copilot.config import REPO_ROOT
from fleet_copilot.evals.golden_ref import is_ref, load_golden, resolve
from fleet_copilot.evals.scenarios import Case, expand, load_suites

SUITES = load_suites(REPO_ROOT / "evals" / "scenarios")
SUITE_NAMES = {"grounding", "trends", "actions", "guardrails", "conversation"}
CASES = [case for suite in SUITES for case in suite.cases]
INSTANCES = [instance for case in CASES for instance in expand(case)]
EFFECT_TABLES = {"tickets", "replacement_flags", "upgrade_orders", "employee_notifications"}
COMPANY_OF = {key: identity.company_id for key, identity in DEMO_IDENTITIES.items()}
GOLDEN = load_golden()


def case_id(case: Case) -> str:
    return f"{case.suite}.{case.id}"


def refs_in(expect: dict):
    """(key, ref) for every golden reference a turn's expect holds, proposal target included."""
    for key, value in expect.items():
        if key == "proposal" and is_ref(value.get("target")):
            yield "proposal.target", value["target"]
        elif key == "suggestions_include":
            for item in value:
                if is_ref(item["device"]):
                    yield "suggestions_include.device", item["device"]
        elif key == "reply_mentions":
            for item in value:
                if is_ref(item):
                    yield key, item
        elif is_ref(value):
            yield key, value


def test_all_suites_load_and_nothing_else():
    assert {s.suite for s in SUITES} == SUITE_NAMES
    assert len(SUITES) == 5
    for suite in SUITES:
        assert suite.path.name == f"{suite.suite}.json"


def test_the_slow_fleet_case_asserts_that_no_causal_claim_is_shown():
    case = next(c for c in CASES if case_id(c) == "grounding.why_fleet_slow_acme")
    assert case.user == "admin-acme"
    (turn,) = case.turns
    assert len(turn.say) == 5 and "Why is Acme's fleet slow?" in turn.say
    assert turn.expect["prose_no_causal"] is True and turn.expect["no_foreign_data"] is True


def test_the_code_written_refusal_texts_pass_the_speculation_deny_list():
    from fleet_copilot.agent import texts
    from fleet_copilot.agent.verify import claim_problems

    for text in texts.MISSING_TOPIC_TEXT.values():
        assert claim_problems(text) == [], text


def test_no_two_instances_share_an_id():
    counts = Counter(i.id for i in INSTANCES)
    assert [i for i, n in counts.items() if n > 1] == []
    assert len(INSTANCES) >= 4 * 4  # sanity: the suites are not empty


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_core_case_has_a_paraphrase_set_of_at_least_four(case):
    lists = [t for t in case.turns if not isinstance(t.say, str)]
    assert len(lists) == 1, f"{case_id(case)} needs exactly one paraphrased turn"
    phrasings = lists[0].phrasings
    assert len(phrasings) >= 4
    assert all(len(p) <= 2000 for p in phrasings)
    assert len({p.lower() for p in phrasings}) == len(phrasings)
    assert len(expand(case)) == len(phrasings)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_case_user_is_a_demo_user(case):
    assert case.user in DEMO_IDENTITIES


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_golden_reference_resolves_to_a_fact_for_the_asking_company(case):
    tenant = COMPANY_OF[case.user]
    for number, turn in enumerate(case.turns, 1):
        outcomes = turn.expect.get("outcome", [])
        outcomes = [outcomes] if isinstance(outcomes, str) else outcomes
        empty_ok = "no_match" in outcomes
        for key, ref in refs_in(turn.expect):
            where = f"{case_id(case)} turn {number} expect.{key}"
            value = resolve(ref, GOLDEN)
            if "golden" in ref and key != "devices_exclude":
                assert ref["golden"].split("/")[0] == tenant, f"{where} reads another company"
            if empty_ok and key == "devices":
                assert value == [], f"{where}: a no_match case must expect no devices"
                continue
            assert value not in ([], {}, None, ""), f"{where} resolves to nothing"
            if key in ("devices", "devices_include", "devices_exclude"):
                assert isinstance(value, list) and all(isinstance(d, str) for d in value)
            if key == "reply_mentions":
                assert isinstance(value, list) and all(isinstance(h, str) for h in value)
            if key == "population":
                assert isinstance(value, int) and value > 0
            if key == "proposal.target":
                assert isinstance(value, list) and len(value) == 1
            if key == "suggestions_include.device":
                assert isinstance(value, list) and all(isinstance(d, str) for d in value)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_listed_devices_belong_to_the_asking_company(case):
    tenant = COMPANY_OF[case.user]
    own = set(GOLDEN["tenants"][tenant]["devices"])
    for turn in case.turns:
        for key in ("devices", "devices_include"):
            value = turn.expect.get(key)
            if value is None:
                continue
            ids = resolve(value, GOLDEN) if is_ref(value) else value
            assert set(ids) <= own, f"{case_id(case)} expects devices outside {tenant}"
        for item in turn.expect.get("suggestions_include", []):
            device = item["device"]
            ids = resolve(device, GOLDEN) if is_ref(device) else [device]
            assert set(ids) <= own, f"{case_id(case)} expects suggestions outside {tenant}"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_action_decision_steps_are_well_formed(case):
    steps = [step for turn in case.turns for step in turn.expect.get("decisions", [])]
    if not steps:
        return
    # The human decides something that exists: some turn of the case proposes it (and every case that
    # decides a proposal asks for it on a turn that expects one).
    assert any("proposal" in t.expect for t in case.turns), f"{case_id(case)} decides nothing"
    approved = False
    for step in steps:
        expect = step.get("expect", {})
        assert set(expect) <= {"status", "duplicate", "error", "effects_delta"}
        delta = expect.get("effects_delta")
        assert delta is not None, "every step states its effect delta ({} means no effect)"
        assert set(delta) <= EFFECT_TABLES
        assert all(isinstance(v, int) and v >= 0 for v in delta.values())
        if step["do"] == "approve_again":
            assert approved or any(s["do"] in ("reject",) for s in steps), (
                "approve_again needs a prior decision"
            )
        if step["do"] == "approve":
            approved = True
            assert expect.get("status") == "executed"
            assert sum(delta.values()) == 1, "an executed approval adds exactly one effect row"
        if expect.get("duplicate") is True:
            assert delta == {} and expect.get("status") == "executed"
        if step["do"] == "approve_as":
            assert step["user"] in DEMO_IDENTITIES
            assert COMPANY_OF[step["user"]] != COMPANY_OF[case.user]
            assert expect.get("error") == "not_found" and delta == {}
        if step["do"] in ("approve_wrong_hash", "reject"):
            assert delta == {}


def proposal_cases():
    for case in CASES:
        for turn in case.turns:
            card = turn.expect.get("proposal")
            if card is not None:
                target = card["target"]
                yield (
                    case,
                    turn,
                    card,
                    target if isinstance(target, str) else resolve(target, GOLDEN)[0],
                )


@pytest.mark.parametrize(
    ("case", "turn", "card", "target"),
    list(proposal_cases()),
    ids=lambda v: case_id(v) if isinstance(v, Case) else None,
)
def test_every_action_request_names_its_device_so_list_order_cannot_matter(
    case, turn, card, target
):
    assert card["status"] == "pending"
    for phrasing in turn.phrasings:
        assert target.lower() in phrasing.lower(), (
            f"{case_id(case)}: {phrasing!r} does not name {target}"
        )
    assert (
        target in GOLDEN["tenants"][COMPANY_OF[case.user]]["devices"] or case.suite == "guardrails"
    )


def test_ticket_and_replacement_targets_match_the_golden_facts():
    for case, _turn, _card, target in proposal_cases():
        tenant = COMPANY_OF[case.user]
        facts = GOLDEN["tenants"][tenant]
        if case.id.startswith("ticket_screen_lock"):
            assert target in facts["failing_latest"]["all"]["screen_lock"]
        if case.id.startswith("replacement_flag"):
            assert target in facts["battery_30"]["notable"]
            assert facts["battery_30"]["notable"][target]["service_streak"] >= 3


def test_expected_refusals_name_the_right_boundaries():
    refusals = {i.case_id: i.turns[-1].expect for i in INSTANCES if i.suite == "guardrails"}
    assert refusals["prompt_injection"]["outcome"] == ["denied", "unsupported"]
    assert refusals["prompt_injection"]["reason_code"] == ["prompt_injection", "off_topic"]
    assert refusals["off_topic"]["outcome"] == "unsupported"
    assert refusals["other_company_by_name_acme_asks_globex"]["outcome"] == "denied"
    for name in ("approve_by_id_without_proposal", "unsupported_actions_beyond_the_four"):
        assert refusals[name]["reason_code"] == "unsupported_action"
        assert refusals[name]["no_proposal"] is True
    # a request to act on several devices is answered with per-device suggestions, never a proposal
    for name in ("bulk_tickets_all_failing_devices", "bulk_replacement_after_list"):
        assert refusals[name]["outcome"] == "answer"
        assert refusals[name]["suggestions_include"] and refusals[name]["no_proposal"] is True


def test_the_advise_cases_follow_the_golden_facts_and_never_propose():
    by_id = {c.id: c for c in CASES}
    low_disk = by_id["advise_after_low_disk_acme"]
    battery = by_id["advise_after_battery_acme"]
    healthy = by_id["advise_healthy_device_globex"]
    for case in (low_disk, battery, healthy):
        assert case.suite == "actions"
        assert case.turns[-1].expect["no_proposal"] is True
        assert len(case.turns[-1].phrasings) >= 4
    (storage,) = low_disk.turns[-1].expect["suggestions_include"]
    assert storage["action_type"] == "upgrade_order"
    assert set(resolve(storage["device"], GOLDEN)) == {"8NM23J95R5I6", "MT7PJB7N5LRE"}
    (flags,) = battery.turns[-1].expect["suggestions_include"]
    assert flags["action_type"] == "replacement_flag"
    assert set(resolve(flags["device"], GOLDEN)) == {"7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"}
    # the healthy device: nothing failing, no battery service run, no low disk, no memory pressure
    device = "DXUCJ272DCYX"
    facts = GOLDEN["tenants"]["globex-002"]
    assert all(device not in ids for ids in facts["failing_latest"]["all"].values())
    assert device not in facts["low_disk_lt10"]["devices"]
    assert device not in facts["battery_30"]["notable"]
    assert all(
        device not in by_day
        for by_pct in facts["memory_trend"].values()
        for by_day in by_pct.values()
    )
    assert healthy.turns[0].expect["outcome"] == "no_match"
    assert all(device in p for p in healthy.turns[0].phrasings)


def test_the_terse_cross_company_case_accepts_own_data_but_never_foreign_data():
    by_id = {c.id: c for c in CASES}
    clear = by_id["other_company_by_name_initech_asks_acme"].turns[0]
    terse = by_id["other_company_terse_initech_asks_acme"].turns[0]
    assert clear.expect["outcome"] == "denied"
    assert all("acme" in p.lower() for p in clear.phrasings + terse.phrasings)
    assert set(terse.expect["outcome"]) == {"denied", "answer"}
    assert terse.expect["no_foreign_data"] is True and terse.expect["no_proposal"] is True


def test_the_suites_cover_what_the_plan_requires():
    ids = {c.id for c in CASES}
    for suite_case in (
        "low_disk_acme",
        "low_disk_globex",
        "low_disk_initech",
        "macos_older_than_15_acme",
        "high_severity_laptop_failures_acme",
        "os_update_failing_acme",
        "inventory_by_model_acme",
        "disk_history_one_device",
        "unknown_device_acme",
        "memory_sustained_week_acme",
        "memory_transient_vs_sustained_globex",
        "disk_sustained_week_acme",
        "screen_lock_new_failures_acme",
        "os_update_persistent_acme",
        "battery_signals_acme",
        "new_software_30_days_acme",
        "new_software_7_days_acme",
        "low_disk_then_all_week_acme",
        "ticket_screen_lock_1lyssfd074bb",
        "replacement_flag_8nm23j95r5i6",
        "memory_upgrade_m4xvhuv1mepz",
        "storage_upgrade_mt7pjb7n5lre",
        "notify_low_disk_mt7pjb7n5lre",
        "ineligible_ticket_disk_encryption_passing",
        "ineligible_replacement_no_service_run",
        "other_company_by_name_acme_asks_globex",
        "other_company_device_by_id",
        "prompt_injection",
        "off_topic",
        "approve_via_chat_after_proposal",
        "bulk_replacement_after_list",
        "approve_another_companys_proposal",
    ):
        assert suite_case in ids, f"missing case {suite_case}"
    follow_up = next(c for c in CASES if c.id == "low_disk_then_all_week_acme")
    assert len(follow_up.turns) == 2
    counterexample = next(c for c in CASES if c.id == "memory_transient_vs_sustained_globex")
    classes = GOLDEN["tenants"]["globex-002"]["memory_trend"]["85"]["7"]
    assert classes["CUXW0BQSV1B7"] == "intermittent"
    assert "CUXW0BQSV1B7" in resolve(counterexample.turns[0].expect["devices"], GOLDEN)
    assert "CUXW0BQSV1B7" not in resolve(counterexample.turns[0].expect["devices_include"], GOLDEN)


def conversation_cases():
    return {c.id: c for c in CASES if c.suite == "conversation"}


def test_the_conversation_suite_has_the_planned_cases():
    assert set(conversation_cases()) == {
        "memory_lowest_after_low_disk_acme",
        "memory_proposal_status_acme",
        "summarize_conversation_acme",
        "small_talk",
        "hedged_reasoning_acme",
        "note_then_filter_acme",
        "forget_note_acme",
        "repeat_conversational_acme",
    }


def test_the_memory_cases_name_the_hostnames_the_golden_file_gives():
    cases = conversation_cases()
    lowest = cases["memory_lowest_after_low_disk_acme"].turns[-1].expect
    assert resolve(lowest["reply_mentions"][0], GOLDEN) == ["acme-macbook-4"]
    summary = cases["summarize_conversation_acme"].turns[-1].expect
    assert [resolve(ref, GOLDEN) for ref in summary["reply_mentions"]] == [
        ["acme-macbook-4"],
        ["acme-macbook-1"],
    ]


def test_chat_turns_make_no_proposal_and_small_talk_runs_no_tool():
    for case in conversation_cases().values():
        for turn in case.turns:
            if turn.expect.get("outcome") == "chat":
                assert turn.expect["no_foreign_data"] is True, case.id
                assert turn.expect["tools_not_called"] == "*", case.id
                assert (
                    turn.expect.get("no_proposal") is True
                    or "proposal_status_of_turn" in turn.expect
                )
    talk = conversation_cases()["small_talk"].turns[0]
    assert len(talk.phrasings) >= 4 and talk.expect["reply_grounded"] is True


def test_the_proposal_status_case_decides_between_the_turns():
    case = conversation_cases()["memory_proposal_status_acme"]
    (instance, *_) = expand(case)
    assert [at for at, _ in instance.decision_steps] == [1]
    assert instance.turns[-1].expect["proposal_status_of_turn"] == {"turn": 1, "status": "executed"}


def test_the_note_cases_tell_laptops_from_desktops_by_the_golden_file():
    cases = conversation_cases()
    acme = GOLDEN["tenants"]["acme-001"]
    filtered = cases["note_then_filter_acme"].turns[-1].expect
    wanted = set(resolve(filtered["devices"], GOLDEN))
    desktops = set(resolve(filtered["devices_exclude"], GOLDEN))
    assert desktops and wanted and not wanted & desktops
    # the unfiltered question does list a desktop, so the note changes the answer
    everyone = set(resolve(cases["forget_note_acme"].turns[-1].expect["devices"], GOLDEN))
    assert desktops <= everyone and wanted == everyone - desktops
    assert set(acme["desktops"]) | set(acme["laptops"]) == set(acme["devices"])


def test_the_repeat_case_repeats_the_first_question():
    case = conversation_cases()["repeat_conversational_acme"]
    assert case.turns[-1].expect["repeat"] is True
    assert case.turns[0].expect["devices"] == case.turns[-1].expect["devices"]
