import pytest

from fleet_copilot.evidence import EvidenceRef


def test_format_and_parse_round_trip():
    ref = EvidenceRef("acme-001", "1LYSSFD074BB", "2026-06-12T09:41:00Z", "compliance[screen_lock]")
    assert ref.format() == "acme-001/1LYSSFD074BB@2026-06-12T09:41:00Z#compliance[screen_lock]"
    assert str(ref) == ref.format()
    assert EvidenceRef.parse(ref.format()) == ref


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "acme-001/DEV@2026-06-12#os",
        "acme-001/DEV@2026-06-12T09:00:00Z",
        "DEV@2026-06-12T09:00:00Z#os",
        "a/b/c@2026-06-12T09:00:00Z#os",
    ],
)
def test_malformed_references_are_rejected(bad):
    with pytest.raises(ValueError):
        EvidenceRef.parse(bad)
