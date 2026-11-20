import re

import pytest
from agent_support import ACME, GLOBEX, INITECH, repo_for, run
from factories import BASE_SOFTWARE, make_conn, make_row

from fleet_copilot.agent.compose import Composition, compose, evidence_kind, evidence_text
from fleet_copilot.agent.records import ToolRecord
from fleet_copilot.agent.verify import (
    allowed_numbers,
    check_prose,
    known_ids,
    known_labels,
    verify_evidence,
    verify_prose,
)
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.fleet import queries as q
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

NEW = {"view": "new_installs"}
FORBIDDEN = re.compile(
    r"malicious|unapproved|unauthori[sz]ed|risk|suspicious|banned|blocked|harmful|unsafe|illegal|piracy",
    re.IGNORECASE,
)
CHROME, SLACK = BASE_SOFTWARE
TORRENT = {"name": "uTorrent", "version": "3.6", "publisher": "Rainberry Inc"}


def composed(real_db_path, real_conn, args, tenant=ACME) -> Composition:
    record = run(real_db_path, real_conn, "software_inventory", args, tenant)
    return compose([record], resolve=repo_for(real_conn, tenant).resolve)


def envelope(comp, real_conn, tenant=ACME):
    return comp.to_envelope(company_id=tenant, as_of=dataset_as_of(real_conn), trace_id="t-1")


# ---- new installs ------------------------------------------------------------------------------------


def test_new_installs_are_one_claim_per_app_with_dates_counts_and_the_rule(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, NEW)
    assert (comp.outcome, comp.reason_code) == ("answer", None)
    cleanmymac, utorrent = comp.claims
    assert cleanmymac.device_ids == ["7VP16KHV88LM"] and cleanmymac.count == 1
    assert utorrent.device_ids == ["1LYSSFD074BB"] and utorrent.count == 1
    assert cleanmymac.text.startswith(
        "Newly installed in the last 30 days and not in the common set: CleanMyMac X on 1 of 10 devices."
    )
    assert "CleanMyMac X 4.15; first seen 2026-05-25 08:55Z" in cleanmymac.text
    assert "uTorrent 3.6; first seen 2026-05-31 11:13Z" in utorrent.text
    assert "7VP16KHV88LM (acme-" in cleanmymac.text
    assert comp.fallback_headline == (
        "2 new installs on 2 of 10 devices in the last 30 days (not in the common set)"
    )
    assert comp.coverage.population == 10
    (rule,) = comp.rules
    assert (
        "common set" in rule and "1Password, Google Chrome, Slack, Visual Studio Code, Zoom" in rule
    )


def test_one_entity_list_holds_each_device_once_newest_install_first(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, NEW)
    (entity_list,) = comp.entity_lists
    assert entity_list.title == "New software (30 d)"
    assert [i.device_id for i in entity_list.items] == ["1LYSSFD074BB", "7VP16KHV88LM"]
    initech = composed(real_db_path, real_conn, NEW, INITECH)
    assert [i.device_id for i in initech.entity_lists[0].items] == [
        "W2656QDAE2ZJ",
        "I3JLKILKQTAC",
        "DARFCP0BQM2G",
    ]
    assert [c.device_ids for c in initech.claims] == [
        ["DARFCP0BQM2G"],  # TeamViewer
        ["W2656QDAE2ZJ", "I3JLKILKQTAC"],  # uTorrent, newest first
    ]
    assert initech.claims[1].count == 2


def test_the_evidence_shows_the_first_and_the_last_sighting(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, NEW)
    by_ref = {e.ref: e for e in comp.evidence}
    utorrent = next(c for c in comp.claims if "uTorrent" in c.text)
    assert len(utorrent.refs) == 2 and set(utorrent.refs) <= set(by_ref)
    first, last = (by_ref[r] for r in utorrent.refs)
    assert first.ref.endswith("@2026-05-31T11:13:00Z#software[uTorrent]")
    assert last.ref.endswith("@2026-06-12T09:47:00Z#software[uTorrent]")
    assert first.value == "uTorrent 3.6, published by Rainberry Inc"
    assert first.label.startswith("Software · acme-") and "2026-05-31 11:13Z" in first.label
    assert all(EvidenceRef.parse(r).company_id == ACME for r in by_ref)


def test_no_new_install_in_a_short_window_is_a_no_match_that_still_states_the_rule(
    real_db_path, real_conn
):
    comp = composed(real_db_path, real_conn, {**NEW, "window_days": 7})
    assert (comp.outcome, comp.reason_code) == ("no_match", "no_matching_records")
    assert comp.claims == [] and comp.evidence == []
    assert comp.coverage.population == 10
    assert comp.fallback_headline == "No devices matched (10 evaluated)"
    assert "last 7 days" in comp.rules[0]


def test_a_removed_app_and_a_changed_version_are_both_said_plainly():
    conn = make_conn(
        [
            make_row("A", ts="2026-06-01T09:00:00Z", installed_software=[CHROME]),
            make_row("A", ts="2026-06-05T09:00:00Z", installed_software=[CHROME, TORRENT]),
            make_row("A", ts="2026-06-09T09:00:00Z", installed_software=[CHROME]),
            make_row("B", ts="2026-06-01T09:00:00Z", installed_software=[CHROME]),
            make_row("B", ts="2026-06-05T09:00:00Z", installed_software=[CHROME, SLACK]),
            make_row(
                "B",
                ts="2026-06-09T09:00:00Z",
                installed_software=[CHROME, {**SLACK, "version": "4.40"}],
            ),
        ]
    )
    repo = FleetRepository(conn, TenantContext("acme-001", "t"))
    record = ToolRecord.from_query(
        "software_inventory", NEW, q.new_software(repo, dataset_as_of(conn))
    )
    comp = compose([record], resolve=repo.resolve)
    text = " ".join(c.text for c in comp.claims)
    assert (
        "uTorrent 3.6; first seen 2026-06-05 09:00Z; no longer installed, last seen 2026-06-05 09:00Z"
        in text
    )
    assert "Slack 4.39, now 4.40; first seen 2026-06-05 09:00Z" in text
    slack, utorrent = comp.claims
    assert "no longer installed" not in slack.text  # Slack is still there
    assert "no longer installed" in utorrent.text
    envelope_ = comp.to_envelope(company_id="acme-001", as_of=dataset_as_of(conn), trace_id="t-1")
    assert verify_evidence(envelope_, repo.resolve) == []


# ---- who has an app, and the inventory ---------------------------------------------------------------


def test_holders_are_listed_with_their_version(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, {"view": "current", "app_name": "utorrent"})
    (claim,) = comp.claims
    assert claim.device_ids == ["1LYSSFD074BB"]
    assert claim.text.startswith("uTorrent is installed at the latest snapshot on 1 of 10 devices.")
    assert "uTorrent 3.6" in claim.text
    assert comp.fallback_headline == "uTorrent is installed on 1 of 10 devices"
    assert comp.entity_lists[0].title == "Devices with uTorrent"
    (item,) = comp.evidence
    assert item.value == "uTorrent 3.6, published by Rainberry Inc"
    assert item.ref.endswith("#software[uTorrent]")


def test_an_app_nobody_holds_now_is_a_no_match(real_db_path, real_conn):
    conn = make_conn([make_row("A", ts="2026-06-12T09:00:00Z")])
    repo = FleetRepository(conn, TenantContext("acme-001", "t"))
    record = ToolRecord.from_query(
        "software_inventory",
        {"view": "current", "app_name": "uTorrent"},
        q.software_installed(repo, dataset_as_of(conn), name="uTorrent"),
    )
    comp = compose([record], resolve=repo.resolve)
    assert (comp.outcome, comp.reason_code) == ("no_match", "no_matching_records")


def test_the_inventory_is_one_claim_that_flags_the_apps_not_on_every_device(
    real_db_path, real_conn
):
    comp = composed(real_db_path, real_conn, {"view": "current"})
    (claim,) = comp.claims
    assert claim.count == 10 and len(claim.device_ids) == 10
    assert claim.text.startswith("Installed software at the latest snapshot on 10 of 10 devices.")
    assert (
        "1LYSSFD074BB (acme-" in claim.text
        and "6 apps; not on every device: uTorrent 3.6" in claim.text
    )
    assert "6 apps; not on every device: CleanMyMac X 4.15" in claim.text
    assert claim.text.count("5 apps") == 8
    assert "Google Chrome 126.0" in comp.rules[0]
    assert comp.fallback_headline == "10 of 10 devices report installed software"
    assert [i.device_id for i in comp.entity_lists[0].items] == claim.device_ids
    assert len(comp.evidence) == 10  # one cited reading per device


# ---- every tenant, every view; wording; verification -----------------------------------------------------


@pytest.mark.parametrize("tenant", [ACME, GLOBEX, INITECH])
@pytest.mark.parametrize(
    "args",
    [
        {"view": "current"},
        {"view": "current", "app_name": "uTorrent"},
        {"view": "new_installs"},
        {"view": "new_installs", "app_name": "uTorrent"},
    ],
)
def test_every_view_composes_a_valid_verified_envelope_in_plain_wording(
    real_db_path, real_conn, tenant, args
):
    comp = composed(real_db_path, real_conn, args, tenant)
    built = envelope(comp, real_conn, tenant)  # contract validation is part of the assertion
    assert all(EvidenceRef.parse(e.ref).company_id == tenant for e in built.evidence)
    assert verify_evidence(built, repo_for(real_conn, tenant).resolve) == []
    text = " ".join(
        [comp.fallback_headline, *comp.notes, *comp.rules, *(c.text for c in comp.claims)]
        + [e.value for e in comp.evidence]
    )
    assert not FORBIDDEN.search(text), text


def test_the_explainer_is_given_app_names_and_versions_as_facts(real_db_path, real_conn):
    facts = composed(real_db_path, real_conn, NEW).facts()
    blob = " ".join(facts["claims"] + facts["rules"])
    assert "uTorrent 3.6" in blob and "CleanMyMac X 4.15" in blob and "2026-05-31" in blob


def test_evidence_text_and_kind_for_software():
    value = {"name": "Zoom", "version": "6.1.1", "publisher": "Zoom Video"}
    assert evidence_text("software", value) == "Zoom 6.1.1, published by Zoom Video"
    assert evidence_kind("software[Google Chrome]") == "software"
    assert evidence_kind("compliance[screen_lock]") == "compliance"
    assert evidence_kind("disk") == "disk"


def test_tampered_or_unprovable_software_evidence_is_caught(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, NEW)
    repo = repo_for(real_conn)
    good = envelope(comp, real_conn)
    assert verify_evidence(good, repo.resolve) == []

    wrong_version = good.model_copy(
        update={
            "evidence": [
                e.model_copy(update={"value": e.value.replace("3.6", "3.7")}) for e in good.evidence
            ]
        }
    )
    assert verify_evidence(wrong_version, repo.resolve) == ["evidence_mismatch"]

    renamed = good.model_copy(
        update={
            "evidence": [
                e.model_copy(update={"ref": e.ref.replace("software[uTorrent]", "software[Nope]")})
                for e in good.evidence
            ]
        }
    )
    assert "unresolvable_ref" in verify_evidence(renamed, repo.resolve)


# ---- model prose about apps -----------------------------------------------------------------------------


def verdict(comp, real_conn, **kw):
    return verify_prose(comp, dataset_as_of(real_conn), **kw)


def test_prose_that_names_apps_versions_devices_and_dates_from_the_facts_passes(
    real_db_path, real_conn
):
    comp = composed(real_db_path, real_conn, NEW)
    got = verdict(
        comp,
        real_conn,
        headline="2 new installs on 2 of 10 devices in the last 30 days: uTorrent 3.6 and CleanMyMac X 4.15",
        explanation=(
            "uTorrent 3.6 first appeared on 1LYSSFD074BB on 2026-05-31, and CleanMyMac X 4.15 on "
            "7VP16KHV88LM on 2026-05-25. Neither is in the common set of 1Password, Google Chrome, Slack, "
            "Visual Studio Code and Zoom."
        ),
    )
    assert got.problems == ()
    assert got.explanation is not None and "CleanMyMac X 4.15" in got.explanation


def test_prose_that_names_a_shared_app_and_its_version_passes_on_the_inventory(
    real_db_path, real_conn
):
    comp = composed(real_db_path, real_conn, {"view": "current"})
    got = verdict(
        comp,
        real_conn,
        headline="10 of 10 devices report installed software",
        explanation="Google Chrome 126.0, Zoom 6.1.1, 1Password 8.10 and Visual Studio Code 1.90 are shared.",
    )
    assert got.problems == ()


@pytest.mark.parametrize(
    ("explanation", "code"),
    [
        (
            "uTorrent 3.7 appeared on 1LYSSFD074BB.",
            "explanation:number",
        ),  # a version nobody reported
        ("uTorrent 3.6 appeared on ZZZ9QQQ88XYZ.", "explanation:unknown_id"),
        ("uTorrent 3.6 appeared on acme-laptop-9.", "explanation:unknown_host"),
        ("uTorrent 3.6 appeared on 1LYSSFD074BB on 2026-05-27.", "explanation:number"),
        ("There is no new software outside the common set.", "explanation:claim_word"),
        ("uTorrent (see www.utorrent.example) is new.", "explanation:markup"),
    ],
)
def test_prose_that_invents_a_version_a_device_or_a_date_is_replaced(
    real_db_path, real_conn, explanation, code
):
    comp = composed(real_db_path, real_conn, NEW)
    got = verdict(comp, real_conn, headline=None, explanation=explanation)
    assert code in got.problems and got.explanation is None


@pytest.mark.parametrize(
    "text",
    [
        "uTorrent is risky software.",
        "CleanMyMac X looks suspicious on 7VP16KHV88LM.",
        "Unapproved apps were installed.",
        "uTorrent is a known threat.",
        "It could be malware.",
        "Unauthorised software appeared.",
        "Unauthorized software appeared.",
        "These installs are dangerous.",
    ],
)
def test_prose_may_not_label_software_as_risky_or_unapproved(real_db_path, real_conn, text):
    comp = composed(real_db_path, real_conn, NEW)
    for field in ("explanation", "headline"):
        got = verdict(comp, real_conn, **{"headline": None, "explanation": None, field: text})
        assert f"{field}:judgment_word" in got.problems, (field, text)
    assert verdict(comp, real_conn, headline=None, explanation=text).explanation is None


def test_the_judgment_word_check_only_applies_to_answers_about_software(real_db_path, real_conn):
    comp = compose(
        [run(real_db_path, real_conn, "low_disk_devices", {})], resolve=repo_for(real_conn).resolve
    )
    got = verdict(
        comp, real_conn, headline=None, explanation="Low disk space puts MT7PJB7N5LRE at risk."
    )
    assert got.problems == () and got.explanation is not None


def test_app_names_alone_never_trip_the_markup_or_id_checks(real_db_path, real_conn):
    comp = composed(real_db_path, real_conn, {"view": "current"})
    allowed, ids, labels = (
        allowed_numbers(comp, dataset_as_of(real_conn)),
        known_ids(comp),
        known_labels(comp),
    )
    text = "Visual Studio Code, Google Chrome, 1Password, CleanMyMac X, TeamViewer, uTorrent, Zoom and Slack"
    problems = check_prose(text, allowed=allowed, ids=ids, labels=labels, max_chars=200)
    assert problems == []
