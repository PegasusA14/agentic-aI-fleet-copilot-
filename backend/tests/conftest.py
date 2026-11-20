import pytest

from fleet_copilot.config import EXPECTED_DATASET_SHA256, Settings
from fleet_copilot.data.loader import connect, load_dataset


@pytest.fixture(scope="session")
def real_db_path(tmp_path_factory):
    """Path of a database built from the real, sha-verified dataset."""
    db = tmp_path_factory.mktemp("db") / "fleet.sqlite"
    load_dataset(Settings.from_env().data_path, db, expected_sha256=EXPECTED_DATASET_SHA256)
    return db


@pytest.fixture(scope="session")
def real_conn(real_db_path):
    """SQLite connection over the real, sha-verified dataset."""
    conn = connect(real_db_path)
    yield conn
    conn.close()
