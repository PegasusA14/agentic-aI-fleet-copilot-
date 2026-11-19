import importlib.util
import json

from fleet_copilot.config import REPO_ROOT, Settings

ORACLE = REPO_ROOT / "evals" / "golden" / "oracle.py"
GOLDEN = REPO_ROOT / "evals" / "golden" / "golden.json"


def load_oracle():
    spec = importlib.util.spec_from_file_location("fleet_oracle", ORACLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_golden_matches_a_fresh_oracle_run():
    fresh = load_oracle().generate(Settings.from_env().data_path.read_bytes())
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == fresh


def test_oracle_is_independent_of_the_product_package():
    assert "fleet_copilot" not in ORACLE.read_text(encoding="utf-8")
