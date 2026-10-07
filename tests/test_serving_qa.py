"""Tests for the Claude question loop (src/.../serving/qa.py), using a scripted fake client.

No network, no API key: the client is injected, so these tests check what we send, how we
answer tool calls, and that the loop always ends.
"""

from types import SimpleNamespace

import pandas as pd

from oa_market_intelligence.serving.qa import SYSTEM_PROMPT, ask


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(name, arguments, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=arguments)


def _response(blocks, stop_reason, tokens=(10, 5)):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class FakeClient:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _panel():
    return {
        "scores": pd.DataFrame({"balanced_accuracy": [0.4]}, index=["seasonal"]),
        "chance": pd.DataFrame({"p95": [0.462]}, index=["balanced_accuracy"]),
        "decision": {"promoted": False, "serving": "seasonal", "reason": "r"},
        "n_test": 35,
        "label_counts": {},
    }


def _ask(client, engine, question="How many months of data?", **kwargs):
    return ask(question, client=client, engine=engine, panel_loader=_panel, **kwargs)


def test_a_question_that_needs_no_tool_is_answered_directly(tiny_gold_engine):
    client = FakeClient([_response([_text("Two months.")], "end_turn")])
    result = _ask(client, tiny_gold_engine)
    assert result["ok"] is True
    assert result["answer"] == "Two months."
    assert result["tool_calls"] == []


def test_a_tool_call_is_run_and_its_result_sent_back(tiny_gold_engine):
    client = FakeClient(
        [
            _response([_tool_use("get_data_status", {})], "tool_use"),
            _response([_text("The data covers 2 months.")], "end_turn"),
        ]
    )
    result = _ask(client, tiny_gold_engine)
    assert result["answer"] == "The data covers 2 months."
    assert result["tool_calls"] == [("get_data_status", {})]

    follow_up = client.requests[1]["messages"][-1]
    assert follow_up["role"] == "user"
    block = follow_up["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "tu_1"
    assert '"n_months": 2' in block["content"]


def test_an_unknown_tool_is_reported_to_the_model_as_an_error(tiny_gold_engine):
    client = FakeClient(
        [
            _response([_tool_use("run_sql", {"sql": "SELECT 1"})], "tool_use"),
            _response([_text("I can't do that.")], "end_turn"),
        ]
    )
    result = _ask(client, tiny_gold_engine)
    assert result["ok"] is True
    block = client.requests[1]["messages"][-1]["content"][0]
    assert "error" in block["content"]


def test_the_loop_stops_after_the_round_limit(tiny_gold_engine):
    endless = [
        _response([_tool_use("get_data_status", {}, f"tu_{i}")], "tool_use") for i in range(10)
    ]
    client = FakeClient(endless)
    result = _ask(client, tiny_gold_engine, max_rounds=3)
    assert result["ok"] is False
    assert len(client.requests) == 3


def test_token_use_is_totalled_across_rounds(tiny_gold_engine):
    client = FakeClient(
        [
            _response([_tool_use("get_data_status", {})], "tool_use", tokens=(100, 20)),
            _response([_text("done")], "end_turn", tokens=(150, 30)),
        ]
    )
    result = _ask(client, tiny_gold_engine)
    assert result["input_tokens"] == 250
    assert result["output_tokens"] == 50


def test_an_api_failure_gives_a_friendly_message_not_a_stack_trace(tiny_gold_engine):
    client = FakeClient([RuntimeError("401 invalid x-api-key sk-secret")])
    result = _ask(client, tiny_gold_engine)
    assert result["ok"] is False
    assert "sk-secret" not in result["answer"]
    assert result["answer"]


def test_the_request_carries_the_model_tools_system_prompt_and_a_token_cap(tiny_gold_engine):
    client = FakeClient([_response([_text("ok")], "end_turn")])
    _ask(client, tiny_gold_engine, model="some-model", max_tokens=700)
    request = client.requests[0]
    assert request["model"] == "some-model"
    assert request["max_tokens"] == 700
    assert request["system"] == SYSTEM_PROMPT
    assert {tool["name"] for tool in request["tools"]} >= {"get_data_status"}
    assert request["messages"][0] == {"role": "user", "content": "How many months of data?"}


def test_the_system_prompt_sets_the_honesty_rules():
    text = SYSTEM_PROMPT.lower()
    assert "only" in text and "tool" in text  # answer from tool results only
    assert "not" in text and "chance" in text  # the model does not beat chance
    assert "lead" in text  # segment gaps are leads, not proof
