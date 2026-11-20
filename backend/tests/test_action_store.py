import sqlite3
from datetime import UTC, datetime

import pytest

from fleet_copilot.actions.store import ActionStore, ProposalRow, payload_hash

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PARAMS = {"check_id": "screen_lock", "severity": "high"}


def make_store(tmp_path, now=NOW):
    return ActionStore(tmp_path / "state" / "actions.db", now=lambda: now)


def row(
    proposal_id="p1",
    company_id="acme",
    action_type="remediation_ticket",
    device="dev-1",
    params=None,
    **extra,
):
    params = PARAMS if params is None else params
    return ProposalRow(
        proposal_id=proposal_id,
        company_id=company_id,
        actor_id="demo-user",
        action_type=action_type,
        target_device_id=device,
        params=params,
        payload_hash=payload_hash(company_id, action_type, device, params),
        evidence_refs=["dev-1#compliance[screen_lock]"],
        card={"proposal_id": proposal_id, "status": "pending"},
        **extra,
    )


def test_schema_is_created_idempotently(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        store.insert_proposal(conn, row())
    again = ActionStore(tmp_path / "state" / "actions.db", now=lambda: NOW)
    with again.transaction() as conn:
        assert again.get(conn, "acme", "p1") is not None
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')"
            )
        }
    assert {
        "proposals",
        "tickets",
        "replacement_flags",
        "upgrade_orders",
        "employee_notifications",
        "audit_log",
        "audit_no_update",
        "audit_no_delete",
    } <= names


def test_insert_get_roundtrip_and_tenant_scope(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        store.insert_proposal(conn, row(conversation_id="c1", trace_id="t1"))
    with store.transaction() as conn:
        got = store.get(conn, "acme", "p1")
        assert got is not None
        assert got.params == PARAMS
        assert got.evidence_refs == ["dev-1#compliance[screen_lock]"]
        assert got.card == {"proposal_id": "p1", "status": "pending"}
        assert (got.status, got.created_at) == ("pending", "2026-11-20T12:00:00Z")
        assert (got.conversation_id, got.trace_id) == ("c1", "t1")
        assert got.decided_by is None and got.decided_at is None
        assert store.get(conn, "globex", "p1") is None
        assert store.get(conn, "acme", "missing") is None


def test_find_pending_by_hash_is_tenant_scoped(tmp_path):
    store = make_store(tmp_path)
    mine = row()
    with store.transaction() as conn:
        store.insert_proposal(conn, mine)
        found = store.find_pending(conn, "acme", mine.payload_hash)
        assert found is not None and found.proposal_id == "p1"
        assert store.find_pending(conn, "globex", mine.payload_hash) is None
        assert store.find_pending(conn, "acme", "0" * 64) is None
        store.set_status(conn, "acme", "p1", "approved", "demo-user", None)
        assert store.find_pending(conn, "acme", mine.payload_hash) is None


def test_set_status_records_decision_and_is_tenant_scoped(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        store.insert_proposal(conn, row())
        assert store.set_status(conn, "globex", "p1", "rejected", "mallory", None) is False
        assert store.get(conn, "acme", "p1").status == "pending"
        assert store.set_status(conn, "acme", "p1", "rejected", "demo-user", "changed my mind")
    with store.transaction() as conn:
        got = store.get(conn, "acme", "p1")
        assert (got.status, got.decided_by, got.detail) == (
            "rejected",
            "demo-user",
            "changed my mind",
        )
        assert got.decided_at == "2026-11-20T12:00:00Z"


def test_list_filters_by_tenant_and_status(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        store.insert_proposal(conn, row("p1", device="dev-1"))
        store.insert_proposal(conn, row("p2", device="dev-2"))
        store.insert_proposal(conn, row("p3", company_id="globex", device="dev-3"))
        store.set_status(conn, "acme", "p2", "rejected", "demo-user", None)
        assert [r.proposal_id for r in store.list(conn, "acme")] == ["p2", "p1"]
        assert [r.proposal_id for r in store.list(conn, "acme", status="pending")] == ["p1"]
        assert [r.proposal_id for r in store.list(conn, "globex")] == ["p3"]
        assert len(store.list(conn, "acme", limit=1)) == 1


def test_audit_log_refuses_update_and_delete(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        store.append_audit(conn, "proposal_created", company_id="acme", actor_id="demo-user")
    for statement in ("UPDATE audit_log SET actor_id = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            with store.transaction() as conn:
                conn.execute(statement)
    with store.transaction() as conn:
        assert len(store.audit(conn, None)) == 1


EFFECTS = [
    ("remediation_ticket", "tickets", {"device_id": "d", "check_id": "screen_lock", "note": "n"}),
    (
        "replacement_flag",
        "replacement_flags",
        {"device_id": "d", "reason": "battery_service_recommended"},
    ),
    (
        "upgrade_order",
        "upgrade_orders",
        {"device_id": "d", "component": "memory", "current_spec": "8 GiB", "target_spec": "16 GiB"},
    ),
    (
        "employee_notification",
        "employee_notifications",
        {"device_id": "d", "employee_id": "e1", "topic": "low_disk", "message": "m"},
    ),
]


@pytest.mark.parametrize(("action_type", "table", "effect"), EFFECTS)
def test_effect_unique_per_proposal(tmp_path, action_type, table, effect):
    store = make_store(tmp_path)
    full = {"proposal_id": "p1", "company_id": "acme", **effect}
    with store.transaction() as conn:
        store.insert_effect(conn, action_type, full)
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            store.insert_effect(conn, action_type, full)
    with store.transaction() as conn:
        assert store.effect_counts(conn)[table] == {"acme": 1}
        simulated, created_at = conn.execute(
            f"SELECT simulated, created_at FROM {table}"
        ).fetchone()
    assert (simulated, created_at) == (1, "2026-11-20T12:00:00Z")


def test_insert_effect_rejects_unknown_type_and_columns(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        with pytest.raises(ValueError):
            store.insert_effect(conn, "wipe_device", {"proposal_id": "p1"})
        with pytest.raises(ValueError):
            store.insert_effect(
                conn,
                "replacement_flag",
                {
                    "proposal_id": "p1",
                    "company_id": "acme",
                    "device_id": "d",
                    "reason": "r",
                    "x) VALUES (1); --": "boom",
                },
            )


def test_effect_counts_per_table_and_company(tmp_path):
    store = make_store(tmp_path)
    flag = {"device_id": "d", "reason": "r"}
    with store.transaction() as conn:
        store.insert_effect(
            conn, "replacement_flag", {"proposal_id": "p1", "company_id": "acme", **flag}
        )
        store.insert_effect(
            conn, "replacement_flag", {"proposal_id": "p2", "company_id": "acme", **flag}
        )
        store.insert_effect(
            conn, "replacement_flag", {"proposal_id": "p3", "company_id": "globex", **flag}
        )
        counts = store.effect_counts(conn)
    assert counts["replacement_flags"] == {"acme": 2, "globex": 1}
    assert counts["tickets"] == {}
    assert set(counts) == {
        "tickets",
        "replacement_flags",
        "upgrade_orders",
        "employee_notifications",
    }


def test_payload_hash_is_stable_and_sensitive_to_every_field():
    base = payload_hash("acme", "remediation_ticket", "dev-1", {"a": "1", "b": "2"})
    assert base == payload_hash("acme", "remediation_ticket", "dev-1", {"b": "2", "a": "1"})
    assert len(base) == 64 and int(base, 16) >= 0
    variants = [
        payload_hash("globex", "remediation_ticket", "dev-1", {"a": "1", "b": "2"}),
        payload_hash("acme", "replacement_flag", "dev-1", {"a": "1", "b": "2"}),
        payload_hash("acme", "remediation_ticket", "dev-2", {"a": "1", "b": "2"}),
        payload_hash("acme", "remediation_ticket", "dev-1", {"a": "1", "b": "3"}),
        payload_hash("acme", "remediation_ticket", "dev-1", {"a": "1"}),
    ]
    assert len({base, *variants}) == 6


def test_audit_reads_are_tenant_scoped_and_operator_sees_all(tmp_path):
    store = make_store(tmp_path)
    with store.transaction() as conn:
        store.append_audit(
            conn,
            "proposal_created",
            company_id="acme",
            actor_id="demo-user",
            proposal_id="p1",
            trace_id="t1",
            action_type="remediation_ticket",
            target_device_id="dev-1",
            payload_hash="h1",
            refs=["dev-1#compliance[screen_lock]"],
            detail={"check_id": "screen_lock"},
        )
        store.append_audit(
            conn, "proposal_approved", company_id="acme", actor_id="admin-2", proposal_id="p1"
        )
        store.append_audit(
            conn,
            "proposal_created",
            company_id="globex",
            actor_id="demo-user",
            proposal_id="p9",
            target_device_id="dev-9",
        )
    with store.transaction() as conn:
        everything = store.audit(conn, None)
        mine = store.audit(conn, "acme")
        assert {e["company_id"] for e in everything} == {"acme", "globex"}
        assert {e["company_id"] for e in mine} == {"acme"} and len(mine) == 2
        assert [e["event"] for e in mine] == ["proposal_approved", "proposal_created"]
        created = mine[1]
        assert created["refs"] == ["dev-1#compliance[screen_lock]"]
        assert created["detail"] == {"check_id": "screen_lock"}
        assert created["at"] == "2026-11-20T12:00:00Z"
        assert store.audit(conn, "acme", proposal_id="p9") == []
        assert len(store.audit(conn, None, proposal_id="p9")) == 1
        assert len(store.audit(conn, "acme", target_device_id="dev-1")) == 1
        assert len(store.audit(conn, "acme", actor_id="admin-2")) == 1
        assert len(store.audit(conn, None, limit=2)) == 2


def test_transaction_rolls_back_on_exception(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            store.insert_proposal(conn, row())
            store.append_audit(conn, "proposal_created", company_id="acme", actor_id="demo-user")
            raise RuntimeError("boom")
    with store.transaction() as conn:
        assert store.get(conn, "acme", "p1") is None
        assert store.audit(conn, None) == []


def test_failed_effect_insert_rolls_back_the_whole_transaction(tmp_path):
    store = make_store(tmp_path)
    flag = {"proposal_id": "p1", "company_id": "acme", "device_id": "d", "reason": "r"}
    with store.transaction() as conn:
        store.insert_effect(conn, "replacement_flag", flag)
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            store.append_audit(conn, "proposal_executed", company_id="acme", actor_id="demo-user")
            store.insert_effect(conn, "replacement_flag", flag)
    with store.transaction() as conn:
        assert store.audit(conn, None) == []
