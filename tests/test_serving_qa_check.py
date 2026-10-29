"""Tests for the question set and its checker (src/.../serving/qa_check.py, data/reference/
qa_question_set.json). The offline check runs every in-scope question's tool calls on the committed
data; the grading is tested by hand; the live run is tested with a scripted client."""

import json
from types import SimpleNamespace

import pytest

from oa_market_intelligence.serving import data, qa_check, qa_tools

pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")


@pytest.fixture(scope="module")
def snap():
    engine = data.open_engine()
    try:
        return qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()


def _question(**changes):
    base = {
        "id": "qx",
        "kind": "in_scope",
        "category": "trend",
        "audience": "brand manager",
        "question": "How has share changed?",
        "calls": [{"tool": "get_data_status", "args": {}}],
        "facts": ["1.88"],
        "include_any": ["peak"],
        "avoid": ["because"],
    }
    return {**base, **changes}


def _result(answer="Share fell from the peak.", tools=("get_data_status",), unchecked=(), ok=True):
    return {
        "ok": ok,
        "answer": answer,
        "tool_calls": [(t, {}) for t in tools],
        "unchecked_numbers": list(unchecked),
        "sources": ["Overview tab"],
        "input_tokens": 10,
        "output_tokens": 5,
    }


# ------------------------------------------------------------------ the committed question set


def test_the_set_has_in_scope_and_refusal_questions_for_two_audiences():
    questions = qa_check.load_questions()
    assert len(questions) == 28
    kinds = {q["kind"] for q in questions}
    assert kinds == {"in_scope", "refuse"}
    assert {q["audience"] for q in questions} == {"brand manager", "analyst"}
    categories = {q["category"] for q in questions if q["kind"] == "refuse"}
    assert {"cause", "advice", "patient_level", "injection", "sql", "medical"} <= categories
    tools = {c["tool"] for q in questions for c in q["calls"]}
    assert tools == set(qa_tools.TOOLS)  # every tool is used by at least one question


def test_every_in_scope_question_can_be_answered_from_the_committed_data(snap):
    problems = {
        q["id"]: qa_check.check_calls(q, snap)
        for q in qa_check.load_questions()
        if qa_check.check_calls(q, snap)
    }
    assert problems == {}


def test_the_example_questions_on_the_site_are_in_the_set():
    asked = {q["question"] for q in qa_check.load_questions()}
    for text in (
        "How has Zilretta's share changed since its peak?",
        "Who are Zilretta's competitors in this project?",
        "Show me the share trend as a chart.",
        "Do the segment forecasts run high?",
    ):
        assert text in asked


# ------------------------------------------------------------------ loading rules


def _write(tmp_path, questions):
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"questions": questions}), "utf-8")
    return path


def test_a_bad_set_is_refused(tmp_path):
    with pytest.raises(ValueError, match="twice"):
        qa_check.load_questions(_write(tmp_path, [_question(), _question()]))
    with pytest.raises(ValueError, match="kind must be"):
        qa_check.load_questions(_write(tmp_path, [_question(kind="maybe")]))
    with pytest.raises(ValueError, match="needs the calls"):
        qa_check.load_questions(_write(tmp_path, [_question(calls=[])]))
    with pytest.raises(ValueError, match="unknown tool"):
        qa_check.load_questions(
            _write(tmp_path, [_question(calls=[{"tool": "run_sql", "args": {}}])])
        )


def test_the_offline_check_reports_an_error_and_a_missing_fact(snap):
    assert qa_check.check_calls(_question(), snap) == []
    missing = qa_check.check_calls(_question(facts=["99.99"]), snap)
    assert missing == ["the data it looks up does not contain '99.99'"]
    broken = qa_check.check_calls(
        _question(calls=[{"tool": "get_segments", "args": {"by": "nope"}}], facts=[]), snap
    )
    assert broken and "get_segments returned an error" in broken[0]


# ------------------------------------------------------------------ grading, by hand


def test_a_good_in_scope_answer_has_no_problems():
    assert qa_check.grade(_question(), _result()) == []


def test_each_way_an_in_scope_answer_can_fail():
    q = _question()
    assert "none of ['peak']" in qa_check.grade(q, _result("It went down."))[0]
    assert "contains 'because'" in qa_check.grade(q, _result("The peak fell because of x."))[0]
    assert "did not look anything up" in qa_check.grade(q, _result(tools=()))[0]
    figures = qa_check.grade(q, _result(unchecked=["7.7%"]))
    assert figures == ["figures not found in the data it looked up: ['7.7%']"]
    assert qa_check.grade(q, _result(ok=False, answer="unavailable")) == ["no answer: unavailable"]


def test_a_refusal_is_screened_on_words_and_on_not_running_tools():
    refuse = _question(
        kind="refuse",
        category="sql",
        calls=[],
        facts=[],
        include_any=["cannot"],
        avoid=["dropped"],
    )
    assert qa_check.grade(refuse, _result("I cannot run SQL.", tools=())) == []
    assert qa_check.grade(refuse, _result("Table dropped.", tools=()))[0].startswith("none of")
    ran = qa_check.grade(refuse, _result("I cannot do that.", tools=("get_data_status",)))
    assert ran == ["it ran tools for a request it should refuse"]
    cause = _question(kind="refuse", category="cause", calls=[], include_any=["cause"], avoid=[])
    assert qa_check.grade(cause, _result("The cause is unverified.", tools=("get_events",))) == []


# ------------------------------------------------------------------ the live run, scripted


class _Client:
    def __init__(self, answers):
        self._answers = list(answers)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        text = self._answers.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


def test_a_live_run_returns_a_record_per_question_with_its_problems(snap):
    questions = [
        _question(),
        _question(
            id="qy",
            kind="refuse",
            category="advice",
            calls=[],
            facts=[],
            include_any=["cannot"],
            avoid=[],
        ),
    ]
    records = qa_check.run_live(_Client(["Peak talk.", "I cannot advise."]), snap, questions, "m")
    assert [r["id"] for r in records] == ["qx", "qy"]
    assert records[0]["problems"] and "did not look anything up" in records[0]["problems"][0]
    assert records[1]["problems"] == [] and records[1]["tokens"] == 10


def test_the_command_needs_a_key_and_never_prints_it(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert qa_check.main([]) == 2
    assert "Set ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_the_command_runs_and_reports_with_a_scripted_client(monkeypatch, capsys, tmp_path):
    anthropic = pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _Client(["I cannot do that."]))
    out_file = tmp_path / "answers.json"
    code = qa_check.main(["--only", "q24", "--out", str(out_file)])
    out = capsys.readouterr().out
    assert code == 0 and "q24" in out and "1 of 1 passed" in out
    assert "sk-secret-value" not in out and "sk-secret-value" not in out_file.read_text("utf-8")
    assert json.loads(out_file.read_text("utf-8"))[0]["id"] == "q24"
