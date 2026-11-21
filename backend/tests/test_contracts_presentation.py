"""The additive list-answer fields: table, summary, scope line, excluded devices, follow-ups, repeat marker."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fleet_copilot.contracts import (
    MAX_TABLE_ROWS,
    Alternative,
    AnswerEnvelope,
    AnswerTable,
    Cell,
    Claim,
    Column,
    Coverage,
    EvidenceItem,
    ExcludedDevice,
    Row,
    ScopeInfo,
    ScopeLine,
    SortSpec,
)

AS_OF = datetime(2026, 6, 12, 11, 0, tzinfo=UTC)
SCOPE = ScopeInfo(company_id="acme-001", as_of=AS_OF, rule="free < 10%", population=10)
REF = "acme-001/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk"
OTHER_REF = "acme-001/8NM23J95R5I6@2026-06-12T08:39:00Z#disk"
FOREIGN_REF = "globex-002/MT7PJB7N5LRE@2026-06-12T08:39:00Z#disk"


def row(refs=(REF,), device_id="MT7PJB7N5LRE", **cells):
    return Row(
        device_id=device_id,
        label="acme-macbook-4",
        cells={"device": Cell(display="acme-macbook-4", value=device_id), **cells},
        refs=list(refs),
    )


def table(rows=None, **overrides):
    base = {
        "title": "Low disk",
        "columns": [
            Column(key="device", label="Device", kind="device"),
            Column(key="free", label="Free", kind="percent"),
        ],
        "rows": rows if rows is not None else [row(free=Cell(display="2.0%", value=2.0))],
        "default_sort": SortSpec(key="free", direction="asc"),
    }
    return AnswerTable(**{**base, **overrides})


def envelope(**overrides):
    base = {
        "outcome": "answer",
        "trace_id": "t-1",
        "headline": "2 of 10 devices are low on disk",
        "claims": [Claim(text="MT7PJB7N5LRE has 2.0% free", refs=[REF])],
        "evidence": [EvidenceItem(ref=REF, label="disk", value="11 GB of 512 GiB free")],
        "scope": SCOPE,
    }
    return AnswerEnvelope(**{**base, **overrides})


def test_an_envelope_without_the_new_fields_still_validates_and_defaults_them():
    old = envelope()
    assert old.table is None and old.summary == "" and old.scope_line is None
    assert old.follow_ups == [] and old.repeat_of is None
    assert Coverage(population=3).excluded_devices == []
    revived = AnswerEnvelope.model_validate(old.model_dump(mode="json"))
    assert revived == old


def test_a_table_whose_rows_cite_known_in_tenant_evidence_validates():
    ok = envelope(table=table(), summary="Both are under 3% free.")
    assert ok.table.rows[0].refs == [REF]


def test_a_row_citing_a_ref_that_is_not_in_the_evidence_is_rejected():
    with pytest.raises(ValidationError, match="table row cites refs without evidence"):
        envelope(table=table([row(refs=[OTHER_REF], device_id="8NM23J95R5I6")]))


def test_a_row_citing_another_companys_ref_is_rejected():
    with pytest.raises(ValidationError, match="belongs to another company"):
        envelope(table=table([row(refs=[FOREIGN_REF])]))


def test_a_row_citing_a_malformed_ref_is_rejected():
    with pytest.raises(ValidationError, match="malformed"):
        envelope(table=table([row(refs=["not-a-ref"])]))


def test_a_row_citing_a_reading_of_another_device_is_rejected():
    evidence = [
        EvidenceItem(ref=REF, label="disk", value="x"),
        EvidenceItem(ref=OTHER_REF, label="disk", value="y"),
    ]
    with pytest.raises(ValidationError, match="another device"):
        envelope(evidence=evidence, table=table([row(refs=[OTHER_REF])]))


def test_a_row_needs_at_least_one_source():
    with pytest.raises(ValidationError):
        row(refs=[])


def test_rows_are_capped():
    many = [row(free=Cell(display="2.0%", value=2.0)) for _ in range(MAX_TABLE_ROWS + 1)]
    with pytest.raises(ValidationError):
        table(many)
    assert len(table(many[:MAX_TABLE_ROWS], total_rows=60).rows) == MAX_TABLE_ROWS


def test_total_rows_cannot_be_smaller_than_the_rows_shown():
    with pytest.raises(ValidationError, match="total_rows"):
        table(total_rows=0)


def test_the_sort_key_and_cell_keys_must_name_columns():
    with pytest.raises(ValidationError, match="default_sort"):
        table(default_sort=SortSpec(key="nope", direction="asc"))
    with pytest.raises(ValidationError, match="cell for a column"):
        table([row(ghost=Cell(display="x"))])


def test_column_keys_are_unique_and_slug_shaped():
    twice = [Column(key="a", label="A", kind="text"), Column(key="a", label="B", kind="text")]
    with pytest.raises(ValidationError, match="duplicate column"):
        table(columns=twice, default_sort=SortSpec(key="a", direction="asc"), rows=[])
    with pytest.raises(ValidationError):
        Column(key="Not A Slug", label="A", kind="text")
    with pytest.raises(ValidationError):
        Column(key="a", label="A", kind="chart")


def test_only_an_answer_may_carry_a_table():
    with pytest.raises(ValidationError, match="only an answer"):
        envelope(outcome="no_match", reason_code="no_matching_records", table=table())


def test_the_summary_is_capped_at_200_characters():
    assert envelope(summary="x" * 200).summary == "x" * 200
    with pytest.raises(ValidationError):
        envelope(summary="x" * 201)


def test_follow_ups_are_alternatives_capped_at_four():
    ups = [Alternative(label=f"Ask {i}", request=f"Question {i}?") for i in range(4)]
    assert len(envelope(follow_ups=ups).follow_ups) == 4
    with pytest.raises(ValidationError):
        envelope(follow_ups=[*ups, Alternative(label="One more", request="Another?")])


def test_scope_line_counts_must_add_up():
    base = {
        "checked": 5,
        "matched": 4,
        "not_checked": 2,
        "basis": "latest snapshot",
        "as_of": AS_OF,
    }
    ok = ScopeLine(**base, ok=1, ok_label="up to date", company_name="Initech")
    assert (ok.ok, ok.not_checked) == (1, 2)
    assert ScopeLine(**base, ok=None).ok is None  # unknown statuses: no "up to date" count
    with pytest.raises(ValidationError, match="matched"):
        ScopeLine(**{**base, "matched": 6})
    with pytest.raises(ValidationError, match="ok must fit"):
        ScopeLine(**base, ok=2, ok_label="up to date")
    with pytest.raises(ValidationError, match="ok must fit"):
        ScopeLine(**base, ok=1)  # a count with no word for it


def test_scope_line_needs_a_timezone_aware_as_of():
    with pytest.raises(ValidationError):
        ScopeLine(checked=1, matched=0, not_checked=0, basis="x", as_of=datetime(2026, 6, 12))


def test_excluded_devices_must_match_the_excluded_map():
    device = ExcludedDevice(
        device_id="D1",
        label="initech-dell-5",
        reason="out_of_scope",
        reason_text="Windows 11, not a Mac",
    )
    ok = Coverage(population=5, excluded={"out_of_scope": ["D1"]}, excluded_devices=[device])
    assert ok.excluded_devices[0].reason_text == "Windows 11, not a Mac"
    with pytest.raises(ValidationError, match="does not"):
        Coverage(population=5, excluded={"unknown_version": ["D1"]}, excluded_devices=[device])
    with pytest.raises(ValidationError):
        ExcludedDevice(device_id="D1", label="x", reason="because", reason_text="x")


@pytest.mark.parametrize("outcome", ["denied", "unsupported", "tool_error"])
def test_refusals_carry_no_list_presentation(outcome):
    reason = {"denied": "not_found", "unsupported": "off_topic", "tool_error": "tool_failure"}
    with pytest.raises(ValidationError):
        AnswerEnvelope(
            outcome=outcome,
            trace_id="t-1",
            headline="No",
            scope=SCOPE,
            reason_code=reason[outcome],
            summary="Two are low.",
        )


def test_repeat_of_is_only_for_answers():
    assert envelope(repeat_of="t-0").repeat_of == "t-0"
    with pytest.raises(ValidationError, match="only an answer"):
        envelope(outcome="no_match", reason_code="no_matching_records", repeat_of="t-0")
