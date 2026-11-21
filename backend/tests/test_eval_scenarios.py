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


def test_decisions_belong_on_the_last_turn(tmp_path):
    turns = [
        {"say": "a", "expect": {"decisions": GOOD_DECISIONS[:1]}},
        {"say": "b", "expect": {"outcome": "answer"}},
    ]
    write_suite(tmp_path, suite="actions", cases=[case(turns=turns)])
    with pytest.raises(ScenarioError, match="last turn"):
        load_suites(tmp_path)


def test_an_instance_without_decisions_has_none(tmp_path):
    write_suite(tmp_path, cases=[case()])
    (scenario,) = load_suites(tmp_path)
    assert expand(scenario.cases[0])[0].decisions == ()


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
