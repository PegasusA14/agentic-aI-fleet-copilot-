import json

from fleet_copilot.config import PINNED_AS_OF, REPO_ROOT

G = json.loads((REPO_ROOT / "evals" / "golden" / "golden.json").read_text(encoding="utf-8"))
T = G["tenants"]


def test_meta_is_pinned():
    assert G["meta"]["as_of"] == PINNED_AS_OF
    assert G["meta"]["dataset_sha256"].startswith("fb0ee31f")
    assert sorted(T) == ["acme-001", "globex-002", "initech-003"]


def test_low_disk_devices():
    assert T["acme-001"]["low_disk_lt10"]["devices"] == ["8NM23J95R5I6", "MT7PJB7N5LRE"]
    assert T["globex-002"]["low_disk_lt10"]["devices"] == ["E4V0SB5I0MYU", "JRZSGXVMKE6M"]
    assert T["initech-003"]["low_disk_lt10"]["devices"] == ["DARFCP0BQM2G", "RLD7WV7J82SH"]


def test_macos_older_than_15():
    acme = T["acme-001"]["macos_below_15"]
    assert acme["devices"] == ["1LYSSFD074BB", "7VP16KHV88LM", "MT7PJB7N5LRE", "XSU9UUDGU6AC"]
    assert acme["population"] == 7 and len(acme["out_of_scope"]) == 3
    assert T["globex-002"]["macos_below_15"]["devices"] == ["4SEFDLPTKXAM", "QPZSXKINA7GP"]
    assert T["globex-002"]["macos_below_15"]["population"] == 5
    assert len(T["initech-003"]["macos_below_15"]["devices"]) == 4
    assert T["initech-003"]["macos_below_15"]["population"] == 5
    assert all(t["macos_below_15"]["unknown_version"] == [] for t in T.values())


def test_no_high_severity_failure_exists_anywhere():
    for tenant in T.values():
        assert tenant["failing_high_severity"] == {"all": [], "laptop": []}
        assert tenant["failing_latest"]["all"]["disk_encryption"] == []


def test_acme_compliance_failures():
    acme = T["acme-001"]
    assert acme["failing_latest"]["laptop"]["screen_lock"] == [
        "1LYSSFD074BB",
        "8NM23J95R5I6",
        "DSYPSES7SKBB",
        "MT7PJB7N5LRE",
    ]
    assert len(acme["failing_latest"]["laptop"]["os_up_to_date"]) == 9 == len(acme["laptops"])
    changes = {d: v["change_at"] for d, v in acme["compliance_trend"]["screen_lock"]["30"].items()}
    assert changes["1LYSSFD074BB"] == "2026-05-22T09:41:00Z"
    assert changes["MT7PJB7N5LRE"] == "2026-05-28T09:32:00Z"
    assert changes["8NM23J95R5I6"] == "2026-05-30T08:38:00Z"
    assert changes["DSYPSES7SKBB"] == "2026-06-01T10:20:00Z"
    persistent = acme["compliance_trend"]["os_up_to_date"]["30"]
    assert persistent["1LYSSFD074BB"] == {"kind": "persistent_failure", "change_at": None}


def test_memory_pressure_sustained_versus_transient():
    assert T["acme-001"]["memory_trend"]["85"]["30"]["M4XVHUV1MEPZ"] == "sustained"
    assert T["initech-003"]["memory_trend"]["85"]["30"]["I3JLKILKQTAC"] == "sustained"
    globex = T["globex-002"]["memory_trend"]
    assert globex["85"]["30"]["CUXW0BQSV1B7"] == "intermittent"
    assert globex["90"]["30"]["CUXW0BQSV1B7"] == "isolated"  # the transient counterexample


def test_disk_trend_distinguishes_onset_from_sustained():
    acme = T["acme-001"]["disk_trend_lt10"]
    assert acme["30"]["MT7PJB7N5LRE"] == "sustained"
    assert acme["30"]["8NM23J95R5I6"] == "onset"
    assert acme["7"] == {"8NM23J95R5I6": "sustained", "MT7PJB7N5LRE": "sustained"}


def test_battery_signals():
    battery = T["acme-001"]["battery_30"]
    assert sorted(battery["notable"]) == ["7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"]
    assert {d: v["service_streak"] for d, v in battery["notable"].items()} == {
        "7VP16KHV88LM": 14,
        "8NM23J95R5I6": 21,
        "M4XVHUV1MEPZ": 20,
    }
    assert all(-4.4 < v["capacity_change_pct"] < -4.0 for v in battery["notable"].values())
    assert battery["missing_latest"] == ["MT7PJB7N5LRE"]
