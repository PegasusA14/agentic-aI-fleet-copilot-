import copy
import pickle
from datetime import datetime

import pytest
from factories import make_conn, make_row

from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext, dataset_as_of
from fleet_copilot.timeutil import parse_ts


@pytest.fixture
def conn():
    return make_conn(
        [
            make_row("A1", "acme-001", "2026-06-11T09:00:00Z"),
            make_row("A1", "acme-001", "2026-06-12T09:00:00Z"),
            make_row("A2", "acme-001", "2026-06-12T09:00:00Z", battery=None),
            make_row("G1", "globex-002", "2026-06-12T10:00:00Z"),
        ]
    )


def acme(conn):
    return FleetRepository(conn, TenantContext("acme-001", "admin-acme"))


def test_list_devices_is_tenant_scoped(conn):
    assert [d.device_id for d in acme(conn).list_devices()] == ["A1", "A2"]


def test_foreign_and_missing_devices_are_indistinguishable(conn):
    repo = acme(conn)
    with pytest.raises(NotFound) as foreign:
        repo.get_device("G1")
    with pytest.raises(NotFound) as missing:
        repo.get_device("NOPE")
    assert str(foreign.value) == str(missing.value) == "not found"


@pytest.mark.parametrize("method", ["snapshots", "latest"])
def test_history_and_latest_deny_foreign_devices(conn, method):
    with pytest.raises(NotFound):
        getattr(acme(conn), method)("G1")


def test_snapshots_are_ordered_and_can_be_windowed(conn):
    repo = acme(conn)
    assert [s.collected_at for s in repo.snapshots("A1")] == [
        parse_ts("2026-06-11T09:00:00Z"),
        parse_ts("2026-06-12T09:00:00Z"),
    ]
    assert len(repo.snapshots("A1", since=parse_ts("2026-06-12T00:00:00Z"))) == 1


def test_a_naive_since_is_rejected_instead_of_read_as_local_time(conn):
    with pytest.raises(ValueError, match="timezone-aware"):
        acme(conn).snapshots("A1", since=datetime(2026, 6, 12, 0, 0))


def test_latest_can_require_a_battery_block(conn):
    repo = acme(conn)
    assert repo.latest("A2").battery is None
    assert repo.latest("A2", require_battery=True) is None
    assert repo.latest("A1", require_battery=True).collected_at == parse_ts("2026-06-12T09:00:00Z")


def test_snapshot_carries_compliance_and_can_cite_itself(conn):
    snap = acme(conn).latest("A1")
    assert snap.compliance["screen_lock"].status == "pass"
    assert snap.ref("os").format() == "acme-001/A1@2026-06-12T09:00:00Z#os"


def test_resolve_returns_the_cited_values(conn):
    ref = EvidenceRef("acme-001", "A1", "2026-06-12T09:00:00Z", "compliance[screen_lock]")
    evidence = acme(conn).resolve(ref)
    assert evidence.value == {"check_id": "screen_lock", "status": "pass", "severity": "medium"}
    assert evidence.ref == ref.format()


@pytest.mark.parametrize(
    "ref",
    [
        EvidenceRef("globex-002", "G1", "2026-06-12T10:00:00Z", "os"),  # foreign company
        EvidenceRef(
            "acme-001", "G1", "2026-06-12T10:00:00Z", "os"
        ),  # forged company, foreign device
        EvidenceRef("acme-001", "A1", "2026-06-12T09:30:00Z", "os"),  # no such snapshot
        EvidenceRef("acme-001", "A1", "2026-06-12T09:00:00Z", "bogus"),  # unknown path
        EvidenceRef("acme-001", "A2", "2026-06-12T09:00:00Z", "battery"),  # block absent
    ],
)
def test_resolve_denies_everything_it_cannot_prove(conn, ref):
    with pytest.raises(NotFound):
        acme(conn).resolve(ref)


def test_dataset_as_of_is_the_latest_collected_at(conn):
    assert dataset_as_of(conn) == parse_ts("2026-06-12T10:00:00Z")


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_not_found_survives_pickling(protocol):
    clone = pickle.loads(pickle.dumps(NotFound(), protocol))
    assert type(clone) is NotFound
    assert str(clone) == "not found"


@pytest.mark.parametrize("duplicate", [copy.copy, copy.deepcopy])
def test_not_found_survives_copying(duplicate):
    clone = duplicate(NotFound())
    assert type(clone) is NotFound
    assert str(clone) == "not found"


def test_not_found_never_carries_a_caller_supplied_detail():
    assert str(NotFound("globex-002/G1")) == "not found"
    assert NotFound("detail").args == ("not found",)
