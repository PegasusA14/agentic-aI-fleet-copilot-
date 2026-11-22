import json
from contextlib import closing
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest

from fleet_copilot.actions import executors
from fleet_copilot.actions.service import (
    ActionService,
    ProposalConflict,
    ProposalNotFound,
    Proposed,
    Refused,
)
from fleet_copilot.actions.store import ActionStore
from fleet_copilot.agent.identity import OPERATOR_IDENTITY, Identity
from fleet_copilot.data.loader import connect
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ACME = Identity("acme-001", "demo-user", "Demo admin", company_name="Acme")
GLOBEX = Identity("globex-002", "demo-globex", "Demo admin", company_name="Globex")
TICKET_DEVICE = "DLT35MHGTH2D"  # Acme, screen_lock failing at the latest reading
SCREEN_LOCK = {"check_id": "screen_lock"}
AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
IDS = iter(f"prp_{n:04d}" for n in range(1, 10_000))


def make_service(fleet_db, tmp_path):
    store = ActionStore(tmp_path / "state" / "actions.db", now=lambda: NOW)
    return ActionService(fleet_db=fleet_db, store=store, new_id=lambda: next(IDS))


@pytest.fixture
def svc(real_db_path, tmp_path):
    return make_service(real_db_path, tmp_path)


def propose(svc, action_type, device, identity=ACME, **kw):
    return svc.propose(
        company_id=identity.company_id,
        actor_id=identity.actor_id,
        action_type=action_type,
        device_id=device,
        as_of=AS_OF,
        **kw,
    )


def propose_ticket(svc, device=TICKET_DEVICE):
    got = propose(svc, "remediation_ticket", device, **SCREEN_LOCK)
    assert isinstance(got, Proposed), got
    return got


def totals(svc):
    return {table: sum(rows.values()) for table, rows in svc.effects().items()}


def events(svc, proposal_id=None):
    rows = svc.audit_all()
    if proposal_id:
        rows = [r for r in rows if r["proposal_id"] == proposal_id]
    return [r["event"] for r in reversed(rows)]


def assert_no_effects(svc):
    assert sum(totals(svc).values()) == 0
    assert set(totals(svc)) == {
        "tickets",
        "replacement_flags",
        "upgrade_orders",
        "employee_notifications",
    }


# ---- propose ------------------------------------------------------------------------------------


def test_propose_creates_a_pending_card_and_no_effect(svc):
    got = propose_ticket(svc)
    card = got.card
    assert got.reused is False
    assert card.status == "pending"
    assert card.company_id == "acme-001"
    assert card.target_device_id == TICKET_DEVICE
    assert card.action_type == "remediation_ticket"
    assert card.scope["device"] == TICKET_DEVICE
    assert card.scope["check_id"] == "screen_lock"
    assert card.expected_effect.endswith("(simulated)")
    assert card.evidence_refs
    assert len(card.payload_hash) == 64
    assert events(svc) == ["proposal_created"]
    created = svc.audit_all()[0]
    assert created["refs"] == card.evidence_refs
    assert created["payload_hash"] == card.payload_hash
    assert_no_effects(svc)


def test_the_same_proposal_again_is_reused_without_a_second_audit_row(svc):
    first = propose_ticket(svc)
    again = propose_ticket(svc)
    assert again.reused is True
    assert again.card.proposal_id == first.card.proposal_id
    assert len(svc.list(ACME)) == 1
    assert events(svc) == ["proposal_created"]


def test_an_ineligible_foreign_or_unknown_target_is_refused_and_audited(svc):
    ineligible = propose(svc, "remediation_ticket", TICKET_DEVICE, check_id="disk_encryption")
    foreign = propose(svc, "remediation_ticket", "JRZSGXVMKE6M", **SCREEN_LOCK)
    unknown = propose(svc, "remediation_ticket", "NOSUCHDEVICE", **SCREEN_LOCK)
    assert ineligible == Refused("not_eligible", ineligible.detail)
    assert isinstance(foreign, Refused) and isinstance(unknown, Refused)
    assert (foreign.reason, foreign.detail) == (unknown.reason, unknown.detail)
    assert foreign.reason == "not_found"
    assert svc.list(ACME) == []
    rows = svc.audit_all()
    assert [r["event"] for r in rows] == ["proposal_refused"] * 3
    assert {r["detail"]["reason"] for r in rows} == {"not_eligible", "not_found"}
    assert all(r["proposal_id"] is None and r["company_id"] == "acme-001" for r in rows)
    assert_no_effects(svc)


def test_the_notification_message_is_part_of_the_hash(svc):
    got = propose(svc, "employee_notification", "MT7PJB7N5LRE", topic="low_disk")
    assert isinstance(got, Proposed)
    assert "2.0% free" in got.card.scope["message"]
    assert got.card.scope["employee"]
    other = propose(svc, "employee_notification", "MT7PJB7N5LRE", topic="failing_check")
    assert isinstance(other, Refused) or other.card.payload_hash != got.card.payload_hash


def test_a_new_card_carries_a_code_written_risk_note_naming_the_device_and_company(svc):
    card = propose_ticket(svc).card
    assert card.risk == (
        f"Affects only {card.target_label} in acme-001. "
        "Simulated: nothing leaves this system. The decision is recorded in Audit."
    )
    named = propose(
        svc, "replacement_flag", "8NM23J95R5I6", company_name="Acme"
    )  # the display name when the caller has it
    assert isinstance(named, Proposed)
    assert named.card.risk.startswith(f"Affects only {named.card.target_label} in Acme. ")
    assert 0 < len(named.card.risk) <= 200


def test_the_notification_risk_note_says_who_receives_the_message(svc):
    got = propose(svc, "employee_notification", "MT7PJB7N5LRE", topic="low_disk", company_name="Acme")
    assert isinstance(got, Proposed)
    assert "the message goes only to its assigned employee" in got.card.risk
    assert len(got.card.risk) <= 200


def test_the_risk_note_never_exceeds_the_contract_limit():
    from fleet_copilot.actions.service import _risk_note

    for action in ("employee_notification", "upgrade_order"):
        text = _risk_note(action, "h" * 63, "c" * 64)
        assert 0 < len(text) <= 200


def test_the_risk_note_is_not_part_of_the_payload_hash(svc):
    first = propose(svc, "remediation_ticket", TICKET_DEVICE, company_name="Acme", **SCREEN_LOCK)
    again = propose(svc, "remediation_ticket", TICKET_DEVICE, company_name="Other", **SCREEN_LOCK)
    assert isinstance(first, Proposed) and isinstance(again, Proposed)
    # Different wording of the risk line, same signed payload: the pending card is simply reused.
    assert again.reused and again.card.payload_hash == first.card.payload_hash
    assert again.card.risk == first.card.risk


def test_a_card_stored_before_the_risk_field_still_loads_and_can_be_approved(svc, tmp_path):
    card = propose_ticket(svc).card
    path = tmp_path / "state" / "actions.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        (stored,) = conn.execute("SELECT card FROM proposals").fetchone()
        old = json.loads(stored)
        del old["risk"]
        conn.execute("UPDATE proposals SET card = ?", (json.dumps(old),))
    (loaded,) = svc.list(ACME)
    assert loaded.risk == "" and loaded.proposal_id == card.proposal_id
    decision = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert decision.card.status == "approved" or decision.card.status == "executed"


# ---- approve ------------------------------------------------------------------------------------


def test_approve_with_the_right_hash_executes_exactly_once(svc):
    card = propose_ticket(svc).card
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.duplicate is False
    assert done.card.status == "executed"
    assert done.effect and done.effect["simulated"] is True
    assert totals(svc) == {
        "tickets": 1,
        "replacement_flags": 0,
        "upgrade_orders": 0,
        "employee_notifications": 0,
    }
    with svc._store.transaction() as conn:
        row = conn.execute("SELECT * FROM tickets").fetchone()
    assert row["simulated"] == 1 and row["device_id"] == TICKET_DEVICE
    assert row["note"] == card.rationale
    assert events(svc, card.proposal_id) == [
        "proposal_created",
        "proposal_approved",
        "proposal_executed",
    ]
    assert svc.get(ACME, card.proposal_id).status == "executed"
    assert svc.audit(ACME, proposal_id=card.proposal_id)[0]["detail"] == {
        "effect_id": done.effect["effect_id"]
    }


def test_duplicate_approval_adds_no_second_effect(svc):
    card = propose_ticket(svc).card
    svc.approve(ACME, card.proposal_id, card.payload_hash)
    again = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert again.duplicate is True
    assert again.card.status == "executed"
    assert totals(svc)["tickets"] == 1
    assert events(svc, card.proposal_id)[-1] == "duplicate_approval"


def test_reject_then_approve_is_a_conflict_with_no_effect(svc):
    card = propose_ticket(svc).card
    rejected = svc.reject(ACME, card.proposal_id)
    assert rejected.card.status == "rejected" and rejected.effect is None
    with pytest.raises(ProposalConflict) as err:
        svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert err.value.reason == "not_pending:rejected"
    assert svc.get(ACME, card.proposal_id).status == "rejected"
    assert events(svc, card.proposal_id) == [
        "proposal_created",
        "proposal_rejected",
        "approval_refused",
    ]
    assert_no_effects(svc)


def test_reject_twice_is_a_conflict(svc):
    card = propose_ticket(svc).card
    svc.reject(ACME, card.proposal_id)
    with pytest.raises(ProposalConflict):
        svc.reject(ACME, card.proposal_id)
    assert_no_effects(svc)


def test_no_approval_means_no_effect(svc):
    card = propose_ticket(svc).card
    assert svc.get(ACME, card.proposal_id).status == "pending"
    assert [c.proposal_id for c in svc.list(ACME, "pending")] == [card.proposal_id]
    assert svc.list(ACME, "executed") == []
    assert_no_effects(svc)


def test_a_wrong_hash_changes_nothing(svc):
    card = propose_ticket(svc).card
    with pytest.raises(ProposalConflict) as err:
        svc.approve(ACME, card.proposal_id, "0" * 64)
    assert err.value.reason == "hash_mismatch"
    assert svc.get(ACME, card.proposal_id).status == "pending"
    assert events(svc, card.proposal_id) == ["proposal_created", "approval_refused"]
    assert_no_effects(svc)
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.card.status == "executed"


def test_another_tenant_cannot_see_or_decide_the_proposal(svc):
    card = propose_ticket(svc).card
    with pytest.raises(ProposalNotFound):
        svc.approve(GLOBEX, card.proposal_id, card.payload_hash)
    with pytest.raises(ProposalNotFound):
        svc.reject(GLOBEX, card.proposal_id)
    with pytest.raises(ProposalNotFound):
        svc.get(GLOBEX, card.proposal_id)
    with pytest.raises(ProposalNotFound):
        svc.approve(GLOBEX, "prp_unknown", card.payload_hash)
    assert svc.list(GLOBEX) == []
    assert svc.get(ACME, card.proposal_id).status == "pending"
    assert all(r["company_id"] == "globex-002" for r in svc.audit(GLOBEX))
    assert svc.audit(GLOBEX, proposal_id=card.proposal_id)[0]["event"] == "approval_refused"
    assert events(svc, card.proposal_id) == [
        "proposal_created",
        "approval_refused",
        "approval_refused",
    ]
    assert_no_effects(svc)


def test_an_operator_cannot_approve_or_reject(svc):
    card = propose_ticket(svc).card
    with pytest.raises(PermissionError):
        svc.approve(OPERATOR_IDENTITY, card.proposal_id, card.payload_hash)
    with pytest.raises(PermissionError):
        svc.reject(OPERATOR_IDENTITY, card.proposal_id)
    assert svc.get(ACME, card.proposal_id).status == "pending"
    assert_no_effects(svc)


LATEST = (
    "SELECT snapshot_id FROM snapshots WHERE company_id = 'acme-001' AND device_id = ? "
    "ORDER BY collected_at DESC LIMIT 1"
)


def test_approval_after_the_data_changed_invalidates_and_executes_nothing(real_db_path, tmp_path):
    db = tmp_path / "mutated.sqlite"
    shutil.copy(real_db_path, db)
    svc = make_service(db, tmp_path)
    card = propose_ticket(svc).card
    with sqlite3.connect(db) as conn:  # the latest reading now passes the check
        conn.execute(
            f"UPDATE compliance SET status = 'pass' WHERE check_id = 'screen_lock' "
            f"AND snapshot_id = ({LATEST})",
            (TICKET_DEVICE,),
        )
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.card.status == "invalidated"
    assert done.effect is None and done.duplicate is False
    assert svc.get(ACME, card.proposal_id).status == "invalidated"
    assert events(svc, card.proposal_id) == [
        "proposal_created",
        "proposal_approved",
        "proposal_invalidated",
    ]
    assert_no_effects(svc)
    with pytest.raises(ProposalConflict):
        svc.approve(ACME, card.proposal_id, card.payload_hash)


def test_changed_parameters_invalidate_instead_of_executing_a_different_action(
    real_db_path, tmp_path
):
    db = tmp_path / "mutated.sqlite"
    shutil.copy(real_db_path, db)
    svc = make_service(db, tmp_path)
    card = propose_ticket(svc).card
    with sqlite3.connect(db) as conn:  # still failing, but with another severity
        conn.execute(
            f"UPDATE compliance SET severity = 'critical' WHERE check_id = 'screen_lock' "
            f"AND snapshot_id = ({LATEST})",
            (TICKET_DEVICE,),
        )
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.card.status == "invalidated"
    assert_no_effects(svc)


def test_an_executor_error_leaves_no_effect_and_audits_only_the_error_type(svc, monkeypatch):
    card = propose_ticket(svc).card

    def boom(store, conn, row, eligible):
        store.insert_effect(
            conn,
            "remediation_ticket",
            {
                "proposal_id": row.proposal_id,
                "company_id": row.company_id,
                "device_id": row.target_device_id,
                "check_id": "screen_lock",
                "note": "partial",
            },
        )
        raise RuntimeError("secret detail host-123")

    monkeypatch.setitem(executors.EXECUTORS, "remediation_ticket", boom)
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.card.status == "failed" and done.effect is None
    assert svc.get(ACME, card.proposal_id).status == "failed"
    assert_no_effects(svc)
    assert events(svc, card.proposal_id) == [
        "proposal_created",
        "proposal_approved",
        "proposal_failed",
    ]
    failed = svc.audit(ACME, proposal_id=card.proposal_id)[0]
    assert failed["detail"] == {"error": "RuntimeError"}
    assert "secret" not in json.dumps(svc.audit_all())


# ---- every action type ----------------------------------------------------------------------------

CASES = [
    ("remediation_ticket", TICKET_DEVICE, SCREEN_LOCK, "tickets"),
    ("replacement_flag", "8NM23J95R5I6", {}, "replacement_flags"),
    ("upgrade_order", "M4XVHUV1MEPZ", {"component": "memory"}, "upgrade_orders"),
    ("employee_notification", "MT7PJB7N5LRE", {"topic": "low_disk"}, "employee_notifications"),
]


@pytest.mark.parametrize(("action_type", "device", "kwargs", "table"), CASES)
def test_each_action_type_writes_only_its_own_table(svc, action_type, device, kwargs, table):
    card = propose(svc, action_type, device, **kwargs).card
    assert_no_effects(svc)
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.card.status == "executed"
    assert totals(svc) == {t: int(t == table) for t in totals(svc)}
    with svc._store.transaction() as conn:
        row = dict(conn.execute(f"SELECT * FROM {table}").fetchone())
    assert row["simulated"] == 1
    assert row["company_id"] == "acme-001" and row["device_id"] == device
    if action_type == "upgrade_order":
        assert (row["current_spec"], row["target_spec"]) == ("16 GiB", "32 GiB")
    if action_type == "replacement_flag":
        assert row["reason"] == "battery_service_recommended"
    if action_type == "employee_notification":
        with connect(svc._fleet_db, read_only=True) as fleet:
            device_row = FleetRepository(fleet, TenantContext("acme-001", "t")).get_device(device)
        assert row["employee_id"] == device_row.employee_id
        assert row["message"] == card.scope["message"]
        assert "2.0% free" in row["message"]
        assert row["topic"] == "low_disk"


def test_audit_rows_hold_ids_and_refs_but_no_telemetry(svc):
    with connect(svc._fleet_db, read_only=True) as fleet:
        repo = FleetRepository(fleet, TenantContext("acme-001", "t"))
        sensitive = {
            value
            for _, device, _, _ in CASES
            for value in (repo.get_device(device).hostname, repo.get_device(device).employee_id)
        }
        assert dataset_as_of(fleet)
    for action_type, device, kwargs, _ in CASES:
        card = propose(svc, action_type, device, **kwargs).card
        svc.approve(ACME, card.proposal_id, card.payload_hash)
    rows = svc.audit_all()
    assert len(rows) == 12
    banned_keys = {"hostname", "employee_id", "employee", "ip", "ip_address", "mac", "mac_address"}
    for row in rows:
        assert not (set(row["detail"]) & banned_keys)
        text = json.dumps(row)
        assert not any(value in text for value in sensitive)


def test_a_failing_check_notification_is_rechecked_for_the_same_check(svc):
    card = propose(svc, "employee_notification", TICKET_DEVICE, topic="failing_check").card
    assert card.scope["check_id"] == "screen_lock"
    assert "screen_lock" in card.scope["message"]
    done = svc.approve(ACME, card.proposal_id, card.payload_hash)
    assert done.card.status == "executed"
    assert done.effect["message"] == card.scope["message"]
