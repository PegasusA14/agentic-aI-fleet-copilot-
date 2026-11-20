import json

from graph_support import answer_plan, explanation

from fleet_copilot import cli
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.config import Settings
from fleet_copilot.data.cli import main as load_data_main


def test_cli_loads_the_real_dataset(tmp_path, capsys):
    code = load_data_main(
        ["--data", str(Settings.from_env().data_path), "--db", str(tmp_path / "x.db")]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "750 snapshots" in out
    assert "as of 2026-06-12T11:00:00Z" in out


def scripted():
    return ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )


def run(argv, monkeypatch, tmp_path, real_db_path, gateway=None, capsys=None):
    monkeypatch.setenv("FLEET_DB_PATH", str(real_db_path))
    monkeypatch.setenv("FLEET_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "build_gateway", lambda settings: gateway or scripted())
    return cli.main(argv)


def test_ask_prints_the_answer_and_persists_the_trace(monkeypatch, tmp_path, real_db_path, capsys):
    code = run(["ask", "Which devices are low on disk space?"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "[answer]" in out and "evidence" in out
    assert (tmp_path / "traces.db").exists()


def test_ask_json_is_one_valid_envelope(monkeypatch, tmp_path, real_db_path, capsys):
    run(["ask", "low disk?", "--json"], monkeypatch, tmp_path, real_db_path)
    assert json.loads(capsys.readouterr().out)["outcome"] == "answer"


def test_ask_trace_lists_the_steps(monkeypatch, tmp_path, real_db_path, capsys):
    run(["ask", "low disk?", "--trace"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert "scope" in out and "respond" in out


def test_ask_refuses_an_unknown_user_and_an_empty_question(
    monkeypatch, tmp_path, real_db_path, capsys
):
    assert run(["ask", "x", "--as", "nobody"], monkeypatch, tmp_path, real_db_path) == 2
    assert run(["ask", "   "], monkeypatch, tmp_path, real_db_path) == 2
    assert "Ask a question" in capsys.readouterr().err


def test_doctor_reports_names_and_never_values(monkeypatch, tmp_path, real_db_path, capsys):
    monkeypatch.setenv("FLEET_MODEL_MODE", "live")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://hidden-resource.openai.azure.com/")
    code = run(["doctor"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert code == 1  # live mode with the version and deployment missing
    assert "AZURE_OPENAI_API_KEY: set" in out and "AZURE_OPENAI_DEPLOYMENT: missing" in out
    assert "sk-super-secret-value" not in out and "hidden-resource" not in out


def test_doctor_in_replay_mode_is_healthy(monkeypatch, tmp_path, real_db_path, capsys):
    monkeypatch.setenv("FLEET_CASSETTE_PATH", str(tmp_path / "c.jsonl"))
    assert run(["doctor"], monkeypatch, tmp_path, real_db_path) == 0
    assert "mode: replay" in capsys.readouterr().out


def test_ask_in_replay_mode_with_an_empty_cassette_fails_politely(
    monkeypatch, tmp_path, real_db_path, capsys
):
    gateway = CassetteGateway(tmp_path / "empty.jsonl")
    code = run(["ask", "low disk?"], monkeypatch, tmp_path, real_db_path, gateway=gateway)
    out = capsys.readouterr().out
    assert code == 0 and "[tool_error]" in out
