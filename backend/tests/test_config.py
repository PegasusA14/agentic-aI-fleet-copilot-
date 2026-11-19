from fleet_copilot.config import EXPECTED_DATASET_SHA256, PINNED_AS_OF, REPO_ROOT, Settings


def test_defaults_resolve_inside_the_repository(monkeypatch):
    monkeypatch.delenv("FLEET_DATA_PATH", raising=False)
    monkeypatch.delenv("FLEET_DB_PATH", raising=False)
    settings = Settings.from_env()
    assert settings.data_path == REPO_ROOT / "data" / "device-telemetry-dataset.ndjson"
    assert settings.data_path.exists()
    assert settings.db_path == REPO_ROOT / "var" / "fleet.db"


def test_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_DATA_PATH", str(tmp_path / "d.ndjson"))
    monkeypatch.setenv("FLEET_DB_PATH", str(tmp_path / "x.db"))
    settings = Settings.from_env()
    assert settings.data_path == tmp_path / "d.ndjson"
    assert settings.db_path == tmp_path / "x.db"


def test_pins_are_well_formed():
    assert len(EXPECTED_DATASET_SHA256) == 64
    assert PINNED_AS_OF == "2026-06-12T11:00:00Z"
