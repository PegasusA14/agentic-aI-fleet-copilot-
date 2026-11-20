import pytest
from api_support import client_for, sign_in
from factories import BASE_SOFTWARE, make_conn, make_row

from fleet_copilot.agent.llm import ScriptedLLM
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import (
    FleetRepository,
    NotFound,
    SoftwareItem,
    TenantContext,
)

UTORRENT = {"name": "uTorrent", "version": "3.6", "publisher": "Rainberry Inc"}
TENANTS = ["acme-001", "globex-002", "initech-003"]
OLD, NEW = "2026-06-11T09:00:00Z", "2026-06-12T09:00:00Z"


@pytest.fixture
def conn():
    return make_conn(
        [
            make_row("A1", "acme-001", OLD),
            make_row("A1", "acme-001", NEW, installed_software=[*BASE_SOFTWARE, UTORRENT]),
            make_row("G1", "globex-002", NEW, installed_software=[UTORRENT]),
        ]
    )


def acme(conn):
    return FleetRepository(conn, TenantContext("acme-001", "admin-acme"))


def ref(company, device, ts, path):
    return EvidenceRef(company, device, ts, path)


def test_snapshots_carry_their_software_sorted_by_name(conn):
    old, new = acme(conn).snapshots("A1")
    assert [s.name for s in old.software] == ["Google Chrome", "Slack"]
    assert [s.name for s in new.software] == ["Google Chrome", "Slack", "uTorrent"]
    assert new.software[2] == SoftwareItem("uTorrent", "3.6", "Rainberry Inc")
    assert isinstance(new.software, tuple)
    assert acme(conn).latest("A1").software == new.software


def test_a_foreign_devices_software_is_never_hydrated_for_this_tenant(conn):
    with pytest.raises(NotFound):
        acme(conn).latest("G1")
    other = FleetRepository(conn, TenantContext("globex-002", "admin-globex"))
    assert [s.name for s in other.latest("G1").software] == ["uTorrent"]
    assert [s.name for s in other.latest("G1").software if s.name == "Slack"] == []


def test_software_names_are_this_companys_own(conn):
    assert acme(conn).software_names() == ["Google Chrome", "Slack", "uTorrent"]
    other = FleetRepository(conn, TenantContext("globex-002", "admin-globex"))
    assert other.software_names() == ["uTorrent"]
    nobody = FleetRepository(conn, TenantContext("initech-003", "admin-initech"))
    assert nobody.software_names() == []


def test_a_software_reference_resolves_to_the_reported_item(conn):
    found = acme(conn).resolve(ref("acme-001", "A1", NEW, "software[uTorrent]"))
    assert found.value == {"name": "uTorrent", "version": "3.6", "publisher": "Rainberry Inc"}
    assert found.ref == "acme-001/A1@2026-06-12T09:00:00Z#software[uTorrent]"


def test_a_name_with_spaces_survives_the_reference_round_trip(conn):
    text = "acme-001/A1@2026-06-12T09:00:00Z#software[Google Chrome]"
    parsed = EvidenceRef.parse(text)
    assert parsed.path == "software[Google Chrome]" and parsed.format() == text
    assert acme(conn).resolve(parsed).value["version"] == "126.0"


@pytest.mark.parametrize(
    "path",
    [
        "software[uTorrent]",  # the app was not installed yet at that snapshot (see OLD below)
        "software[utorrent]",  # names are matched exactly in evidence
        "software[Nope]",
        "software[]",
        "software[uTorrent",
        "software[uTorrent]x",
        "xsoftware[uTorrent]",
        "software[../../etc]",
        "software[uTorrent][Slack]",
        "software",
    ],
)
def test_an_absent_or_malformed_software_reference_is_not_found(conn, path):
    with pytest.raises(NotFound) as err:
        acme(conn).resolve(ref("acme-001", "A1", OLD, path))
    assert str(err.value) == "not found"


def test_another_companys_software_is_not_found_however_the_ref_is_forged(conn):
    for company in ("globex-002", "acme-001"):
        with pytest.raises(NotFound):
            acme(conn).resolve(ref(company, "G1", NEW, "software[uTorrent]"))


@pytest.mark.parametrize("tenant", TENANTS)
def test_foreign_software_refs_look_like_nonexistent_ones(real_conn, tenant):
    repo = FleetRepository(real_conn, TenantContext(tenant, "isolation-test"))
    own = {d.device_id for d in repo.list_devices()}
    foreign = [
        (r["device_id"], r["collected_at"])
        for r in real_conn.execute(
            "SELECT device_id, MAX(collected_at) AS collected_at FROM snapshots GROUP BY device_id"
        )
        if r["device_id"] not in own
    ]
    assert foreign
    for device_id, stamp in [*foreign, ("DOES-NOT-EXIST", "2026-06-12T09:00:00Z")]:
        for claimed in TENANTS:
            with pytest.raises(NotFound) as err:
                repo.resolve(ref(claimed, device_id, stamp, "software[Slack]"))
            assert str(err.value) == "not found"


def test_the_real_dataset_resolves_a_new_install_and_a_common_app(real_conn):
    repo = FleetRepository(real_conn, TenantContext("acme-001", "admin-acme"))
    latest = repo.latest("1LYSSFD074BB")
    assert {s.name for s in latest.software} == {
        "1Password",
        "Google Chrome",
        "Slack",
        "Visual Studio Code",
        "Zoom",
        "uTorrent",
    }
    assert repo.resolve(latest.ref("software[uTorrent]")).value["version"] == "3.6"
    assert repo.resolve(latest.ref("software[Visual Studio Code]")).value["version"] == "1.90"
    assert "uTorrent" in repo.software_names() and "TeamViewer" not in repo.software_names()


async def test_the_evidence_endpoint_serves_a_software_ref_with_spaces(
    real_db_path, real_conn, tmp_path
):
    repo = FleetRepository(real_conn, TenantContext("acme-001", "admin-acme"))
    good = repo.latest("1LYSSFD074BB").ref("software[Visual Studio Code]").format()
    foreign = good.replace("acme-001", "globex-002")
    async with client_for(real_db_path, tmp_path, ScriptedLLM()) as client:
        await sign_in(client, "admin-acme")
        mine = await client.get("/api/evidence", params={"ref": good})
        theirs = await client.get("/api/evidence", params={"ref": foreign})
        absent = await client.get(
            "/api/evidence", params={"ref": good.replace("Visual Studio Code", "Nope")}
        )
    assert mine.status_code == 200
    assert mine.json()["value"] == {
        "name": "Visual Studio Code",
        "version": "1.90",
        "publisher": "Microsoft",
    }
    assert theirs.status_code == absent.status_code == 404
    assert theirs.json() == absent.json() == {"detail": "not found"}
