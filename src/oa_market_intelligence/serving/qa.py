"""The Claude question loop: send a question, run the tools Claude asks for, return the answer.

`client` is an `anthropic.Anthropic` instance (or any object with `messages.create`); it is
injected so tests run without a network or a key. This module does not import `anthropic`.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import Engine

from oa_market_intelligence.serving.qa_tools import TOOL_DEFINITIONS, run_tool

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_ROUNDS = 5
DEFAULT_MAX_TOKENS = 800

SYSTEM_PROMPT = """You are the analytics assistant on a public site about Zilretta (a branded \
injectable for knee osteoarthritis) visit-share intelligence, built from IQVIA NMTA data.

Rules:
- Answer ONLY from the results of the tools you are given. Call a tool whenever a question \
needs a number. If the tools cannot answer, say so plainly; do not guess or use outside knowledge \
about Zilretta's sales or the market.
- You see aggregate summaries only. You cannot see or query individual patient visits, and you \
must not claim to.
- Be honest about the models. The direction models do not beat chance on the held-out months; \
the site serves a simple baseline for that reason. Never present a forecast as reliable unless \
get_model_results says a trained model was promoted.
- Segment gaps (observed vs expected share) are leads to investigate, not proof of an opportunity.
- Keep answers short and plain, quote the numbers you used, and mention the months covered.
"""

_FRIENDLY_FAILURE = (
    "The assistant is unavailable right now. The tables and charts on this page still work; "
    "please try the question again later."
)
_ROUND_LIMIT = "I could not finish answering that within the allowed number of steps."


def _text_of(response) -> str:
    return "".join(block.text for block in response.content if block.type == "text").strip()


def ask(
    question: str,
    *,
    client,
    engine: Engine,
    panel_loader: Callable[[], dict],
    model: str = DEFAULT_MODEL,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict:
    """Answer one question. Returns {ok, answer, tool_calls, input_tokens, output_tokens}."""
    messages: list[dict] = [{"role": "user", "content": question}]
    result = {"ok": False, "answer": _ROUND_LIMIT, "tool_calls": [],
              "input_tokens": 0, "output_tokens": 0}

    for _ in range(max_rounds):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )
        except Exception:  # noqa: BLE001 - never show the visitor an API error or key fragment
            result["answer"] = _FRIENDLY_FAILURE
            return result

        result["input_tokens"] += response.usage.input_tokens
        result["output_tokens"] += response.usage.output_tokens

        if response.stop_reason != "tool_use":
            result["ok"] = True
            result["answer"] = _text_of(response) or "I do not have an answer for that."
            return result

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            arguments = dict(block.input) if isinstance(block.input, dict) else {}
            result["tool_calls"].append((block.name, arguments))
            output = run_tool(block.name, arguments, engine=engine, panel_loader=panel_loader)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, allow_nan=False),
                    **({"is_error": True} if "error" in output else {}),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return result
