from fleet_copilot.api.__main__ import bind_host


def test_the_server_listens_on_loopback_by_default():
    assert bind_host({}) == "127.0.0.1"


def test_only_the_container_flag_opens_every_interface():
    assert bind_host({"FLEET_CONTAINER": "1"}) == "0.0.0.0"


def test_any_other_value_keeps_loopback():
    for value in ("", "0", "true", "yes", "0.0.0.0"):
        assert bind_host({"FLEET_CONTAINER": value}) == "127.0.0.1"
    assert bind_host({"FLEET_HOST": "0.0.0.0"}) == "127.0.0.1"
