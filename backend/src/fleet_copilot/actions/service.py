"""Propose, approve, reject, execute and audit the four actions.

A proposal is created only from an eligibility result (code decides the parameters, evidence and wording). It
changes nothing until an admin approves it with the payload hash they saw. Approval re-checks eligibility on the
current data, then runs the simulated executor, sets the status and writes the audit entry in ONE transaction,
so a decision, its effect and its audit trail commit together or not at all.
"""

from __future__ import annotations

import hmac
import re
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fleet_copilot import policy
from fleet_copilot.actions import eligibility, executors
from fleet_copilot.actions.store import ActionStore, AuditEvent, ProposalRow, payload_hash
from fleet_copilot.agent.identity import Identity
from fleet_copilot.contracts import ActionType, ProposalCard, ProposalStatus
from fleet_copilot.data.loader import connect
from fleet_copilot.repository import FleetRepository, TenantContext, dataset_as_of

_CHECK_REF = re.compile(r"#compliance\[([A-Za-z0-9_.-]+)\]$")
_PLAIN_ARG = re.compile(r"[A-Za-z0-9_.-]{1,64}")

RefusalReason = Literal[
    "not_eligible", "insufficient_evidence", "not_found", "already_done", "too_many_pending"
]
TOO_MANY_PENDING = (
    "There are already {limit} proposals waiting for a decision. Approve or reject some first."
)
ALREADY_DONE = "This was already done on {day}: {action}. Nothing new was created."


@dataclass(frozen=True)
class Proposed:
    card: ProposalCard
    reused: bool


@dataclass(frozen=True)
class Refused:
    reason: RefusalReason
    detail: str


@dataclass(frozen=True)
class Decision:
    card: ProposalCard
    effect: dict[str, Any] | None
    duplicate: bool = False


class ProposalNotFound(Exception):
    """A foreign or unknown proposal id; the API answers 404 for both."""


class ProposalConflict(Exception):
    """`reason` is "hash_mismatch" or "not_pending:<status>"; the API answers 409."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _new_id() -> str:
    return "prp_" + secrets.token_hex(8)


def _card(row: ProposalRow) -> ProposalCard:
    """The stored card text with the row's CURRENT status (the stored JSON is not updated on decisions)."""
    return ProposalCard.model_validate({**row.card, "status": row.status})


def _params(eligible: eligibility.Eligible) -> dict[str, str]:
    """The hashed parameters. A failing_check notification also pins WHICH check, so the re-check asks the same."""
    params = dict(eligible.params)
    if eligible.action_type == "employee_notification" and params.get("topic") == "failing_check":
        match = _CHECK_REF.search(eligible.refs[0])
        if match:
            params["check_id"] = match.group(1)
    return params


def _recipient(card: dict[str, Any]) -> str | None:
    """The notification recipient the approver's hash covers: the `employee` the stored card names, if any."""
    scope = card.get("scope")
    employee = scope.get("employee") if isinstance(scope, dict) else None
    return employee if isinstance(employee, str) else None


def _requested_params(**args: str | None) -> dict[str, str]:
    """The requested arguments for an audit row: only plain enum-like values, never free text."""
    return {
        name: value if _PLAIN_ARG.fullmatch(value) else "<invalid>"
        for name, value in args.items()
        if isinstance(value, str)
    }


def _short_action(row: ProposalRow) -> str:
    """A fixed, code-built phrase for an executed proposal, from its enumerated parameters."""
    label = eligibility.display_hostname(
        str(row.card.get("target_label", "")), row.target_device_id
    )
    p = row.params
    if row.action_type == "remediation_ticket":
        return f"remediation ticket for {p.get('check_id')} on {label}"
    if row.action_type == "replacement_flag":
        return f"replacement flag for {label}"
    if row.action_type == "upgrade_order":
        return f"{p.get('component')} upgrade order for {label} ({p.get('current')} to {p.get('target')})"
    return f"{p.get('topic')} notification to the employee of {label}"


def _raise_if_refused(outcome: ProposalRow | Decision | Exception) -> None:
    if isinstance(outcome, Exception):
        raise outcome


class ActionService:
    def __init__(
        self,
        *,
        fleet_db: Path,
        store: ActionStore,
        new_id: Callable[[], str] = _new_id,
    ) -> None:
        self._fleet_db = fleet_db
        self._store = store
        self._new_id = new_id

    # ---- propose --------------------------------------------------------------------------------

    def propose(
        self,
        *,
        company_id: str,
        actor_id: str,
        action_type: ActionType,
        device_id: str,
        check_id: str | None = None,
        component: str | None = None,
        topic: str | None = None,
        as_of: datetime,
        conversation_id: str | None = None,
        trace_id: str | None = None,
    ) -> Proposed | Refused:
        result = self._check(
            company_id,
            actor_id,
            as_of,
            action_type,
            device_id,
            check_id=check_id,
            component=component,
            topic=topic,
        )
        requested = _requested_params(check_id=check_id, component=component, topic=topic)
        target = (company_id, actor_id, trace_id, action_type, device_id, requested)
        if isinstance(result, eligibility.Ineligible):
            with self._store.transaction() as conn:
                return self._refuse_proposal(conn, target, result.reason, result.detail)

        params = _params(result)
        digest = payload_hash(
            company_id,
            action_type,
            device_id,
            params,
            employee_id=result.employee_id,
            rationale=result.rationale,
        )
        with self._store.transaction() as conn:
            existing = self._store.find_pending(conn, company_id, digest)
            if existing is not None:
                return Proposed(_card(existing), reused=True)
            done = self._store.find_executed(
                conn,
                company_id,
                action_type,
                device_id,
                params,
                self._store.now() - timedelta(days=policy.ACTION_REPEAT_WINDOW_DAYS),
            )
            if done is not None:
                day = (done.decided_at or done.created_at or "")[:10]
                detail = ALREADY_DONE.format(day=day, action=_short_action(done))
                return self._refuse_proposal(conn, target, "already_done", detail)
            if self._store.count_pending(conn, company_id) >= policy.MAX_PENDING_PER_TENANT:
                detail = TOO_MANY_PENDING.format(limit=policy.MAX_PENDING_PER_TENANT)
                return self._refuse_proposal(conn, target, "too_many_pending", detail)
            card = ProposalCard(
                proposal_id=self._new_id(),
                action_type=action_type,
                company_id=company_id,
                target_device_id=device_id,
                target_label=result.target_label,
                rationale=result.rationale,
                evidence_refs=list(result.refs),
                scope={
                    "device": device_id,
                    **params,
                    **({"employee": result.employee_id} if result.employee_id else {}),
                },
                expected_effect=result.expected_effect,
                status="pending",
                payload_hash=digest,
            )
            self._store.insert_proposal(
                conn,
                ProposalRow(
                    proposal_id=card.proposal_id,
                    company_id=company_id,
                    actor_id=actor_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    action_type=action_type,
                    target_device_id=device_id,
                    params=params,
                    payload_hash=digest,
                    evidence_refs=list(result.refs),
                    card=card.model_dump(mode="json"),
                ),
            )
            self._store.append_audit(
                conn,
                "proposal_created",
                company_id=company_id,
                actor_id=actor_id,
                proposal_id=card.proposal_id,
                trace_id=trace_id,
                action_type=action_type,
                target_device_id=device_id,
                payload_hash=digest,
                refs=result.refs,
            )
        return Proposed(card, reused=False)

    def _refuse_proposal(
        self,
        conn: sqlite3.Connection,
        target: tuple[str, str, str | None, str, str, dict[str, str]],
        reason: RefusalReason,
        detail: str,
    ) -> Refused:
        """Audit a refused proposal (what was asked for and why not) and return it."""
        company_id, actor_id, trace_id, action_type, device_id, requested = target
        self._store.append_audit(
            conn,
            "proposal_refused",
            company_id=company_id,
            actor_id=actor_id,
            trace_id=trace_id,
            action_type=action_type,
            target_device_id=device_id,
            detail={"reason": reason, "params": requested},
        )
        return Refused(reason, detail)

    def _check(
        self,
        company_id: str,
        actor_id: str,
        as_of: datetime | None,
        action_type: ActionType,
        device_id: str,
        *,
        check_id: str | None,
        component: str | None,
        topic: str | None,
    ) -> eligibility.Eligible | eligibility.Ineligible:
        """Eligibility on the tenant's data. `as_of=None` pins to the current database's own as-of."""
        with closing(connect(self._fleet_db, read_only=True)) as fleet:
            repo = FleetRepository(fleet, TenantContext(company_id, actor_id))
            return eligibility.check(
                repo,
                as_of if as_of is not None else dataset_as_of(fleet),
                action_type,
                device_id,
                check_id=check_id,
                component=component,
                topic=topic,
            )

    # ---- decide ---------------------------------------------------------------------------------

    def approve(self, identity: Identity, proposal_id: str, payload_hash: str) -> Decision:
        with self._store.transaction() as conn:
            row = self._decidable(conn, identity, proposal_id, payload_hash)
            if isinstance(row, ProposalRow):
                return self._execute(conn, identity, row)
        _raise_if_refused(row)
        assert isinstance(row, Decision)
        return row

    def reject(self, identity: Identity, proposal_id: str) -> Decision:
        with self._store.transaction() as conn:
            row = self._decidable(conn, identity, proposal_id, None)
            if isinstance(row, ProposalRow):
                self._store.set_status(
                    conn, identity.company_id, proposal_id, "rejected", identity.actor_id, None
                )
                self._audit(conn, "proposal_rejected", identity, row)
                return Decision(self._card_now(conn, identity, proposal_id), None)
        _raise_if_refused(row)
        raise AssertionError("a rejection cannot be a duplicate")

    def _decidable(
        self,
        conn: sqlite3.Connection,
        identity: Identity,
        proposal_id: str,
        supplied_hash: str | None,
    ) -> ProposalRow | Decision | Exception:
        """The pending proposal this admin may decide, or the outcome that ends the request.

        A refusal is audited here and RETURNED, not raised: the caller raises it after the transaction has
        committed, because an exception inside the transaction would roll the audit entry back.
        """
        approving = supplied_hash is not None
        if identity.role != "admin":
            self._refuse(conn, identity, proposal_id, "forbidden", approving)
            return PermissionError("only an admin may decide a proposal")
        row = self._store.get(conn, identity.company_id, proposal_id)
        if row is None:
            self._refuse(conn, identity, proposal_id, "not_found", approving)
            return ProposalNotFound(proposal_id)
        if row.status == "executed" and approving:
            self._audit(conn, "duplicate_approval", identity, row)
            return Decision(_card(row), None, duplicate=True)
        if row.status != "pending":
            self._refuse(conn, identity, proposal_id, f"not_pending:{row.status}", approving, row)
            return ProposalConflict(f"not_pending:{row.status}")
        if supplied_hash is not None and not hmac.compare_digest(
            supplied_hash.encode("utf-8"), row.payload_hash.encode("utf-8")
        ):
            self._refuse(conn, identity, proposal_id, "hash_mismatch", approving, row)
            return ProposalConflict("hash_mismatch")
        return row

    def _refuse(
        self,
        conn: sqlite3.Connection,
        identity: Identity,
        proposal_id: str,
        reason: str,
        approving: bool,
        row: ProposalRow | None = None,
    ) -> None:
        self._store.append_audit(
            conn,
            "approval_refused",
            company_id=identity.company_id,
            actor_id=identity.actor_id,
            proposal_id=proposal_id,
            action_type=row.action_type if row else None,
            target_device_id=row.target_device_id if row else None,
            payload_hash=row.payload_hash if row else None,
            detail={"reason": reason, "operation": "approve" if approving else "reject"},
        )

    def _execute(self, conn: sqlite3.Connection, identity: Identity, row: ProposalRow) -> Decision:
        self._audit(conn, "proposal_approved", identity, row)
        current = self._check(
            row.company_id,
            identity.actor_id,
            None,
            row.action_type,  # type: ignore[arg-type]
            row.target_device_id,
            check_id=row.params.get("check_id"),
            component=row.params.get("component"),
            topic=row.params.get("topic"),
        )
        if isinstance(current, eligibility.Ineligible):
            return self._conclude(
                conn, identity, row, "invalidated", "proposal_invalidated", current.reason
            )
        if current.employee_id != _recipient(row.card):
            return self._conclude(
                conn, identity, row, "invalidated", "proposal_invalidated", "recipient_changed"
            )
        if (
            _params(current) != row.params
            or payload_hash(
                row.company_id,
                row.action_type,
                row.target_device_id,
                _params(current),
                employee_id=current.employee_id,
                rationale=str(row.card.get("rationale", "")),
            )
            != row.payload_hash
        ):
            return self._conclude(
                conn, identity, row, "invalidated", "proposal_invalidated", "changed"
            )
        conn.execute("SAVEPOINT executor")
        try:
            effect = executors.execute(self._store, conn, row, current)
        except Exception as exc:
            conn.execute("ROLLBACK TO executor")
            conn.execute("RELEASE executor")
            name = type(exc).__name__
            return self._conclude(
                conn, identity, row, "failed", "proposal_failed", None, {"error": name}, name
            )
        conn.execute("RELEASE executor")
        self._store.set_status(
            conn, row.company_id, row.proposal_id, "executed", identity.actor_id, None
        )
        self._audit(conn, "proposal_executed", identity, row, {"effect_id": effect["effect_id"]})
        return Decision(self._card_now(conn, identity, row.proposal_id), effect)

    def _conclude(
        self,
        conn: sqlite3.Connection,
        identity: Identity,
        row: ProposalRow,
        status: ProposalStatus,
        event: AuditEvent,
        reason: str | None,
        detail: dict[str, Any] | None = None,
        stored_detail: str | None = None,
    ) -> Decision:
        self._store.set_status(
            conn,
            row.company_id,
            row.proposal_id,
            status,
            identity.actor_id,
            stored_detail or reason,
        )
        self._audit(conn, event, identity, row, detail or {"reason": reason})
        return Decision(self._card_now(conn, identity, row.proposal_id), None)

    def _audit(
        self,
        conn: sqlite3.Connection,
        event: AuditEvent,
        identity: Identity,
        row: ProposalRow,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._store.append_audit(
            conn,
            event,
            company_id=row.company_id,
            actor_id=identity.actor_id,
            proposal_id=row.proposal_id,
            trace_id=row.trace_id,
            action_type=row.action_type,
            target_device_id=row.target_device_id,
            payload_hash=row.payload_hash,
            refs=row.evidence_refs,
            detail=detail,
        )

    def _card_now(
        self, conn: sqlite3.Connection, identity: Identity, proposal_id: str
    ) -> ProposalCard:
        row = self._store.get(conn, identity.company_id, proposal_id)
        assert row is not None
        return _card(row)

    # ---- reads ----------------------------------------------------------------------------------

    def get(self, identity: Identity, proposal_id: str) -> ProposalCard:
        with self._store.transaction() as conn:
            row = self._store.get(conn, identity.company_id, proposal_id)
        if row is None:
            raise ProposalNotFound(proposal_id)
        return _card(row)

    def list(self, identity: Identity, status: ProposalStatus | None = None) -> list[ProposalCard]:
        with self._store.transaction() as conn:
            return [_card(r) for r in self._store.list(conn, identity.company_id, status)]

    def audit(
        self,
        identity: Identity,
        *,
        proposal_id: str | None = None,
        target_device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._store.transaction() as conn:
            return self._store.audit(
                conn,
                identity.company_id,
                proposal_id=proposal_id,
                target_device_id=target_device_id,
            )

    def audit_all(
        self,
        *,
        company_id: str | None = None,
        actor_id: str | None = None,
        target_device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Operator read across tenants; the API allows it only to the operator identity."""
        with self._store.transaction() as conn:
            rows = self._store.audit(
                conn, None, actor_id=actor_id, target_device_id=target_device_id
            )
        return [r for r in rows if company_id is None or r["company_id"] == company_id]

    def effects(self) -> dict[str, dict[str, int]]:
        with self._store.transaction() as conn:
            return self._store.effect_counts(conn)
