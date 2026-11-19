from fleet_copilot.config import Settings
from fleet_copilot.data.cli import main


def test_cli_loads_the_real_dataset(tmp_path, capsys):
    code = main(["--data", str(Settings.from_env().data_path), "--db", str(tmp_path / "x.db")])
    out = capsys.readouterr().out
    assert code == 0
    assert "750 snapshots" in out
    assert "as of 2026-06-12T11:00:00Z" in out
