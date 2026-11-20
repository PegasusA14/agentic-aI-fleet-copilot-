from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


async def test_async_tests_run_without_a_marker():
    assert True


async def test_the_async_sqlite_checkpointer_opens_in_a_temp_directory(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        assert saver is not None
