import json

import pytest

from fleet_copilot.evals.golden_ref import GoldenRefError, resolve, resolve_expect
from fleet_copilot.evals.scenarios import ScenarioError, expand, load_suites

GOLDEN = {
    "tenants": {
        "acme-001": {
            "low_disk_lt10": {"devices": ["B", "A"], "population": 10},
            "disk_trend_lt10": {"7": {"A": "sustained", "B": "onset", "C": "sustained"}},
            "compliance_trend": {
                "screen_lock": {
                    "30": {
                        "A": {"kind": "transition", "change_at": "2026-05-22T09:41:00Z"},
                        "B": {"kind": "persistent_failure", "change_at": None},
                    }
                }
            },
            "software": {
                "new_installs": {
                    "30": [{"device_id": "B", "app": "x"}, {"device_id": "A", "app": "y"}],
                    "7": [],
                }
            },
            "lists": {"rows": [{"id": "r0"}, {"id": "r1"}]},
        }
    }
}


def write_suite(tmp_path, suite="grounding", cases=None, name=None):
    path = tmp_path / f"{name or suite}.json"
    path.write_text(json.dumps({"suite": suite, "cases": cases}), encoding="utf-8")
    return path


def case(**overrides):
    base = {
        "id": "low_disk",
        "user": "admin-acme",
        "turns": [{"say": "Which devices are low on disk?", "expect": {"outcome": "answer"}}],
    }
    base.update(overrides)
    return base


# --- loading and expansion ---------------------------------------------------------------------------


def test_a_single_question_is_one_instance_with_a_stable_id(tmp_path):
    write_suite(tmp_path, cases=[case()])
    (scenario,) = load_suites(tmp_path)
    (instance,) = expand(scenario.cases[0])
    assert scenario.suite == "grounding"
    assert instance.id == "grounding.low_disk#1"
    assert instance.suite == "grounding" and instance.case_id == "low_disk"
    assert instance.user == "admin-acme"
    assert instance.paraphrased is False
    assert [t.say for t in instance.turns] == ["Which devices are low on disk?"]


def test_a_list_say_expands_into_one_instance_per_phrasing(tmp_path):
    phrasings = ["one?", "two?", "thr33?", "four?"]
    write_suite(
        tmp_path,
        cases=[
            case(
                turns=[
                    {"say": "first, plain", "expect": {"outcome": "answer"}},
                    {"say": phrasings, "expect": {"outcome": "answer", "population": 10}},
                ]
            )
        ],
    )
    (scenario,) = load_suites(tmp_path)
    instances = expand(scenario.cases[0])
    assert [i.id for i in instances] == [f"grounding.low_disk#{n}" for n in (1, 2, 3, 4)]
    assert [i.phrasing_index for i in instances] == [1, 2, 3, 4]
    assert all(i.paraphrased for i in instances)
    assert [i.turns[1].say for i in instances] == phrasings
    assert all(i.turns[0].say == "first, plain" for i in instances)
    assert all(i.turns[1].expect == {"outcome": "answer", "population": 10} for i in instances)
    # expanding again gives the same ids
    assert [i.id for i in expand(scenario.cases[0])] == [i.id for i in instances]


def test_two_list_turns_in_one_case_are_rejected_and_the_error_names_file_and_case(tmp_path):
    write_suite(
        tmp_path,
        cases=[case(turns=[{"say": ["a", "b"], "expect": {}}, {"say": ["c", "d"], "expect": {}}])],
    )
    with pytest.raises(ScenarioError) as error:
        load_suites(tmp_path)
    assert "grounding.json" in str(error.value)
    assert "low_disk" in str(error.value)
    assert "one" in str(error.value).lower()


def test_suites_load_in_a_stable_order_and_other_files_are_ignored(tmp_path):
    write_suite(tmp_path, suite="trends", cases=[case()])
    write_suite(tmp_path, suite="actions", cases=[case()])
    (tmp_path / "notes.txt").write_text("not a suite")
    assert [s.suite for s in load_suites(tmp_path)] == ["actions", "trends"]


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda c: c.update(user="nobody"), "user"),
        (lambda c: c.update(turns=[]), "turns"),
        (lambda c: c.update(id="Bad Id"), "id"),
        (lambda c: c["turns"][0].update(say=""), "say"),
        (lambda c: c["turns"][0].update(say=[]), "say"),
        (lambda c: c["turns"][0].update(say=["a", "a"]), "say"),
        (lambda c: c["turns"][0].update(expect={"outcom": "answer"}), "outcom"),
        (lambda c: c["turns"][0].update(expect={"outcome": "maybe"}), "outcome"),
        (lambda c: c["turns"][0].update(expect={"reason_code": "because"}), "reason_code"),
        (lambda c: c["turns"][0].update(expect={"devices": "A"}), "devices"),
        (lambda c: c["turns"][0].update(expect={"population": "ten"}), "population"),
        (lambda c: c["turns"][0].update(expect={"no_foreign_data": "yes"}), "no_foreign_data"),
        (lambda c: c["turns"][0].update(expect={"proposal": {"action_type": "nope"}}), "proposal"),
        (lambda c: c["turns"][0].update(expect={"devices": {"golden": "acme-001"}}), "golden"),
        (
            lambda c: c["turns"][0].update(
                expect={"devices": {"golden": "a/b", "keys": True, "keys_where": "x"}}
            ),
            "keys",
        ),
        (lambda c: c["turns"][0].update(expect={"devices": {"devices": ["A"]}}), "source"),
        (lambda c: c["turns"][0].update(expect={"alternatives_min": 0}), "alternatives_min"),
        (lambda c: c["turns"][0].update(expect={"alternatives_min": 5}), "alternatives_min"),
        (lambda c: c["turns"][0].update(expect={"alternatives_min": "2"}), "alternatives_min"),
        (lambda c: c["turns"][0].update(expect={"alternatives_min": True}), "alternatives_min"),
        (lambda c: c["turns"][0].update(expect={"needs_min": 0}), "needs_min"),
        (lambda c: c["turns"][0].update(expect={"needs_min": 5}), "needs_min"),
        (lambda c: c["turns"][0].update(expect={"needs_min": "1"}), "needs_min"),
        (lambda c: c["turns"][0].update(expect={"choices_min": 0}), "choices_min"),
        (lambda c: c["turns"][0].update(expect={"choices_min": 7}), "choices_min"),
        (lambda c: c["turns"][0].update(expect={"choices_min": True}), "choices_min"),
        (lambda c: c["turns"][0].update(expect={"insights_min": 0}), "insights_min"),
        (lambda c: c["turns"][0].update(expect={"insights_min": 11}), "insights_min"),
        (lambda c: c["turns"][0].update(expect={"insights_min": "3"}), "insights_min"),
        (lambda c: c["turns"][0].update(expect={"insights_min": True}), "insights_min"),
        (lambda c: c["turns"][0].update(expect={"suggestions_include": []}), "suggestions_include"),
        (
            lambda c: c["turns"][0].update(expect={"suggestions_include": "A"}),
            "suggestions_include",
        ),
        (
            lambda c: c["turns"][0].update(
                expect={"suggestions_include": [{"action_type": "nope", "device": "A"}]}
            ),
            "action_type",
        ),
        (
            lambda c: c["turns"][0].update(
                expect={"suggestions_include": [{"action_type": "upgrade_order"}]}
            ),
            "device",
        ),
        (
            lambda c: c["turns"][0].update(
                expect={
                    "suggestions_include": [
                        {"action_type": "upgrade_order", "device": "A", "extra": 1}
                    ]
                }
            ),
            "extra",
        ),
        (
            lambda c: c["turns"][0].update(
                expect={
                    "suggestions_include": [
                        {"action_type": "upgrade_order", "device": {"golden": "acme-001"}}
                    ]
                }
            ),
            "golden",
        ),
    ],
)
def test_malformed_cases_are_rejected_naming_the_file_and_the_case(tmp_path, mutation, needle):
    broken = case()
    mutation(broken)
    write_suite(tmp_path, cases=[broken])
    with pytest.raises(ScenarioError) as error:
        load_suites(tmp_path)
    message = str(error.value)
    assert "grounding.json" in message
    assert needle in message


@pytest.mark.parametrize(
    "expect, needle",
    [
        ({"notes_count": -1}, "notes_count"),
        ({"notes_count": True}, "notes_count"),
        ({"notes_count": "1"}, "notes_count"),
        ({"reply_excludes": "laptop"}, "reply_excludes"),
        ({"reply_excludes": []}, "reply_excludes"),
        ({"reply_excludes": [""]}, "reply_excludes"),
    ],
)
def test_the_notes_and_reply_excludes_keys_are_validated(tmp_path, expect, needle):
    write_suite(tmp_path, cases=[case(turns=[{"say": "hi", "expect": expect}])])
    with pytest.raises(ScenarioError, match=needle):
        load_suites(tmp_path)


def test_the_notes_and_reply_excludes_keys_load(tmp_path):
    expect = {"notes_count": 0, "reply_excludes": ["laptop"]}
    write_suite(tmp_path, cases=[case(turns=[{"say": "hi", "expect": expect}])])
    assert load_suites(tmp_path)


def test_duplicate_case_ids_in_a_suite_are_rejected(tmp_path):
    write_suite(tmp_path, cases=[case(), case()])
    with pytest.raises(ScenarioError, match="duplicate"):
        load_suites(tmp_path)


def test_invalid_json_and_a_bad_top_level_are_rejected(tmp_path):
    (tmp_path / "broken.json").write_text("{nope")
    with pytest.raises(ScenarioError, match="broken.json"):
        load_suites(tmp_path)
    (tmp_path / "broken.json").write_text(json.dumps({"suite": "", "cases": []}))
    with pytest.raises(ScenarioError, match="broken.json"):
        load_suites(tmp_path)


def test_a_missing_directory_is_a_scenario_error(tmp_path):
    with pytest.raises(ScenarioError, match="no such"):
        load_suites(tmp_path / "nope")


GOOD_DECISIONS = [
    {"do": "approve", "expect": {"status": "executed", "effects_delta": {"tickets": 1}}},
    {"do": "approve_again", "expect": {"status": "executed", "duplicate": True}},
    {"do": "approve_wrong_hash", "expect": {"error": "proposal changed"}},
    {"do": "reject", "expect": {"status": "rejected", "effects_delta": {}}},
    {"do": "approve_as", "user": "admin-globex", "expect": {"error": "not_found"}},
]


def action_case(decisions):
    return case(
        turns=[{"say": "Open a ticket", "expect": {"outcome": "proposal", "decisions": decisions}}]
    )


def test_well_formed_decisions_load_and_are_exposed_on_the_instance(tmp_path):
    write_suite(tmp_path, suite="actions", cases=[action_case(GOOD_DECISIONS)])
    (scenario,) = load_suites(tmp_path)
    (instance,) = expand(scenario.cases[0])
    assert [d["do"] for d in instance.decisions] == [d["do"] for d in GOOD_DECISIONS]


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        [{"do": "explode", "expect": {}}],
        [{"do": "approve", "expect": {"status": "flying"}}],
        [{"do": "approve", "expect": {"duplicate": "yes"}}],
        [{"do": "approve", "expect": {"effects_delta": {"tickets": "one"}}}],
        [{"do": "approve", "expect": {"surprise": 1}}],
        [{"do": "approve_as", "expect": {}}],
        [{"do": "approve_as", "user": "nobody", "expect": {}}],
        [{"do": "approve", "user": "admin-acme", "expect": {}}],
    ],
)
def test_malformed_decisions_are_rejected(tmp_path, decisions):
    write_suite(tmp_path, suite="actions", cases=[action_case(decisions)])
    with pytest.raises(ScenarioError, match="actions.json"):
        load_suites(tmp_path)


def test_decisions_may_be_declared_on_any_turn_and_run_after_that_turn(tmp_path):
    turns = [
        {
            "say": "Open a ticket",
            "expect": {
                "outcome": "proposal",
                "proposal": PROPOSAL,
                "decisions": GOOD_DECISIONS[:1],
            },
        },
        {"say": "b", "expect": {"outcome": "answer", "decisions": GOOD_DECISIONS[:1]}},
    ]
    write_suite(tmp_path, suite="actions", cases=[case(turns=turns)])
    (scenario,) = load_suites(tmp_path)
    (instance,) = expand(scenario.cases[0])
    assert [at for at, _ in instance.decision_steps] == [1, 2]
    assert len(instance.decisions) == 2


def test_an_instance_without_decisions_has_none(tmp_path):
    write_suite(tmp_path, cases=[case()])
    (scenario,) = load_suites(tmp_path)
    assert expand(scenario.cases[0])[0].decisions == ()


# --- decisions and checks that name an earlier turn ---------------------------------------------------

PROPOSAL = {"action_type": "remediation_ticket", "target": "1LYSSFD074BB", "status": "pending"}


def two_turn_case(decision=None, *, second=None, first=None):
    """Turn 1 proposes a ticket; turn 2 is a refusal that carries the decision (or `second`)."""
    first_expect = {"outcome": "proposal", "proposal": PROPOSAL} if first is None else first
    second_expect = (
        {"outcome": "unsupported", "decisions": [decision]} if second is None else second
    )
    return case(
        turns=[
            {"say": "Open a ticket", "expect": first_expect},
            {"say": "Approve it", "expect": second_expect},
        ]
    )


def test_a_decision_may_name_the_turn_whose_proposal_it_acts_on(tmp_path):
    step = {"do": "reject", "of_turn": 1, "expect": {"status": "rejected"}}
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(step)])
    (scenario,) = load_suites(tmp_path)
    (instance,) = expand(scenario.cases[0])
    assert instance.decisions[0]["of_turn"] == 1


def test_a_decision_may_name_the_current_turn_when_it_made_the_proposal(tmp_path):
    step = {"do": "reject", "of_turn": 1, "expect": {}}
    single = case(
        turns=[
            {
                "say": "Open a ticket",
                "expect": {"outcome": "proposal", "proposal": PROPOSAL, "decisions": [step]},
            }
        ]
    )
    write_suite(tmp_path, suite="actions", cases=[single])
    assert load_suites(tmp_path)


@pytest.mark.parametrize(
    "of_turn", [0, -1, 3, "1", True, 1.0, None], ids=repr
)  # 3 is a turn the case does not have
def test_of_turn_must_be_a_whole_number_naming_a_turn_of_the_case(tmp_path, of_turn):
    step = {"do": "reject", "of_turn": of_turn, "expect": {}}
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(step)])
    with pytest.raises(ScenarioError, match="of_turn"):
        load_suites(tmp_path)


def test_of_turn_must_name_a_turn_that_expects_a_proposal(tmp_path):
    step = {"do": "reject", "of_turn": 1, "expect": {}}
    write_suite(
        tmp_path,
        suite="guardrails",
        cases=[two_turn_case(step, first={"outcome": "answer"})],
    )
    with pytest.raises(ScenarioError, match="of_turn.*proposal"):
        load_suites(tmp_path)


def test_proposal_status_of_turn_loads_and_no_effects_is_a_switch(tmp_path):
    second = {
        "outcome": "unsupported",
        "proposal_status_of_turn": {"turn": 1, "status": "pending"},
        "no_effects": True,
    }
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(second=second)])
    assert load_suites(tmp_path)


@pytest.mark.parametrize(
    "check",
    [
        {"turn": 1},
        {"status": "pending"},
        {"turn": 1, "status": "flying"},
        {"turn": 0, "status": "pending"},
        {"turn": 3, "status": "pending"},  # later than the turn that asks
        {"turn": "1", "status": "pending"},
        {"turn": 1, "status": "pending", "extra": 1},
        "pending",
    ],
)
def test_malformed_proposal_status_of_turn_is_rejected(tmp_path, check):
    second = {"outcome": "unsupported", "proposal_status_of_turn": check}
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(second=second)])
    with pytest.raises(ScenarioError, match="proposal_status_of_turn"):
        load_suites(tmp_path)


def test_proposal_status_of_turn_must_name_a_turn_that_expects_a_proposal(tmp_path):
    second = {"proposal_status_of_turn": {"turn": 2, "status": "pending"}}
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(second=second)])
    with pytest.raises(ScenarioError, match="proposal_status_of_turn.*proposal"):
        load_suites(tmp_path)


def test_no_effects_must_be_true_or_false(tmp_path):
    second = {"outcome": "unsupported", "no_effects": "yes"}
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(second=second)])
    with pytest.raises(ScenarioError, match="no_effects"):
        load_suites(tmp_path)


# --- trajectory keys ---------------------------------------------------------------------------------


def test_trajectory_keys_load_with_real_tool_names_and_the_no_tool_marker(tmp_path):
    first = {
        "outcome": "answer",
        "tools_planned": ["low_disk_devices"],
        "tools_called": ["low_disk_devices", "trend"],
    }
    second = {"outcome": "unsupported", "tools_not_called": "*"}
    write_suite(tmp_path, suite="guardrails", cases=[two_turn_case(first=first, second=second)])
    assert load_suites(tmp_path)


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("tools_called", ["no_such_tool"], "unknown tool"),
        ("tools_not_called", ["low_disk_devices", "nope"], "unknown tool"),
        ("tools_called", [], "non-empty list"),
        ("tools_called", "low_disk_devices", "non-empty list"),
        (
            "tools_called",
            "*",
            "non-empty list",
        ),  # the marker only means something for tools_not_called
        ("tools_not_called", "all", "non-empty list"),
        ("tools_planned", ["no_such_tool"], "unknown tool"),
        ("tools_planned", ["recommend_actions"], "unknown tool"),
        ("tools_planned", [], "non-empty list"),
        ("tools_planned", "*", "non-empty list"),
        ("tools_planned", ["trend", "trend"], "twice"),
        ("tools_not_called", ["trend", "trend"], "twice"),
        ("tools_called", [1], "non-empty list"),
    ],
)
def test_malformed_trajectory_keys_are_rejected(tmp_path, key, value, needle):
    write_suite(
        tmp_path,
        suite="guardrails",
        cases=[two_turn_case(second={"outcome": "unsupported", key: value})],
    )
    with pytest.raises(ScenarioError, match=f"{key}.*{needle}"):
        load_suites(tmp_path)


def test_trajectory_names_are_checked_against_the_live_tool_list(tmp_path, monkeypatch):
    from fleet_copilot.evals import scenarios

    class Named:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(scenarios, "TOOLS", [Named("brand_new_tool")])
    write_suite(
        tmp_path,
        suite="guardrails",
        cases=[two_turn_case(second={"outcome": "answer", "tools_called": ["brand_new_tool"]})],
    )
    assert load_suites(tmp_path)
    write_suite(
        tmp_path,
        suite="guardrails",
        cases=[two_turn_case(second={"outcome": "answer", "tools_called": ["trend"]})],
    )
    with pytest.raises(ScenarioError, match="unknown tool"):
        load_suites(tmp_path)


# --- golden references -------------------------------------------------------------------------------


def test_a_path_reference_returns_the_value_at_the_path():
    assert resolve({"golden": "acme-001/low_disk_lt10/devices"}, GOLDEN) == ["B", "A"]
    assert resolve({"golden": "acme-001/low_disk_lt10/population"}, GOLDEN) == 10


def test_a_path_may_run_through_numeric_dict_keys_and_list_indexes():
    assert resolve({"golden": "acme-001/disk_trend_lt10/7/B"}, GOLDEN) == "onset"
    assert resolve({"golden": "acme-001/lists/rows/1/id"}, GOLDEN) == "r1"


def test_keys_where_matches_plain_values():
    ref = {"golden": "acme-001/disk_trend_lt10/7", "keys_where": "sustained"}
    assert resolve(ref, GOLDEN) == ["A", "C"]


def test_keys_where_matches_the_kind_of_object_values():
    ref = {"golden": "acme-001/compliance_trend/screen_lock/30", "keys_where": "transition"}
    assert resolve(ref, GOLDEN) == ["A"]


def test_keys_returns_every_key():
    assert resolve({"golden": "acme-001/disk_trend_lt10/7", "keys": True}, GOLDEN) == [
        "A",
        "B",
        "C",
    ]


def test_pluck_takes_a_field_from_each_item_of_a_list():
    ref = {"golden": "acme-001/software/new_installs/30", "pluck": "device_id"}
    assert resolve(ref, GOLDEN) == ["A", "B"]
    assert (
        resolve({"golden": "acme-001/software/new_installs/7", "pluck": "device_id"}, GOLDEN) == []
    )


def test_a_literal_with_a_source_note_returns_its_devices():
    ref = {"devices": ["X", "Y"], "source": "battery run from the raw NDJSON"}
    assert resolve(ref, GOLDEN) == ["X", "Y"]


@pytest.mark.parametrize(
    "ref",
    [
        {"golden": "acme-001/nope/devices"},
        {"golden": "nobody-009/low_disk_lt10/devices"},
        {"golden": "acme-001/low_disk_lt10/devices", "keys": True},  # not a dict
        {"golden": "acme-001/low_disk_lt10/population", "keys_where": "x"},
        {"golden": "acme-001/low_disk_lt10/devices", "pluck": "device_id"},  # items not dicts
        {"golden": "acme-001/lists/rows/9"},
        {"golden": "acme-001"},
        {"devices": ["X"]},
        {"something": "else"},
    ],
)
def test_references_that_do_not_resolve_raise_a_readable_error(ref):
    with pytest.raises(GoldenRefError):
        resolve(ref, GOLDEN)


def test_resolve_expect_replaces_every_reference_and_leaves_other_values_alone():
    expect = {
        "outcome": "answer",
        "devices": {"golden": "acme-001/low_disk_lt10/devices"},
        "devices_exclude": ["Z"],
        "population": {"golden": "acme-001/low_disk_lt10/population"},
        "proposal": {
            "action_type": "remediation_ticket",
            "target": {"golden": "acme-001/disk_trend_lt10/7", "keys_where": "onset"},
            "status": "pending",
        },
        "cites_every_device": True,
    }
    resolved = resolve_expect(expect, GOLDEN)
    assert resolved["devices"] == ["B", "A"]
    assert resolved["population"] == 10
    assert resolved["devices_exclude"] == ["Z"]
    assert resolved["proposal"]["target"] == "B"
    assert resolved["cites_every_device"] is True
    assert resolved["outcome"] == "answer"
    assert expect["devices"] == {"golden": "acme-001/low_disk_lt10/devices"}  # input untouched


def test_resolve_expect_expands_a_suggestion_device_reference_into_one_pair_per_device():
    expect = {
        "suggestions_include": [
            {
                "action_type": "upgrade_order",
                "device": {"golden": "acme-001/disk_trend_lt10/7", "keys_where": "sustained"},
            },
            {"action_type": "replacement_flag", "device": "Z"},
        ]
    }
    resolved = resolve_expect(expect, GOLDEN)
    assert resolved["suggestions_include"] == [
        {"action_type": "upgrade_order", "device": "A"},
        {"action_type": "upgrade_order", "device": "C"},
        {"action_type": "replacement_flag", "device": "Z"},
    ]
    assert isinstance(expect["suggestions_include"][0]["device"], dict)  # input untouched


def test_a_proposal_target_reference_must_resolve_to_exactly_one_device():
    expect = {
        "proposal": {
            "action_type": "remediation_ticket",
            "target": {"golden": "acme-001/disk_trend_lt10/7", "keys_where": "sustained"},
        }
    }
    with pytest.raises(GoldenRefError, match="one"):
        resolve_expect(expect, GOLDEN)


def test_alternatives_min_is_a_valid_expect_key(tmp_path):
    ok = case(turns=[{"say": "q", "expect": {"outcome": "unsupported", "alternatives_min": 3}}])
    write_suite(tmp_path, cases=[ok])
    (scenario,) = load_suites(tmp_path)
    assert scenario.cases[0].turns[0].expect["alternatives_min"] == 3


def test_needs_min_and_choices_min_are_valid_expect_keys(tmp_path):
    ok = case(
        turns=[
            {"say": "q", "expect": {"outcome": "denied", "needs_min": 1}},
            {"say": "r", "expect": {"outcome": "clarification", "choices_min": 3}},
        ]
    )
    write_suite(tmp_path, cases=[ok])
    (scenario,) = load_suites(tmp_path)
    assert scenario.cases[0].turns[0].expect["needs_min"] == 1
    assert scenario.cases[0].turns[1].expect["choices_min"] == 3


def test_insights_min_is_a_valid_expect_key(tmp_path):
    ok = case(turns=[{"say": "q", "expect": {"outcome": "answer", "insights_min": 3}}])
    write_suite(tmp_path, cases=[ok])
    (scenario,) = load_suites(tmp_path)
    assert scenario.cases[0].turns[0].expect["insights_min"] == 3


# --- reply keys ----------------------------------------------------------------------------------------


def test_reply_keys_load(tmp_path):
    expect = {
        "outcome": "answer",
        "reply_grounded": True,
        "no_confident_claim": True,
        "reply_mentions": ["acme-macbook-4", "acme-thinkpad-8"],
    }
    write_suite(tmp_path, suite="conversation", cases=[two_turn_case(second=expect)])
    assert load_suites(tmp_path)


def test_reply_mentions_accepts_golden_references_next_to_hostnames(tmp_path):
    expect = {
        "outcome": "answer",
        "reply_mentions": [{"golden": "acme-001/low_disk_lt10/lowest_hostnames"}, "acme-mac-5"],
    }
    write_suite(tmp_path, suite="conversation", cases=[two_turn_case(second=expect)])
    assert load_suites(tmp_path)


def test_a_malformed_reply_mentions_reference_is_rejected(tmp_path):
    expect = {"outcome": "answer", "reply_mentions": [{"golden": "no-slash"}]}
    write_suite(tmp_path, suite="conversation", cases=[two_turn_case(second=expect)])
    with pytest.raises(ScenarioError, match="reply_mentions.*golden"):
        load_suites(tmp_path)


def test_resolve_expect_turns_reply_mentions_references_into_hostnames():
    golden = {"tenants": {"acme-001": {"low": {"lowest": ["host-1"]}}}}
    resolved = resolve_expect(
        {"reply_mentions": [{"golden": "acme-001/low/lowest"}, "host-2", "host-1"]}, golden
    )
    assert resolved["reply_mentions"] == ["host-1", "host-2"]


def test_repeat_is_a_switch(tmp_path):
    write_suite(
        tmp_path,
        suite="conversation",
        cases=[two_turn_case(second={"outcome": "answer", "repeat": True})],
    )
    assert load_suites(tmp_path)
    write_suite(
        tmp_path,
        suite="conversation",
        cases=[two_turn_case(second={"outcome": "answer", "repeat": "yes"})],
    )
    with pytest.raises(ScenarioError, match="repeat.*true or false"):
        load_suites(tmp_path)


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("reply_mentions", [], "non-empty list of hostnames"),
        ("reply_mentions", "acme-macbook-4", "non-empty list of hostnames"),
        ("reply_mentions", ["acme-macbook-4", ""], "hostnames"),
        ("reply_mentions", [3], "hostnames"),
        ("reply_mentions", ["acme-macbook-4", "ACME-macbook-4"], "twice"),
        ("reply_grounded", "yes", "true or false"),
        ("no_confident_claim", 1, "true or false"),
    ],
)
def test_malformed_reply_keys_are_rejected(tmp_path, key, value, needle):
    write_suite(
        tmp_path,
        suite="conversation",
        cases=[two_turn_case(second={"outcome": "answer", key: value})],
    )
    with pytest.raises(ScenarioError, match=f"{key}.*{needle}"):
        load_suites(tmp_path)
