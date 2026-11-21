from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from fleet_copilot import policy
from fleet_copilot.actions.eligibility import display_hostname
from fleet_copilot.agent import texts
from fleet_copilot.agent.format import fmt_bytes, fmt_day, fmt_pct, fmt_signed_pct, fmt_ts, noun
from fleet_copilot.agent.present import (
    GENERIC_EXCLUDED_TEXT,
    ScopeParts,
    present,
    representative,
)
from fleet_copilot.agent.records import FindingRecord, ToolRecord
from fleet_copilot.contracts import (
    MAX_EXCLUDED_DEVICES,
    Alternative,
    AnswerEnvelope,
    AnswerTable,
    Claim,
    Coverage,
    EntityList,
    EntityRef,
    EvidenceItem,
    ExcludedDevice,
    OutcomeType,
    ProposalCard,
    ReasonCode,
    ScopeInfo,
    ScopeLine,
    SuggestedAction,
)
from fleet_copilot.domain.metrics import free_pct, used_pct
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import EvidenceValue

Resolver = Callable[[EvidenceRef], EvidenceValue]

_CLASS_ORDER = ("sustained", "onset", "intermittent", "recovering", "isolated")
_CLASS_TEXT = {
    "sustained": "breached in at least 80% of the window's snapshots and still breached at the latest",
    "onset": "began inside the window and is still breaching",
    "intermittent": "breached on and off, with no sustained run",
    "recovering": "was breached for most of the window but is back within limits at the latest snapshot",
    "isolated": "isolated spikes, under 20% of snapshots",
}
_KIND_TEXT = {
    "persistent_failure": "failed in every snapshot in the window",
    "transition": "changed from pass to fail inside the window",
    "recovered": "changed from fail back to pass inside the window",
}
_REASON_TEXT = {
    "out_of_scope": "{n} {dev} {are} out of scope for this check",
    "unknown_version": "{n} {dev} report an unrecognized OS version and {are} counted separately",
    "insufficient_history": "{n} {dev} {have} fewer than 3 snapshots in the window, so no trend is reported",
    "no_reading": "{n} {dev} {have} no battery reading in the window",
    "no_readings": "{n} {dev} {have} no readings for that metric in the window",
}
_PATH_TITLES = {
    "disk": "Disk",
    "memory": "Memory",
    "os": "OS version",
    "battery": "Battery",
    "compliance": "Compliance",
    "software": "Software",
}


def max_refs_per_device(claims: Sequence[Claim]) -> int:
    """The most distinct readings the claims cite for any one device (what the scope text states)."""
    per_device: dict[str, set[str]] = {}
    for claim in claims:
        for ref in claim.refs:
            per_device.setdefault(EvidenceRef.parse(ref).device_id, set()).add(ref)
    return max((len(refs) for refs in per_device.values()), default=0)


@dataclass
class Composition:
    outcome: OutcomeType
    reason_code: ReasonCode | None
    claims: list[Claim] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: Coverage | None = None
    entity_lists: list[EntityList] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fallback_headline: str = ""
    rules: list[str] = field(default_factory=list)
    proposal: ProposalCard | None = None
    suggested_actions: list[SuggestedAction] = field(default_factory=list)
    # Presentation for the UI. None of it is a fact the model reads.
    table: AnswerTable | None = None
    summary: str = ""
    scope_parts: ScopeParts | None = None
    follow_ups: list[Alternative] = field(default_factory=list)

    def facts(self) -> dict:
        """Everything the model may read when asked to explain: code-written text only."""
        facts: dict = {
            "claims": [c.text for c in self.claims],
            "notes": list(self.notes),
            "population": self.coverage.population if self.coverage else None,
            "rules": list(self.rules),
        }
        if self.suggested_actions:
            facts["suggested_actions"] = [a.label for a in self.suggested_actions]
        return facts

    def to_envelope(
        self,
        *,
        company_id: str,
        as_of: datetime,
        trace_id: str,
        headline: str | None = None,
        explanation: str | None = None,
        company_name: str = "",
    ) -> AnswerEnvelope:
        distinct = len({device_id for c in self.claims for device_id in c.device_ids})
        cited = max_refs_per_device(self.claims)
        scope = ScopeInfo(
            company_id=company_id,
            as_of=as_of,
            rule="; ".join(self.rules) or None,
            population=self.coverage.population if self.coverage else None,
            calculation=(
                f"{distinct} distinct {noun(distinct, 'device')} across {len(self.claims)} "
                f"{noun(len(self.claims), 'claim')}; at most {cited} "
                f"supporting {noun(cited, 'reading')} {'is' if cited == 1 else 'are'} cited per device"
                if self.claims
                else None
            ),
        )
        parts = self.scope_parts
        scope_line = (
            ScopeLine(
                checked=parts.checked,
                matched=parts.matched,
                ok=parts.ok,
                ok_label=parts.ok_label,
                not_checked=parts.not_checked,
                company_name=company_name,
                basis=parts.basis,
                as_of=as_of,
            )
            if parts is not None
            else None
        )
        return AnswerEnvelope(
            outcome=self.outcome,
            trace_id=trace_id,
            headline=headline or self.fallback_headline,
            claims=self.claims,
            evidence=self.evidence,
            proposal=self.proposal,
            suggested_actions=self.suggested_actions,
            scope=scope,
            coverage=self.coverage,
            # One list id per turn: the raw ids repeat every turn, so follow-ups could not tell lists apart.
            entity_lists=[
                entity_list.model_copy(update={"list_id": f"{trace_id}/{entity_list.list_id}"})
                for entity_list in self.entity_lists
            ],
            reason_code=self.reason_code,
            explanation=explanation
            if self.outcome not in ("denied", "unsupported", "tool_error")
            else None,
            notes=self.notes,
            table=self.table,
            summary=self.summary,
            scope_line=scope_line,
            follow_ups=self.follow_ups,
        )


def evidence_text(path: str, value: dict) -> str:
    """The one-line reading shown beside an evidence ref. The verify gate recomputes it from the data."""
    if path == "disk":
        free = free_pct(value["size_bytes"], value["available_bytes"])
        return f"{fmt_bytes(value['available_bytes'])} free of {fmt_bytes(value['size_bytes'])} ({fmt_pct(free)} free)"
    if path == "memory":
        used = used_pct(value["total_bytes"], value["used_bytes"])
        return f"{fmt_pct(used)} used ({fmt_bytes(value['used_bytes'])} of {fmt_bytes(value['total_bytes'])})"
    if path == "os":
        return f"{value['os_name']} {value['os_version']}"
    if path == "battery":
        capacity = value["full_charge_capacity"]
        return (
            f"condition {value['condition']}; {value['cycle_count']} cycles; "
            f"full-charge capacity {capacity} (unit not reported)"
        )
    if path == "software":
        return f"{value['name']} {value['version']}, published by {value['publisher']}"
    return f"{value['check_id']}: {value['status']} ({value['severity']} severity)"


def evidence_kind(path: str) -> str:
    """The reading type behind an evidence path: `compliance[x]` and `software[x]` are one kind each."""
    for kind in ("compliance", "software"):
        if path.startswith(f"{kind}["):
            return kind
    return path


def _path_title(path: str) -> str:
    return _PATH_TITLES.get(evidence_kind(path), path)


def _item(finding: FindingRecord, detail: str) -> str:
    return f"{finding.device_id} ({finding.hostname}; {detail})"


class _Builder:
    def __init__(self, resolve: Resolver) -> None:
        self._resolve = resolve
        self._hosts: dict[str, str] = {}
        self.claims: list[Claim] = []
        self.evidence: dict[str, EvidenceItem] = {}
        self.lists: list[EntityList] = []
        self.notes: list[str] = []
        self.rules: list[str] = []
        self.headlines: list[str] = []
        self.populations: list[tuple[str, int]] = []
        self.stale: set[str] = set()
        self.missing: set[str] = set()
        self.excluded: dict[str, set[str]] = {}
        self.excluded_text: dict[tuple[str, str], tuple[str, str]] = {}

    def coverage(self, rec: ToolRecord) -> None:
        for f in rec.findings:
            self._hosts[f.device_id] = f.hostname
        self.populations.append((rec.tool, rec.population))
        if rec.rule and rec.rule not in self.rules:
            self.rules.append(rec.rule)
        self.stale.update(rec.stale)
        self.missing.update(rec.missing_latest)
        for reason, ids in rec.excluded.items():
            self.excluded.setdefault(reason, set()).update(ids)
        for reason, found in rec.excluded_detail.items():
            for device_id, info in found.items():
                self.excluded_text[(reason, device_id)] = (info.hostname, info.text)

    def set_headline(self, text: str) -> None:
        if text not in self.headlines:
            self.headlines.append(text)

    def add_note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    @property
    def population(self) -> int:
        return max((n for _, n in self.populations), default=0)

    def claim(
        self,
        text: str,
        findings: Sequence[FindingRecord],
        refs_by_device: dict[str, list[str]] | None = None,
    ) -> None:
        refs: list[str] = []
        for f in findings:
            refs.extend(refs_by_device[f.device_id] if refs_by_device else representative(f.refs))
        refs = list(dict.fromkeys(refs))
        for ref in refs:
            self.add_evidence(ref)
        ids = [f.device_id for f in findings]
        self.claims.append(Claim(text=text, refs=refs, count=len(ids), device_ids=ids))

    def entity_list(self, list_id: str, title: str, findings: Sequence[FindingRecord]) -> None:
        items = [
            EntityRef(rank=i, device_id=f.device_id, label=f.hostname)
            for i, f in enumerate(findings, 1)
        ]
        self.lists.append(EntityList(list_id=list_id, title=title, items=items))

    def proposal_evidence(self, card: ProposalCard) -> list[EvidenceItem]:
        """Evidence items for the refs a proposal cites, labelled with the target's hostname."""
        self._hosts[card.target_device_id] = card.target_label
        for ref in card.evidence_refs:
            self.add_evidence(ref)
        return list(self.evidence.values())

    def add_evidence(self, ref: str) -> None:
        if ref in self.evidence:
            return
        parsed = EvidenceRef.parse(ref)
        resolved = self._resolve(parsed)
        host = self._hosts.get(parsed.device_id, parsed.device_id)
        kind = evidence_kind(parsed.path)
        self.evidence[ref] = EvidenceItem(
            ref=ref,
            label=f"{_path_title(parsed.path)} · {host} · {fmt_ts(parsed.collected_at)}",
            value=evidence_text(kind, resolved.value),
        )

    def excluded_devices(self) -> list[ExcludedDevice]:
        """Each excluded device once per reason, with its hostname and a code-written reason."""
        out = []
        for reason, ids in self.excluded.items():
            for device_id in ids:
                hostname, text = self.excluded_text.get(
                    (reason, device_id),
                    (device_id, GENERIC_EXCLUDED_TEXT.get(reason, "Not checked")),
                )
                out.append(
                    ExcludedDevice(
                        device_id=device_id,
                        label=display_hostname(hostname, device_id),
                        reason=reason,  # type: ignore[arg-type]
                        reason_text=text,
                    )
                )
        out.sort(key=lambda e: (e.reason, e.label, e.device_id))
        return out[:MAX_EXCLUDED_DEVICES]

    def disclosure_notes(self) -> None:
        if len({n for _, n in self.populations}) > 1:
            listing = ", ".join(f"{n} ({tool})" for tool, n in self.populations)
            self.notes.append(f"Populations differ per check: {listing}.")
        for reason, ids in sorted(self.excluded.items()):
            template = _REASON_TEXT.get(reason)
            if template and ids:
                n = len(ids)
                self.notes.append(
                    template.format(
                        n=n,
                        dev=noun(n, "device"),
                        are="is" if n == 1 else "are",
                        have="has" if n == 1 else "have",
                    )
                    + f": {', '.join(sorted(ids)[:10])}."
                )
        if self.stale:
            self.notes.append(
                f"{len(self.stale)} {noun(len(self.stale), 'device')} had readings older than "
                f"{policy.STALE_HOURS} hours before the as-of time: {', '.join(sorted(self.stale)[:10])}."
            )
        # A laptop with no reading anywhere in the window is already disclosed as `no_reading`.
        fell_back = self.missing - self.excluded.get("no_reading", set())
        if fell_back:
            self.notes.append(
                f"{len(fell_back)} {noun(len(fell_back), 'laptop')} had no battery reading in the latest "
                f"snapshot (the newest earlier reading was used): {', '.join(sorted(fell_back)[:10])}."
            )


# ---- templates: one per tool ------------------------------------------------------------


def _low_disk(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    thr = rec.args.get("threshold_pct") or policy.LOW_DISK_FREE_PCT
    items = [
        _item(
            f,
            f"{fmt_pct(f.values['free_pct'])} free, "
            f"{fmt_bytes(f.values['available_bytes'])} of {fmt_bytes(f.values['size_bytes'])}",
        )
        for f in rec.findings
    ]
    k, n = len(rec.findings), rec.population
    b.claim(
        f"Low disk space (under {thr:g}% free on the primary volume, latest snapshot): "
        f"{k} of {n} {noun(n, 'device')}. " + "; ".join(items) + ".",
        rec.findings,
    )
    b.entity_list(f"{rec.tool}-{idx}", f"Low disk (<{thr:g}% free)", rec.findings)
    b.set_headline(f"{k} of {n} {noun(n, 'device')} have under {thr:g}% disk space free")


def _macos(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    target = rec.args.get("older_than") or "15"
    items = [_item(f, f"macOS {f.values['os_version']}") for f in rec.findings]
    k, n = len(rec.findings), rec.population
    b.claim(
        f"macOS older than {target}: {k} of {n} macOS {noun(n, 'device')}. "
        + "; ".join(items)
        + ".",
        rec.findings,
    )
    b.entity_list(f"{rec.tool}-{idx}", f"macOS older than {target}", rec.findings)
    b.set_headline(f"{k} of {n} macOS {noun(n, 'device')} run a version older than macOS {target}")


def _compliance_now(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    by_check: dict[str, list[tuple[FindingRecord, str, str]]] = {}
    for f in rec.findings:
        for failing, ref in zip(f.values["failing"], f.refs, strict=True):
            by_check.setdefault(failing["check_id"], []).append((f, failing["severity"], ref))
    scope_noun = "laptops" if rec.args.get("form_factor") == "laptop" else "devices"
    for check in sorted(by_check):
        rows = by_check[check]
        findings = [row[0] for row in rows]
        severity = "/".join(sorted({row[1] for row in rows}))
        items = [f"{f.device_id} ({f.hostname})" for f in findings]
        b.claim(
            f"Failing {check} ({severity} severity) at the latest snapshot: {len(findings)} of {rec.population} "
            f"{scope_noun}. " + "; ".join(items) + ".",
            findings,
            refs_by_device={f.device_id: [ref] for f, _, ref in rows},
        )
        b.entity_list(f"{rec.tool}-{idx}-{check}", f"{check} failures", findings)
    if len(by_check) == 1:
        check = next(iter(by_check))
        b.set_headline(f"{len(by_check[check])} of {rec.population} {scope_noun} fail {check}")
    else:
        b.set_headline(
            f"{len(rec.findings)} of {rec.population} {scope_noun} fail at least one compliance check"
        )


def _memory_now(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    thr = rec.args.get("threshold_pct") or policy.MEMORY_USED_PCT
    items = [
        _item(
            f,
            f"{fmt_pct(f.values['used_pct'])} used, "
            f"{fmt_bytes(f.values['used_bytes'])} of {fmt_bytes(f.values['total_bytes'])}",
        )
        for f in rec.findings
    ]
    k, n = len(rec.findings), rec.population
    b.claim(
        f"High memory use (at least {thr:g}% used, latest snapshot): {k} of {n} {noun(n, 'device')}. "
        + "; ".join(items)
        + ".",
        rec.findings,
    )
    b.entity_list(f"{rec.tool}-{idx}", f"High memory (>={thr:g}% used)", rec.findings)
    b.set_headline(f"{k} of {n} {noun(n, 'device')} use at least {thr:g}% of memory")


def _trend(rec: ToolRecord, idx: int, b: _Builder) -> None:
    metric = rec.args.get("metric")
    if metric in ("memory", "disk"):
        _trend_breach(rec, idx, b, metric)
    elif metric == "compliance":
        _trend_compliance(rec, idx, b)
    else:
        _trend_battery(rec, idx, b)


def _trend_breach(rec: ToolRecord, idx: int, b: _Builder, metric: str) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    days = rec.args.get("window_days") or policy.TREND_WINDOW_DAYS
    if metric == "memory":
        thr = rec.args.get("threshold_pct") or policy.MEMORY_USED_PCT
        what, latest_label = f"memory use at or above {thr:g}%", "used"
    else:
        thr = rec.args.get("threshold_pct") or policy.LOW_DISK_FREE_PCT
        what, latest_label = f"free disk space under {thr:g}%", "free"
    groups: dict[str, list[FindingRecord]] = {}
    for f in rec.findings:
        groups.setdefault(f.values["trend"], []).append(f)
    summary = []
    for cls in _CLASS_ORDER:
        group = groups.get(cls)
        if not group:
            continue
        items = [
            _item(
                f,
                f"breached in {f.values['breached']} of {f.values['snapshots']} snapshots; "
                f"latest {fmt_pct(f.values['latest_value'])} {latest_label}",
            )
            for f in group
        ]
        b.claim(
            f"{cls.capitalize()} ({_CLASS_TEXT[cls]}), {what} over the last {days} days: {len(group)} of "
            f"{rec.population} {noun(rec.population, 'device')}. " + "; ".join(items) + ".",
            group,
        )
        b.entity_list(f"{rec.tool}-{idx}-{cls}", f"{cls} {metric} ({days} d)", group)
        summary.append(f"{len(group)} {cls}")
    b.set_headline(
        f"{len(rec.findings)} of {rec.population} devices breached {what} in the last {days} days "
        f"({', '.join(summary)})"
    )


def _trend_compliance(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    check = rec.args.get("check_id", "the check")
    days = rec.args.get("window_days") or policy.COMPLIANCE_WINDOW_DAYS
    groups: dict[str, list[FindingRecord]] = {}
    for f in rec.findings:
        groups.setdefault(f.values["kind"], []).append(f)
    summary = []
    for kind in ("persistent_failure", "transition", "recovered"):
        group = groups.get(kind)
        if not group:
            continue
        if kind == "persistent_failure":
            items = [
                _item(f, f"failing in {f.values['snapshots']} of {f.values['snapshots']} snapshots")
                for f in group
            ]
        else:
            since = "failing since" if kind == "transition" else "passing since"
            items = [_item(f, f"{since} {fmt_ts(f.values['change_at'])}") for f in group]
        b.claim(
            f"{check} over the last {days} days: {len(group)} of {rec.population} {noun(rec.population, 'device')} "
            f"{_KIND_TEXT[kind]}. " + "; ".join(items) + ".",
            group,
        )
        b.entity_list(f"{rec.tool}-{idx}-{kind}", f"{check} {kind.replace('_', ' ')}", group)
        summary.append(f"{len(group)} {kind.replace('_', ' ')}")
    b.set_headline(
        f"{len(rec.findings)} of {rec.population} devices have a {check} change or failure ({', '.join(summary)})"
    )


def _trend_battery(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    days = rec.args.get("window_days") or policy.BATTERY_WINDOW_DAYS
    b.add_note(
        "The telemetry has no design capacity, so a battery health percentage cannot be computed; capacity change "
        "is relative to each device's own first reading in the window."
    )
    if not rec.findings:
        return
    items = []
    for f in rec.findings:
        v = f.values
        parts = []
        if v["capacity_change_pct"] is not None:
            parts.append(
                f"full-charge capacity {fmt_signed_pct(v['capacity_change_pct'])} vs its first reading in the window"
            )
        if v["cycles_latest"] is not None:
            parts.append(f"{v['cycles_latest']} cycles")
        if v["service_streak"]:
            parts.append(
                f"vendor condition 'Service Recommended' since {fmt_day(v['first_service_at'])}"
            )
        items.append(_item(f, ", ".join(parts)))
    k, n = len(rec.findings), rec.population
    b.claim(
        f"Battery wear signals over the last {days} days: {k} of {n} {noun(n, 'laptop')}. "
        + "; ".join(items)
        + ".",
        rec.findings,
    )
    b.entity_list(f"{rec.tool}-{idx}-battery", f"Battery wear ({days} d)", rec.findings)
    b.set_headline(
        f"{k} of {n} {noun(n, 'laptop')} show battery wear signals over the last {days} days"
    )


def _find(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    group_by = rec.args.get("group_by")
    k, n = len(rec.findings), rec.population
    if group_by:
        key = {
            "os": lambda f: f"{f.values['os_name']} {f.values['os_version']}",
            "model": lambda f: f.values["model_name"],
            "form_factor": lambda f: f.values["form_factor"],
        }[group_by]
        counts = Counter(key(f) for f in rec.findings)
        listing = "; ".join(f"{label}: {count}" for label, count in sorted(counts.items()))
        b.claim(
            f"Devices by {group_by.replace('_', ' ')}: {k} of {n} {noun(n, 'device')}. {listing}.",
            rec.findings,
        )
        b.set_headline(f"{k} {noun(k, 'device')} grouped by {group_by.replace('_', ' ')}")
    else:
        items = [
            _item(f, f"{f.values['os_name']} {f.values['os_version']}; {f.values['model_name']}")
            for f in rec.findings
        ]
        b.claim(
            f"Matching devices: {k} of {n} {noun(n, 'device')}. " + "; ".join(items) + ".",
            rec.findings,
        )
        b.set_headline(f"{k} of {n} {noun(n, 'device')} match")
    b.entity_list(f"{rec.tool}-{idx}", "Matching devices", rec.findings)


def _history(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    f = rec.findings[0]
    v = f.values
    points = v["points"]
    first, last = points[0], points[-1]
    span = f"over {v['snapshots']} snapshots from {fmt_ts(first['at'])} to {fmt_ts(last['at'])}"
    metric = v["metric"]
    if metric == "compliance":
        counts = Counter(p["value"] for p in points)
        detail = f"pass in {counts.get('pass', 0)} and fail in {counts.get('fail', 0)} snapshots; latest {last['value']}"
        label = f"Compliance {v['check_id']}"
    else:
        values = [p["value"] for p in points]
        if metric in ("disk_free_pct", "memory_used_pct"):
            fmt = fmt_pct
            label = "Free disk space" if metric == "disk_free_pct" else "Memory use"
        else:
            fmt = str
            label = "Full-charge capacity (unit not reported)"
        detail = f"first {fmt(values[0])}, latest {fmt(values[-1])}, lowest {fmt(min(values))}, highest {fmt(max(values))}"
    b.claim(f"{label} for {f.device_id} ({f.hostname}) {span}: {detail}.", [f])
    b.entity_list(f"{rec.tool}-{idx}", "Device", [f])
    b.set_headline(f"{label} for {f.hostname}: {detail}")


def _software(rec: ToolRecord, idx: int, b: _Builder) -> None:
    b.coverage(rec)
    if not rec.findings:
        return
    if rec.args.get("view") == "new_installs":
        _new_installs(rec, idx, b)
    elif "apps" in rec.findings[0].values:
        _software_inventory(rec, idx, b)
    else:
        _software_holders(rec, idx, b)


def _new_installs(rec: ToolRecord, idx: int, b: _Builder) -> None:
    days = rec.args.get("window_days") or policy.SOFTWARE_WINDOW_DAYS
    by_app: dict[str, list[FindingRecord]] = {}
    for f in rec.findings:
        by_app.setdefault(f.values["app"], []).append(f)
    for app in sorted(by_app, key=lambda name: (name.lower(), name)):
        group = by_app[app]
        items = []
        for f in group:
            v = f.values
            version = (
                v["version"]
                if v["version"] == v["latest_version"]
                else f"{v['version']}, now {v['latest_version']}"
            )
            detail = f"{app} {version}; first seen {fmt_ts(v['first_seen'])}"
            if not v["still_present"]:
                detail += f"; no longer installed, last seen {fmt_ts(v['last_seen'])}"
            items.append(_item(f, detail))
        n = rec.population
        b.claim(
            f"Newly installed in the last {days} days and not in the common set: {app} on "
            f"{len(group)} of {n} {noun(n, 'device')}. " + "; ".join(items) + ".",
            group,
            refs_by_device={f.device_id: list(f.refs) for f in group},
        )
    devices = list({f.device_id: f for f in rec.findings}.values())  # newest install first
    b.entity_list(f"{rec.tool}-{idx}", f"New software ({days} d)", devices)
    k, n = len(rec.findings), rec.population
    b.set_headline(
        f"{k} new {noun(k, 'install')} on {len(devices)} of {n} {noun(n, 'device')} in the last "
        f"{days} days (not in the common set)"
    )


def _software_holders(rec: ToolRecord, idx: int, b: _Builder) -> None:
    name = rec.findings[0].values["name"]
    items = [_item(f, f"{f.values['name']} {f.values['version']}") for f in rec.findings]
    k, n = len(rec.findings), rec.population
    b.claim(
        f"{name} is installed at the latest snapshot on {k} of {n} {noun(n, 'device')}. "
        + "; ".join(items)
        + ".",
        rec.findings,
    )
    b.entity_list(f"{rec.tool}-{idx}", f"Devices with {name}", rec.findings)
    b.set_headline(f"{name} is installed on {k} of {n} {noun(n, 'device')}")


def _software_inventory(rec: ToolRecord, idx: int, b: _Builder) -> None:
    items = []
    for f in rec.findings:
        count = len(f.values["apps"])
        detail = f"{count} {noun(count, 'app')}"
        if f.values["extra"]:
            extra = ", ".join(f"{a['name']} {a['version']}" for a in f.values["extra"])
            detail += f"; not on every device: {extra}"
        items.append(_item(f, detail))
    k, n = len(rec.findings), rec.population
    b.claim(
        f"Installed software at the latest snapshot on {k} of {n} {noun(n, 'device')}. "
        + "; ".join(items)
        + ".",
        rec.findings,
        refs_by_device={f.device_id: list(f.refs) for f in rec.findings},
    )
    b.entity_list(f"{rec.tool}-{idx}", "Installed software", rec.findings)
    b.set_headline(f"{k} of {n} {noun(n, 'device')} report installed software")


_TEMPLATES: dict[str, Callable[[ToolRecord, int, _Builder], None]] = {
    "low_disk_devices": _low_disk,
    "macos_version_check": _macos,
    "compliance_failures_now": _compliance_now,
    "memory_pressure_now": _memory_now,
    "trend": _trend,
    "find_devices": _find,
    "device_history": _history,
    "software_inventory": _software,
}


def _advice(record: ToolRecord, resolve: Resolver) -> Composition:
    """The recommendations of one `recommend_actions` record: a claim per suggestion, or why none."""
    builder = _Builder(resolve)
    builder.coverage(record)
    suggestions = [SuggestedAction.model_validate(s) for s in record.suggestions or []]
    hostnames = {f.device_id: f.hostname for f in record.findings}
    notes: list[str] = []
    if record.unresolved_ids:
        notes.append(texts.unresolved_note(record.unresolved_ids))
    if record.advice_fallback in texts.ACTION_LABELS:
        notes.append(texts.no_qualifying_note(record.advice_fallback))
    coverage = Coverage(population=record.population)
    if not suggestions:
        # Fixed headline, plus one code-written line per device saying why nothing was suggested.
        return Composition(
            "no_match",
            "no_matching_records",
            coverage=coverage,
            notes=[texts.no_suggestion_note(h) for h in hostnames.values()] + notes,
            fallback_headline=texts.NO_SUGGESTIONS,
            rules=[texts.ADVISE_RULE],
        )
    for suggestion in suggestions:
        builder._hosts[suggestion.device_id] = suggestion.target_label  # noqa: SLF001
        finding = FindingRecord(
            device_id=suggestion.device_id,
            hostname=suggestion.target_label,
            values={},
            refs=suggestion.refs,
        )
        builder.claim(
            suggestion.reason, [finding], refs_by_device={suggestion.device_id: suggestion.refs}
        )
    ordered = list(dict.fromkeys(s.device_id for s in suggestions))
    builder.entity_list(
        "suggested_actions",
        "Suggested actions",
        [
            FindingRecord(device_id=i, hostname=hostnames.get(i, i), values={}, refs=[])
            for i in ordered
        ],
    )
    return Composition(
        "answer",
        None,
        builder.claims,
        list(builder.evidence.values()),
        coverage,
        builder.lists,
        notes,
        texts.ADVISE_HEADLINE,
        [texts.ADVISE_RULE],
        suggested_actions=suggestions,
    )


# Tools that analyse the fleet and answer a question themselves. A `find_devices` record is only an
# inventory answer when none of these ran in the turn; next to one it is a lookup (a way to pick or
# name devices) and adds no claim, list or evidence, even when the model never fed its devices to the
# analysis call (a sibling call in the same round). `device_history` counts: it answers for one
# device. `recommend_actions` and `propose_action` never reach this path (composed earlier).
ANALYSIS_TOOLS = frozenset(_TEMPLATES) - {"find_devices"}


def _scaffolding(usable: Sequence[ToolRecord]) -> set[int]:
    """Positions of `find_devices` records that are lookups, not answers.

    A lookup is scaffolding when a succeeded analysis record (`ANALYSIS_TOOLS`) is in the turn,
    whether it came before, after or in the same round as the lookup. The analysis record answers
    the question; the lookup adds no claim, list or evidence, and if the analysis is empty the
    outcome is no_match over the analysis population. It is also scaffolding when a later record that
    succeeded was called with `device_ids` that are all among the lookup's devices. A lookup nothing
    was filtered by, with no analysis beside it (a plain inventory question), is composed as usual.
    """
    hidden: set[int] = set()
    analysed = any(r.tool in ANALYSIS_TOOLS for r in usable)
    for i, record in enumerate(usable):
        if record.tool != "find_devices":
            continue
        if analysed:
            hidden.add(i)
            continue
        found = set(record.device_ids)
        for later in usable[i + 1 :]:
            asked = later.args.get("device_ids")
            if isinstance(asked, list) and asked and set(asked) <= found:
                hidden.add(i)
                break
    return hidden


def compose(records: Sequence[ToolRecord], *, resolve: Resolver) -> Composition:
    """Turn tool records into claims, evidence, coverage and entity lists. No model involved."""
    if any(r.error == "tool_error" for r in records):
        return Composition("tool_error", "tool_failure", fallback_headline=texts.TOOL_FAILED)
    if any(r.tool == "propose_action" and r.error == "not_found" for r in records):
        # A device that is not this tenant's reads exactly like any other missing record.
        return Composition("denied", "not_found", fallback_headline=texts.NOT_FOUND)
    proposed = next((r for r in records if r.proposal is not None), None)
    if proposed is not None:
        card = ProposalCard.model_validate(proposed.proposal)
        return Composition(
            "proposal",
            None,
            evidence=_Builder(resolve).proposal_evidence(card),
            fallback_headline=texts.proposal_headline(
                texts.ACTION_LABELS[card.action_type], card.target_label
            ),
            proposal=card,
        )
    refused = next((r for r in records if r.action_refusal is not None), None)
    if refused is not None:
        reason = refused.action_refusal["reason"]  # type: ignore[index]
        return Composition(
            "denied",
            reason,  # type: ignore[arg-type]
            notes=[refused.action_refusal["detail"]],  # type: ignore[index]
            fallback_headline=texts.ACTION_REFUSAL_HEADLINES[reason],  # type: ignore[index]
        )
    if records and all(r.error == "not_found" for r in records):
        return Composition("denied", "not_found", fallback_headline=texts.NOT_FOUND)
    advised = next((r for r in records if not r.error and r.suggestions is not None), None)
    if advised is not None:
        return _advice(advised, resolve)
    builder = _Builder(resolve)
    usable = [r for r in records if not r.error]
    hidden = _scaffolding(usable)
    shown = [r for i, r in enumerate(usable) if i not in hidden]
    for index, record in enumerate(shown, start=1):
        _TEMPLATES[record.tool](record, index, builder)
    builder.disclosure_notes()
    unresolved = sum(r.unresolved_ids for r in usable)
    if unresolved:
        builder.add_note(texts.unresolved_note(unresolved))
    if usable and any(r.error == "not_found" for r in records):
        builder.add_note(texts.PARTIAL_NOT_FOUND)
    population = builder.population or 0
    coverage = Coverage(
        population=population,
        stale=sorted(builder.stale),
        missing_latest=sorted(builder.missing),
        excluded={reason: sorted(ids) for reason, ids in sorted(builder.excluded.items())},
        excluded_devices=builder.excluded_devices(),
    )
    # One list tool in the turn gets a table, a sentence, scope counts and next questions.
    shown_presentation = present(shown[0]) if len(shown) == 1 else None
    ui: dict = {}
    if shown_presentation is not None:
        ui = {
            "scope_parts": shown_presentation.scope,
            "follow_ups": shown_presentation.follow_ups,
        }
    if builder.claims:
        if shown_presentation is not None and shown_presentation.table is not None:
            for row in shown_presentation.table.rows:
                for ref in row.refs:
                    builder.add_evidence(ref)
            ui["table"] = shown_presentation.table
            ui["summary"] = shown_presentation.summary
        return Composition(
            "answer",
            None,
            builder.claims,
            list(builder.evidence.values()),
            coverage,
            builder.lists,
            builder.notes,
            "; ".join(builder.headlines) or builder.claims[0].text.split(":")[0],
            builder.rules,
            **ui,
        )
    thin = [r for r in usable if r.excluded.get("insufficient_history")]
    if (
        usable
        and len(thin) == len(usable)
        and all(len(r.excluded["insufficient_history"]) >= r.population > 0 for r in thin)
    ):
        return Composition(
            "insufficient_data",
            "insufficient_history",
            coverage=coverage,
            notes=builder.notes,
            fallback_headline=texts.INSUFFICIENT_HISTORY,
            rules=builder.rules,
            **ui,
        )
    return Composition(
        "no_match",
        "no_matching_records",
        coverage=coverage,
        notes=builder.notes,
        fallback_headline=texts.no_match(population),
        rules=builder.rules,
        **ui,
    )
