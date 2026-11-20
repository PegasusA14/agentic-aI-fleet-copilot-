"""Replays the recorded live check offline. Skipped until `make live-check` has recorded a cassette."""

import pytest
from graph_support import make_service

from fleet_copilot import live_check
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.config import REPO_ROOT

CASSETTE = REPO_ROOT / "var" / "cassettes" / "live-check.jsonl"


@pytest.mark.skipif(not CASSETTE.exists(), reason="no recorded live-check cassette yet")
async def test_the_recorded_live_check_still_passes_offline(real_db_path):
    service = make_service(real_db_path, CassetteGateway(CASSETTE))
    results = await live_check.run_cases(service)
    failed = [(r.name, r.reason) for r in results if not r.passed]
    assert failed == []
