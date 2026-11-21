import sqlite3
from datetime import UTC, datetime, timedelta

from graph_support import ACME_ADMIN, GLOBEX_ADMIN, answer_plan, explanation, make_service

from fleet_copilot.agent import outcomes
from fleet_copilot.agent.identity import Identity
from fleet_copilot.agent.llm import ScriptedLLM, final_message, tool_calls_message
from fleet_copilot.agent.service import TurnRecord
from fleet_copilot.contracts import AnswerEnvelope
from fleet_copilot.observability.conversations import ConversationStore, make_title

AS_OF = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
OTHER_ACME_ADMIN = Identity("acme-001", "admin-acme-2", "Second Acme admin (demo)")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def store(tmp_path, clock=None):
    return ConversationStore(tmp_path / "conversations.db", now=clock or Clock())


def envelope(trace_id="t1", company_id="acme-001") -> AnswerEnvelope:
    return outcomes.tool_failure(company_id=company_id, as_of=AS_OF, trace_id=trace_id)


def turn(
    question="Which devices are low on disk space?",
    *,
    identity=ACME_ADMIN,
    conversation_id="c1",
    trace_id="t1",
    env="default",
) -> TurnRecord:
    return TurnRecord(
        identity=identity,
        conversation_id=conversation_id,
        question=question,
        trace_id=trace_id,
        events=(),
        envelope=envelope(trace_id, identity.company_id) if env == "default" else env,
        elapsed_ms=5,
    )


# ---- title ---------------------------------------------------------------------------------------


def test_a_short_question_is_its_own_title():
    assert (
        make_title("Which devices are low on disk space?") == "Which devices are low on disk space?"
    )


def test_a_long_question_is_trimmed_to_60_characters_at_a_word_boundary():
    title = make_title(
        "Which devices have both low disk space and a battery that needs replacing soon?"
    )
    assert title == "Which devices have both low disk space and a battery that"
    assert len(title) <= 60


def test_a_title_without_spaces_is_cut_at_60_and_whitespace_is_collapsed():
    assert make_title("x" * 200) == "x" * 60
    assert make_title("  low \n\n disk\t devices  ") == "low disk devices"


def test_a_title_that_ends_exactly_on_a_word_boundary_keeps_the_word():
    sixty = "a" * 30 + " " + "b" * 29  # 60 characters
    assert make_title(sixty + " tail") == sixty


# ---- recording and reading ------------------------------------------------------------------------


def test_the_first_turn_creates_the_conversation_and_later_turns_append(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn("First question here", trace_id="t1"))
    conversations.record(turn("Second one", trace_id="t2"))
    stored = conversations.get("acme-001", "admin-acme", "c1")
    assert stored is not None
    assert stored["conversation_id"] == "c1" and stored["title"] == "First question here"
    assert [(t["seq"], t["question"], t["trace_id"]) for t in stored["turns"]] == [
        (0, "First question here", "t1"),
        (1, "Second one", "t2"),
    ]


def test_envelopes_are_stored_as_json_and_come_back_as_objects(tmp_path):
    conversations = store(tmp_path)
    original = envelope("t1")
    conversations.record(turn(trace_id="t1"))
    stored = conversations.get("acme-001", "admin-acme", "c1")
    back = stored["turns"][0]["envelope"]
    assert isinstance(back, dict)
    assert AnswerEnvelope.model_validate(back) == original
    with sqlite3.connect(tmp_path / "conversations.db") as raw:
        (text,) = raw.execute("SELECT envelope_json FROM turns").fetchone()
    assert isinstance(text, str) and AnswerEnvelope.model_validate_json(text) == original


def test_turn_timestamps_come_from_the_injected_clock(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn(trace_id="t1"))
    stored = conversations.get("acme-001", "admin-acme", "c1")
    assert stored["turns"][0]["created_at"] == "2026-11-21T09:01:00Z"


def test_a_turn_with_no_envelope_is_not_stored(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn(env=None))
    assert conversations.list("acme-001", "admin-acme") == []
    assert conversations.get("acme-001", "admin-acme", "c1") is None


def test_an_aborted_later_turn_leaves_the_earlier_turns_alone(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn("kept", trace_id="t1"))
    conversations.record(turn("left before the answer", trace_id="t2", env=None))
    stored = conversations.get("acme-001", "admin-acme", "c1")
    assert [t["question"] for t in stored["turns"]] == ["kept"]


def test_list_is_newest_first_with_turn_counts_and_a_limit(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn("older", conversation_id="a", trace_id="t1"))
    conversations.record(turn("newer", conversation_id="b", trace_id="t2"))
    conversations.record(turn("older again", conversation_id="a", trace_id="t3"))  # a is now newest
    rows = conversations.list("acme-001", "admin-acme")
    assert [(r["conversation_id"], r["title"], r["turns"]) for r in rows] == [
        ("a", "older", 2),
        ("b", "newer", 1),
    ]
    assert set(rows[0]) == {"conversation_id", "title", "updated_at", "turns"}
    assert rows[0]["updated_at"] > rows[1]["updated_at"]
    assert [
        r["conversation_id"] for r in conversations.list("acme-001", "admin-acme", limit=1)
    ] == ["a"]
    assert conversations.list("acme-001", "admin-acme", limit=0) == []


def test_reads_and_deletes_are_scoped_to_company_and_actor(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn("acme private", trace_id="t1"))
    # the same conversation id under another tenant and under another admin of the same tenant
    conversations.record(turn("globex own", identity=GLOBEX_ADMIN, trace_id="t2"))
    conversations.record(turn("second admin", identity=OTHER_ACME_ADMIN, trace_id="t3"))

    assert [r["title"] for r in conversations.list("acme-001", "admin-acme")] == ["acme private"]
    assert conversations.get("globex-002", "admin-acme", "c1") is None
    assert conversations.get("acme-001", "admin-other", "c1") is None
    assert conversations.get("acme-001", "admin-acme-2", "c1")["title"] == "second admin"

    assert conversations.delete("globex-002", "admin-acme", "c1") is False
    assert conversations.delete("acme-001", "admin-acme", "c1") is True
    assert conversations.get("acme-001", "admin-acme", "c1") is None
    assert conversations.get("globex-002", "admin-globex", "c1")["title"] == "globex own"
    assert conversations.get("acme-001", "admin-acme-2", "c1")["title"] == "second admin"
    assert conversations.delete("acme-001", "admin-acme", "c1") is False  # idempotent


def test_delete_removes_the_turns_too(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn(trace_id="t1"))
    conversations.record(turn(trace_id="t2"))
    conversations.delete("acme-001", "admin-acme", "c1")
    with sqlite3.connect(tmp_path / "conversations.db") as raw:
        assert raw.execute("SELECT COUNT(*) FROM turns").fetchone() == (0,)
        assert raw.execute("SELECT COUNT(*) FROM conversations").fetchone() == (0,)


def test_a_new_turn_after_a_delete_starts_a_fresh_conversation(tmp_path):
    conversations = store(tmp_path)
    conversations.record(turn("old title", trace_id="t1"))
    conversations.delete("acme-001", "admin-acme", "c1")
    conversations.record(turn("new title", trace_id="t2"))
    stored = conversations.get("acme-001", "admin-acme", "c1")
    assert stored["title"] == "new title" and [t["seq"] for t in stored["turns"]] == [0]


def test_history_survives_reopening_the_database(tmp_path):
    store(tmp_path).record(turn(trace_id="t1"))
    again = ConversationStore(tmp_path / "conversations.db")
    assert again.get("acme-001", "admin-acme", "c1")["turns"][0]["trace_id"] == "t1"


def test_the_parent_directory_is_created(tmp_path):
    ConversationStore(tmp_path / "state" / "deeper" / "conversations.db")
    assert (tmp_path / "state" / "deeper" / "conversations.db").is_file()


# ---- written by the real recorder ------------------------------------------------------------------


async def test_the_service_hands_the_question_and_envelope_to_the_recorder(tmp_path, real_db_path):
    conversations = store(tmp_path)
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    service = make_service(real_db_path, llm, recorder=conversations.record)
    result = await service.run_turn(ACME_ADMIN, "c1", "  Which devices are low on disk space?  ")
    stored = conversations.get("acme-001", "admin-acme", "c1")
    assert stored["title"] == "Which devices are low on disk space?"  # the cleaned question
    (only,) = stored["turns"]
    assert only["trace_id"] == result.envelope.trace_id
    assert AnswerEnvelope.model_validate(only["envelope"]) == result.envelope


async def test_a_reader_that_leaves_before_the_answer_stores_nothing(tmp_path, real_db_path):
    conversations = store(tmp_path)
    llm = ScriptedLLM(
        plans=[answer_plan()],
        acts=[tool_calls_message(("low_disk_devices", {})), final_message()],
        explains=[explanation()],
    )
    service = make_service(real_db_path, llm, recorder=conversations.record)
    stream = service.stream_turn(ACME_ADMIN, "c1", "low disk?")
    await anext(stream)
    await stream.aclose()
    assert conversations.list("acme-001", "admin-acme") == []
