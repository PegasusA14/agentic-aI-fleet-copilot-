"""One device order: the answer table's first row is the device "the first one" resolves to."""

import pytest
from agent_support import ACME, GLOBEX, INITECH, repo_for, run
from graph_support import (
    INITECH_ADMIN,
    answer_plan,
    explanation,
    follow_up_plan,
    make_service,
)

from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.domain.osversion import parse_version

CALLS = [
    ("low_disk_devices", {}),
    ("memory_pressure_now", {}),
    ("macos_version_check", {"older_than": "15"}),
    ("compliance_failures_now", {}),
    ("trend", {"metric": "disk"}),
    ("trend", {"metric": "memory"}),
    ("trend", {"metric": "compliance", "check_id": "screen_lock"}),
    ("trend", {"metric": "battery"}),
    ("find_devices", {"os_name": "macOS"}),
    ("software_inventory", {"view": "current"}),
    ("software_inventory", {"view": "current", "app_name": "utorrent"}),
    ("software_inventory", {"view": "new_installs"}),
]


def default_order(table, group_check=None):
    """The device ids of a table in its default row order, each once (a device can have several rows)."""
    rows = [r for r in table.rows if group_check is None or r.cells["check"].display == group_check]
    return list(dict.fromkeys(r.device_id for r in rows))


@pytest.mark.parametrize("tenant", [ACME, GLOBEX, INITECH])
@pytest.mark.parametrize(
    "call", CALLS, ids=[f"{n}-{a.get('metric') or a.get('view') or ''}" for n, a in CALLS]
)
def test_every_entity_list_follows_the_table_row_order(real_db_path, real_conn, tenant, call):
    name, args = call
    record = run(real_db_path, real_conn, name, args, tenant)
    comp = compose([record], resolve=repo_for(real_conn, tenant).resolve)
    if comp.table is None:
        pytest.skip("nothing matched, so there is no table")
    assert comp.entity_lists
    for entity_list in comp.entity_lists:
        listed = [i.device_id for i in entity_list.items]
        check = (
            entity_list.title.removesuffix(" failures")
            if name == "compliance_failures_now"
            else None
        )
        expected = [d for d in default_order(comp.table, check) if d in listed]
        assert listed == expected, (entity_list.title, listed, expected)
        assert [i.rank for i in entity_list.items] == list(range(1, len(listed) + 1))


@pytest.mark.parametrize("tenant", [ACME, GLOBEX, INITECH])
def test_a_single_list_tool_lists_exactly_the_tables_devices_in_its_order(
    real_db_path, real_conn, tenant
):
    for name, args in [("low_disk_devices", {}), ("macos_version_check", {"older_than": "15"})]:
        comp = compose(
            [run(real_db_path, real_conn, name, args, tenant)],
            resolve=repo_for(real_conn, tenant).resolve,
        )
        (entity_list,) = comp.entity_lists
        assert [i.device_id for i in entity_list.items] == [r.device_id for r in comp.table.rows]


def test_the_compliance_lists_order_each_check_by_that_checks_rows(real_db_path, real_conn):
    comp = compose(
        [run(real_db_path, real_conn, "compliance_failures_now", {}, ACME)],
        resolve=repo_for(real_conn, ACME).resolve,
    )
    assert len(comp.entity_lists) >= 2
    for entity_list in comp.entity_lists:
        check = entity_list.title.removesuffix(" failures")
        rows = [r.device_id for r in comp.table.rows if r.cells["check"].display == check]
        assert [i.device_id for i in entity_list.items] == rows


# ---- "the first one" after the macOS list ------------------------------------------------------


def oldest_mac(real_conn, ids):
    """The tenant's oldest-version device among `ids`, by the dataset's own versions (ties by id)."""
    repo = repo_for(real_conn, INITECH)
    return min(ids, key=lambda d: (parse_version(repo.latest(d).os_version) or (), d))


async def test_the_first_one_after_the_macos_list_is_the_oldest_version_mac(
    real_db_path, real_conn
):
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("ordinal", 1)],
        acts=[
            tool_calls_message(("macos_version_check", {"older_than": "15"})),
            final_message(),
            tool_calls_message(("trend", {"metric": "disk"})),
            final_message(),
        ],
        explains=[explanation(headline="4 of 5 macOS devices are older"), explanation()],
    )
    service = make_service(real_db_path, llm)
    first = await service.run_turn(INITECH_ADMIN, "c1", "Which Macs need a macOS update?")
    listed = [i.device_id for i in first.envelope.entity_lists[0].items]
    oldest = oldest_mac(real_conn, listed)
    assert first.envelope.table.rows[0].device_id == listed[0] == oldest
    second = await service.run_turn(INITECH_ADMIN, "c1", "show the disk trend for the first one")
    assert next(e for e in second.trace if e.kind == "reference").data["devices"] == 1
    assert {d for c in second.envelope.claims for d in c.device_ids} <= {oldest}
    assert f"<resolved>\n{oldest}\n</resolved>" in llm.calls_of("act")[2].text


async def test_the_second_one_is_the_second_table_row(real_db_path, real_conn):
    llm = ScriptedLLM(
        plans=[answer_plan(), follow_up_plan("ordinal", 2)],
        acts=[
            tool_calls_message(("macos_version_check", {"older_than": "15"})),
            final_message(),
            tool_calls_message(("trend", {"metric": "disk"})),
            final_message(),
        ],
        explains=[explanation(headline="4 of 5 macOS devices are older"), explanation()],
    )
    service = make_service(real_db_path, llm)
    first = await service.run_turn(INITECH_ADMIN, "c1", "Which Macs need a macOS update?")
    second_row = first.envelope.table.rows[1].device_id
    await service.run_turn(INITECH_ADMIN, "c1", "and the second one?")
    assert f"<resolved>\n{second_row}\n</resolved>" in llm.calls_of("act")[2].text
