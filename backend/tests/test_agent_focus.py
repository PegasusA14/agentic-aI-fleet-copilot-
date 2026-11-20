import pytest
from agent_support import ACME, GLOBEX, repo_for, run

from fleet_copilot.agent.compose import compose
from fleet_copilot.agent.focus import (
    NeedsClarification,
    NotFoundRef,
    Resolved,
    focus_summary,
    resolve_reference,
)
from fleet_copilot.agent.llm import Reference
from fleet_copilot.agent.texts import NEED_REFERENCE, WHICH_LIST, entry_out_of_range
from fleet_copilot.contracts import EntityList, EntityRef
from fleet_copilot.repository import Device


def make_list(list_id, title, *pairs):
    items = [
        EntityRef(rank=i, device_id=d, label=label) for i, (d, label) in enumerate(pairs, start=1)
    ]
    return EntityList(list_id=list_id, title=title, items=items)


def device(device_id, hostname):
    return Device("acme-001", device_id, "emp", hostname, "macOS", "MacBook Pro", "laptop")


DEVICES = [
    device("A", "host-a"),
    device("B", "host-b"),
    device("C", "host-c"),
    device("D", "host-d"),
]
LOW_DISK = make_list(
    "t1/low-1", "Low disk (<10% free)", ("A", "host-a"), ("B", "host-b"), ("C", "host-c")
)
SCREEN = make_list("t1/cmp-1-screen_lock", "screen_lock failures", ("D", "host-d"), ("A", "host-a"))
OS_FAIL = make_list(
    "t1/cmp-1-os_up_to_date", "os_up_to_date failures", ("B", "host-b"), ("C", "host-c")
)


def ids(resolution):
    assert isinstance(resolution, Resolved), resolution
    return list(resolution.device_ids)


def test_ordinal_picks_that_position_from_the_only_list():
    result = resolve_reference(Reference(kind="ordinal", index=2), [LOW_DISK], DEVICES)
    assert ids(result) == ["B"] and result.list_id == "t1/low-1"


def test_last_and_all_use_the_displayed_order():
    assert ids(resolve_reference(Reference(kind="last"), [LOW_DISK], DEVICES)) == ["C"]
    assert ids(resolve_reference(Reference(kind="all"), [LOW_DISK], DEVICES)) == ["A", "B", "C"]


def test_an_ordinal_past_the_end_asks_which_one_with_the_labels():
    result = resolve_reference(Reference(kind="ordinal", index=9), [LOW_DISK], DEVICES)
    assert result == NeedsClarification(entry_out_of_range(3), ("host-a", "host-b", "host-c"))


def test_no_previous_list_needs_clarification():
    for ref in (Reference(kind="ordinal", index=1), Reference(kind="last"), Reference(kind="all")):
        assert resolve_reference(ref, [], DEVICES) == NeedsClarification(NEED_REFERENCE, ())


def test_two_lists_make_an_ordinal_ambiguous_and_the_options_are_their_titles():
    result = resolve_reference(Reference(kind="ordinal", index=1), [SCREEN, OS_FAIL], DEVICES)
    assert result == NeedsClarification(
        WHICH_LIST, ("screen_lock failures", "os_up_to_date failures")
    )


def test_a_list_hint_chooses_the_list_whatever_the_wording_of_the_title():
    focus = [SCREEN, OS_FAIL]
    assert ids(
        resolve_reference(
            Reference(kind="ordinal", index=1, list_hint="screen lock"), focus, DEVICES
        )
    ) == ["D"]
    assert ids(
        resolve_reference(Reference(kind="last", list_hint="OS up to date"), focus, DEVICES)
    ) == ["C"]


def test_a_hint_that_matches_nothing_asks_which_list():
    result = resolve_reference(
        Reference(kind="ordinal", index=1, list_hint="battery"), [SCREEN, OS_FAIL], DEVICES
    )
    assert isinstance(result, NeedsClarification) and result.question == WHICH_LIST
    assert result.options == ("screen_lock failures", "os_up_to_date failures")


def test_a_hint_that_matches_several_lists_still_asks():
    result = resolve_reference(
        Reference(kind="ordinal", index=1, list_hint="failures"), [SCREEN, OS_FAIL], DEVICES
    )
    assert isinstance(result, NeedsClarification) and len(result.options) == 2


def test_those_after_several_lists_means_everything_shown_without_duplicates():
    result = resolve_reference(Reference(kind="all"), [SCREEN, OS_FAIL], DEVICES)
    assert ids(result) == ["D", "A", "B", "C"] and result.list_id is None


def test_named_devices_match_ids_and_hostnames_case_insensitively():
    assert ids(resolve_reference(Reference(kind="named", device_ref="C"), [], DEVICES)) == ["C"]
    assert ids(resolve_reference(Reference(kind="named", device_ref="HOST-B"), [], DEVICES)) == [
        "B"
    ]


def test_a_named_device_id_typed_in_lower_case_resolves_to_the_tenants_device():
    for typed in ("c", "  c  ", "C"):
        assert ids(resolve_reference(Reference(kind="named", device_ref=typed), [], DEVICES)) == [
            "C"
        ]


def test_a_real_tenant_device_id_in_lower_case_resolves_and_a_foreign_one_does_not(
    real_conn,
):
    devices = repo_for(real_conn, ACME).list_devices()
    assert ids(
        resolve_reference(Reference(kind="named", device_ref="mt7pjb7n5lre"), [], devices)
    ) == ["MT7PJB7N5LRE"]
    for foreign in ("jrzsgxvmke6m", "JRZSGXVMKE6M", "nope"):
        assert (
            resolve_reference(Reference(kind="named", device_ref=foreign), [], devices)
            == NotFoundRef()
        )


def test_unknown_and_foreign_named_devices_resolve_identically():
    foreign = resolve_reference(
        Reference(kind="named", device_ref="JRZSGXVMKE6M"), [LOW_DISK], DEVICES
    )
    unknown = resolve_reference(Reference(kind="named", device_ref="NOPE"), [LOW_DISK], DEVICES)
    assert foreign == unknown == NotFoundRef()


def test_devices_that_left_the_tenant_are_dropped_and_an_empty_result_is_not_found():
    stale = make_list("t0/x", "Old list", ("Z", "host-z"))
    assert resolve_reference(Reference(kind="ordinal", index=1), [stale], DEVICES) == NotFoundRef()
    mixed = make_list("t0/y", "Mixed", ("Z", "host-z"), ("A", "host-a"))
    assert ids(resolve_reference(Reference(kind="all"), [mixed], DEVICES)) == ["A"]


def test_focus_summary_lists_titles_and_sizes_but_no_device_ids():
    lines = focus_summary([LOW_DISK, make_list("t1/one", "Single", ("A", "host-a"))])
    assert lines == ['1. "Low disk (<10% free)" (3 devices)', '2. "Single" (1 device)']
    assert not any("host-a" in line for line in lines)


# ---- against real composed answers -----------------------------------------------------------------


def composed_lists(real_db_path, real_conn, calls, tenant=ACME):
    records = [run(real_db_path, real_conn, name, args, tenant) for name, args in calls]
    comp = compose(records, resolve=repo_for(real_conn, tenant).resolve)
    return comp.entity_lists, repo_for(real_conn, tenant).list_devices()


def test_the_first_low_disk_device_resolves_from_a_real_answer(real_db_path, real_conn):
    lists, devices = composed_lists(real_db_path, real_conn, [("low_disk_devices", {})])
    assert ids(resolve_reference(Reference(kind="ordinal", index=1), lists, devices)) == [
        "MT7PJB7N5LRE"
    ]
    assert ids(resolve_reference(Reference(kind="all"), lists, devices)) == [
        "MT7PJB7N5LRE",
        "8NM23J95R5I6",
    ]


def test_two_real_compliance_lists_are_ambiguous_until_hinted(real_db_path, real_conn):
    lists, devices = composed_lists(
        real_db_path,
        real_conn,
        [("compliance_failures_now", {"severity": "medium", "form_factor": "laptop"})],
    )
    ambiguous = resolve_reference(Reference(kind="ordinal", index=1), lists, devices)
    assert ambiguous == NeedsClarification(
        WHICH_LIST, ("os_up_to_date failures", "screen_lock failures")
    )
    hinted = resolve_reference(
        Reference(kind="ordinal", index=1, list_hint="screen lock"), lists, devices
    )
    assert ids(hinted) == ["1LYSSFD074BB"]


@pytest.mark.parametrize("foreign", ["JRZSGXVMKE6M", "E4V0SB5I0MYU"])
def test_a_globex_device_cannot_be_named_by_an_acme_admin(real_db_path, real_conn, foreign):
    devices = repo_for(real_conn, ACME).list_devices()
    assert (
        resolve_reference(Reference(kind="named", device_ref=foreign), [], devices) == NotFoundRef()
    )
    globex_devices = repo_for(real_conn, GLOBEX).list_devices()
    assert ids(
        resolve_reference(Reference(kind="named", device_ref=foreign), [], globex_devices)
    ) == [foreign]
