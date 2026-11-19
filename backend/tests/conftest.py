import pytest

from fleet_copilot.config import EXPECTED_DATASET_SHA256, Settings
from fleet_copilot.data.loader import connect, load_dataset


@pytest.fixture(scope="session")
def real_conn(tmp_path_factory):
    """SQLite connection over the real, sha-verified dataset."""
    db = tmp_path_factory.mktemp("db") / "fleet.sqlite"
    load_dataset(Settings.from_env().data_path, db, expected_sha256=EXPECTED_DATASET_SHA256)
    conn = connect(db)
    yield conn
    conn.close()
