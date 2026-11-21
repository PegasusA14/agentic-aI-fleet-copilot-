"""Software questions end to end through the service, with a scripted model and the real dataset."""

import json

import pytest
from graph_support import (
    ACME_ADMIN,
    GLOBEX_ADMIN,
    INITECH_ADMIN,
    answer_plan,
    assert_clean,
    explanation,
    follow_up_plan,
    golden,
    kinds,
    listed_ids,
    make_service,
)
from langchain_core.messages import ToolMessage

from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message

IDENTITIES = {"acme-001": ACME_ADMIN, "globex-002": GLOBEX_ADMIN, "initech-003": INITECH_ADMIN}
NEW = ("software_inventory", {"view": "new_installs"})
QUESTION = "Has any new software been installed recently?"


def one_call(tool, args):
    return [tool_calls_message((tool, args)), final_message()]


def installs(tenant):
    return golden(tenant)["software"]["new_installs"]["30"]


def blob_of(result):
    return result.envelope.model_dump_json() + json.dumps([e.model_dump() for e in result.trace])


@pytest.mark.parametrize("tenant", IDENTITIES)
async def test_each_company_sees_only_its_own_new_installs(real_db_path, tenant):
    llm = ScriptedLLM(plans=[answer_plan()], acts=one_call(*NEW), explains=[explanation()])
    result = await make_service(real_db_path, llm).run_turn(IDENTITIES[tenant], "c1", QUESTION)
    assert_clean(result, llm)
    envelope = result.envelope
    wanted = installs(tenant)
    assert envelope.outcome == "answer"
    assert {d for c in envelope.claims for d in c.device_ids} == {i["device_id"] for i in wanted}
    assert set(listed_ids(envelope)) == {i["device_id"] for i in wanted}
    shown = " ".join(c.text for c in envelope.claims)
    for item in wanted:
        assert f"{item['app']} {item['version']}; first seen {item['first_seen'][:10]}" in shown
    assert all(e.ref.startswith(f"{tenant}/") for e in envelope.evidence)
    assert envelope.coverage.population == len(golden(tenant)["devices"])
    assert "common set" in envelope.scope.rule
    # nothing of any other company, and no label the telemetry does not carry
    blob = blob_of(result)
    for other in IDENTITIES:
        if other != tenant:
            assert not any(d in blob for d in golden(other)["devices"])
    apps_of_others = {
        i["app"] for other in IDENTITIES if other != tenant for i in installs(other)
    } - {i["app"] for i in wanted}
    assert not any(app in blob for app in apps_of_others)
    assert not any(word in blob.lower() for word in ("malicious", "unapproved", "risky"))


async def test_a_short_window_with_no_install_is_a_no_match_without_a_model_explanation(
    real_db_path,
):
    llm = ScriptedLLM(
        plans=[answer_plan()], acts=one_call("software_inventory", {**NEW[1], "window_days": 7})
    )
    result = await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", QUESTION)
    assert_clean(result, llm)
    assert (result.envelope.outcome, result.envelope.reason_code) == (
        "no_match",
        "no_matching_records",
    )
    assert result.envelope.coverage.population == 10 and "explain" not in kinds(result)


async def test_who_has_an_app_resolves_a_follow_up_to_the_listed_device(real_db_path):
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("last")],
        acts=[
            *one_call("software_inventory", {"view": "current", "app_name": "utorrent"}),
            *one_call("software_inventory", {"view": "new_installs"}),
        ],
        explains=[explanation(headline="uTorrent is installed on 2 of 7 devices")],
    )
    service = make_service(real_db_path, llm)
    first = await service.run_turn(INITECH_ADMIN, "c1", "Which devices have uTorrent?")
    holders = golden("initech-003")["software"]["holders"]["uTorrent"]
    assert set(listed_ids(first.envelope)) == set(holders)
    # the list follows the table, which is ordered by hostname: "the last one" is the last row
    rows = first.envelope.table.rows
    assert [r.label for r in rows] == sorted(r.label for r in rows)
    assert listed_ids(first.envelope) == [r.device_id for r in rows]
    second = await service.run_turn(INITECH_ADMIN, "c1", "When was it installed on the last one?")
    last = rows[-1].device_id
    assert second.envelope.outcome == "answer"
    assert {d for c in second.envelope.claims for d in c.device_ids} == {last}
    assert second.envelope.coverage.population == 1
    first_seen = next(
        i["first_seen"]
        for i in installs("initech-003")
        if i["device_id"] == last and i["app"] == "uTorrent"
    )
    assert f"first seen {first_seen[:10]} {first_seen[11:16]}Z" in second.envelope.claims[0].text


async def test_open_the_first_one_after_a_new_install_list_means_the_newest_install(real_db_path):
    newest = max(installs("acme-001"), key=lambda i: i["first_seen"])["device_id"]
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("ordinal", 1)],
        acts=[*one_call(*NEW), *one_call("software_inventory", {"view": "current"})],
        explains=[explanation(), explanation()],
    )
    service = make_service(real_db_path, llm)
    first = await service.run_turn(ACME_ADMIN, "c1", QUESTION)
    assert listed_ids(first.envelope)[0] == newest
    second = await service.run_turn(
        ACME_ADMIN, "c1", "Open the first one and show what is installed"
    )
    assert second.envelope.outcome == "answer"
    assert {d for c in second.envelope.claims for d in c.device_ids} == {newest}
    assert second.envelope.coverage.population == 1
    assert next(e for e in second.trace if e.kind == "reference").data["devices"] == 1
    assert "<resolved>" in llm.calls_of("act")[2].text


async def test_new_installs_are_listed_newest_first_not_in_device_id_order(real_db_path):
    llm = ScriptedLLM(plans=[answer_plan()], acts=one_call(*NEW), explains=[explanation()])
    result = await make_service(real_db_path, llm).run_turn(INITECH_ADMIN, "c1", QUESTION)
    newest_first = [
        i["device_id"] for i in sorted(installs("initech-003"), key=lambda i: i["first_seen"])
    ][::-1]
    assert newest_first == ["W2656QDAE2ZJ", "I3JLKILKQTAC", "DARFCP0BQM2G"]
    assert listed_ids(result.envelope) == newest_first
    assert listed_ids(result.envelope) != sorted(newest_first)


async def test_an_app_that_only_another_company_has_is_an_input_error_the_model_can_fix(
    real_db_path,
):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[
            tool_calls_message(
                ("software_inventory", {"view": "current", "app_name": "TeamViewer"})
            ),
            tool_calls_message(("software_inventory", {"view": "current", "app_name": "uTorrent"})),
            final_message(),
        ],
        explains=[explanation(headline="uTorrent is installed on 1 of 10 devices")],
    )
    result = await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", "Who has TeamViewer?")
    assert_clean(result, llm)
    told = [
        str(m.content)
        for call in llm.calls_of("act")
        for m in call.messages
        if isinstance(m, ToolMessage)
    ]
    assert told and all("TeamViewer" not in text for text in told)
    assert any(text.startswith("Invalid tool input: app_name is not an app") for text in told)
    assert result.envelope.outcome == "answer" and "TeamViewer" not in blob_of(result)
    assert result.envelope.notes == []  # the corrected call went through, so nothing is missing


async def test_model_prose_that_names_apps_and_versions_from_the_facts_is_kept(real_db_path):
    text = "uTorrent 3.6 first appeared on acme-macbook-2 and CleanMyMac X 4.15 on acme-macbook-1."
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=one_call(*NEW),
        explains=[
            explanation(headline="2 new installs on 2 of 10 devices in the last 30 days", text=text)
        ],
    )
    result = await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", QUESTION)
    assert result.envelope.explanation == text
    assert result.envelope.headline == "2 new installs on 2 of 10 devices in the last 30 days"


@pytest.mark.parametrize(
    "text",
    [
        "uTorrent 3.7 first appeared on 1LYSSFD074BB.",  # a version the data never reported
        "uTorrent is risky software that nobody approved.",  # a label the data does not carry
        "uTorrent first appeared on ZZZ9QQQ88XYZ.",  # a device that is not listed
    ],
)
async def test_model_prose_that_adds_a_version_a_label_or_a_device_is_dropped(real_db_path, text):
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=one_call(*NEW),
        explains=[explanation(headline="2 new installs on 2 of 10 devices", text=text)],
    )
    result = await make_service(real_db_path, llm).run_turn(ACME_ADMIN, "c1", QUESTION)
    assert result.envelope.explanation is None
    assert result.envelope.headline.startswith("2 new installs on 2 of 10 devices in the last 30")
    assert text not in blob_of(result)
