import asyncio
import json
from pathlib import Path

from graph_support import answer_plan, explanation
from pydantic import SecretStr

from fleet_copilot import cli
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.chat_gateway import ModelUnavailable
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.providers import AzureSettings
from fleet_copilot.config import ModelSettings, Settings
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


def test_doctor_names_the_provider_and_its_variables(monkeypatch, tmp_path, real_db_path, capsys):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-super-secret-value")
    run(["doctor"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert "provider: azure" in out and "AZURE_OPENAI_API_KEY: set" in out
    assert "OPENAI_MODEL" not in out and "OPENROUTER" not in out
    assert "sk-super-secret-value" not in out


def test_doctor_for_openai_lists_only_its_variables(monkeypatch, tmp_path, real_db_path, capsys):
    monkeypatch.setenv("FLEET_MODEL_MODE", "live")
    monkeypatch.setenv("FLEET_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret-value")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-secret-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://hidden-gateway.example.net/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-azure-leftover")
    code = run(["doctor"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "provider: openai" in out
    assert "OPENAI_API_KEY: set" in out and "OPENAI_MODEL: set" in out
    assert "OPENAI_BASE_URL: set" in out
    assert "AZURE" not in out and "OPENROUTER" not in out
    for value in ("sk-openai-secret-value", "gpt-secret-model", "hidden-gateway", "sk-azure"):
        assert value not in out


def test_doctor_for_openrouter_reports_missing_and_optional_variables(
    monkeypatch, tmp_path, real_db_path, capsys
):
    monkeypatch.setenv("FLEET_MODEL_MODE", "live")
    monkeypatch.setenv("FLEET_MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value")
    code = run(["doctor"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert code == 1  # the model is missing
    assert "provider: openrouter" in out and "OPENROUTER_API_KEY: set" in out
    assert "OPENROUTER_MODEL: missing" in out
    assert "OPENROUTER_APP_NAME: not set (optional)" in out
    assert "sk-or-secret-value" not in out


def test_doctor_refuses_an_unknown_provider_without_echoing_it(
    monkeypatch, tmp_path, real_db_path, capsys
):
    monkeypatch.setenv("FLEET_MODEL_PROVIDER", "sk-pasted-into-the-wrong-variable")
    code = run(["doctor"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert code == 1
    assert "provider: invalid" in out and "FLEET_MODEL_PROVIDER" in out and "sk-pasted" not in out


def test_the_probe_keeps_the_selected_provider(monkeypatch):
    from fleet_copilot.agent.providers import OpenAICompatibleSettings

    seen = []

    def fake_build(settings):
        seen.append(settings)
        return ScriptedLLM(explains=[explanation()])

    monkeypatch.setattr(cli, "build_gateway", fake_build)
    models = ModelSettings(
        mode="live",
        cassette_path=Path("unused.jsonl"),
        record=True,
        call_timeout_s=5.0,
        provider="openrouter",
        provider_settings=OpenAICompatibleSettings(
            api_key=SecretStr("sk-x"), model="m", base_url="https://openrouter.ai/api/v1"
        ),
    )
    assert asyncio.run(cli._probe(models)) == "ok"
    assert (
        seen[0].provider == "openrouter" and seen[0].provider_settings is models.provider_settings
    )
    assert seen[0].record is False


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


def test_ask_with_unusable_settings_prints_variable_names_and_exits_2(
    monkeypatch, tmp_path, real_db_path, capsys
):
    monkeypatch.setenv("FLEET_MODEL_MODE", "live")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-super-secret-value")
    code = run(["ask", "low disk?"], monkeypatch, tmp_path, real_db_path)
    captured = capsys.readouterr()
    assert code == 2
    assert "AZURE_OPENAI_ENDPOINT" in captured.err and "Run make doctor." in captured.err
    assert "sk-super-secret-value" not in captured.out + captured.err


def test_doctor_does_not_echo_an_invalid_mode(monkeypatch, tmp_path, real_db_path, capsys):
    monkeypatch.setenv("FLEET_MODEL_MODE", "sk-pasted-into-the-wrong-variable")
    code = run(["doctor"], monkeypatch, tmp_path, real_db_path)
    out = capsys.readouterr().out
    assert code == 1
    assert "mode: invalid" in out and "sk-pasted" not in out


def azure_models():
    return ModelSettings(
        mode="live",
        provider_settings=AzureSettings(
            api_key=SecretStr("sk-probe-secret"),
            endpoint="https://hidden-resource.openai.azure.com/",
            api_version="2024-01-01",
            deployment="d",
        ),
        cassette_path=Path("unused.jsonl"),
        record=True,
        call_timeout_s=5.0,
    )


async def test_the_probe_never_records_a_cassette(monkeypatch):
    seen = []

    def fake_build(settings):
        seen.append(settings)
        return ScriptedLLM(explains=[explanation()])

    monkeypatch.setattr(cli, "build_gateway", fake_build)
    assert await cli._probe(azure_models()) == "ok"
    assert [s.record for s in seen] == [False]


async def test_the_probe_reports_only_type_names(monkeypatch):
    class Broken:
        async def explain(self, messages):
            raise ModelUnavailable("RateLimitError")

    monkeypatch.setattr(cli, "build_gateway", lambda settings: Broken())
    assert await cli._probe(azure_models()) == "failed (ModelUnavailable: RateLimitError)"

    def explode(settings):
        raise RuntimeError("sk-probe-secret in the message")

    monkeypatch.setattr(cli, "build_gateway", explode)
    outcome = await cli._probe(azure_models())
    assert outcome == "failed (RuntimeError)" and "sk-probe-secret" not in outcome


def test_doctor_reports_a_stale_database(monkeypatch, tmp_path, real_db_path, capsys):
    import shutil
    import sqlite3

    stale = tmp_path / "stale.db"
    shutil.copy(real_db_path, stale)
    with sqlite3.connect(stale) as conn:
        conn.execute("DELETE FROM dataset_meta WHERE key = 'schema_version'")
    monkeypatch.setenv("FLEET_CASSETTE_PATH", str(tmp_path / "c.jsonl"))
    code = run(["doctor"], monkeypatch, tmp_path, stale)
    out = capsys.readouterr().out
    assert code == 1 and "dataset db: stale (run make load-data)" in out
