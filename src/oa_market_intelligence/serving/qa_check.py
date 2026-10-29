"""Check the question box against the question set (DL-88).

Two checks, one file:

- `check_calls` (offline, no key): for every in-scope question, run the tool calls a good answer
  would make on the committed data and confirm they return the facts the answer needs. CI runs
  this, so a change to the data or the tools that would leave a question unanswerable fails.
- `run_live` and `main` (`oa-ask-check`, needs ANTHROPIC_API_KEY in the environment): ask Claude
  every question and screen each answer with `grade`. The grade is a rough screen on words and
  on figures that were not in the data looked up; it does not replace reading the answers. The
  key is read from the environment and never printed or written.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from oa_market_intelligence.serving import data, qa, qa_tools

QUESTION_SET = Path(__file__).resolve().parents[3] / "data" / "reference" / "qa_question_set.json"


def load_questions(path: Path = QUESTION_SET) -> list[dict]:
    questions = json.loads(Path(path).read_text("utf-8"))["questions"]
    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("a question id appears twice")
    for q in questions:
        if q["kind"] not in ("in_scope", "refuse"):
            raise ValueError(f"{q['id']}: kind must be in_scope or refuse")
        if q["kind"] == "in_scope" and not q["calls"]:
            raise ValueError(f"{q['id']}: an in-scope question needs the calls a good answer makes")
        for call in q["calls"]:
            if call["tool"] not in qa_tools.TOOLS:
                raise ValueError(f"{q['id']}: unknown tool {call['tool']!r}")
    return questions


def check_calls(question: dict, snapshot: qa_tools.Snapshot) -> list[str]:
    """Problems found running the question's calls on the data: an error, or a missing fact."""
    problems = []
    text = ""
    for call in question["calls"]:
        output = qa_tools.run_tool(call["tool"], call["args"], snapshot=snapshot)
        if "error" in output:
            problems.append(f"{call['tool']} returned an error: {output['error']}")
        text += json.dumps(output, allow_nan=False)
    for fact in question["facts"]:
        if fact not in text:
            problems.append(f"the data it looks up does not contain {fact!r}")
    return problems


def grade(question: dict, result: dict) -> list[str]:
    """Problems with a real answer: a rough screen on words, tools and unverified figures."""
    problems = []
    if not result["ok"]:
        return [f"no answer: {result['answer']}"]
    answer = result["answer"].lower()
    include = [w.lower() for w in question.get("include_any", [])]
    if include and not any(w in answer for w in include):
        problems.append(f"none of {question['include_any']} appears in the answer")
    for word in question.get("avoid", []):
        if word.lower() in answer:
            problems.append(f"the answer contains {word!r}")
    if question["kind"] == "in_scope":
        wanted = {call["tool"] for call in question["calls"]}
        used = {name for name, _ in result["tool_calls"]}
        if not wanted & used:
            problems.append(f"it did not look anything up (expected one of {sorted(wanted)})")
        if result["unchecked_numbers"]:
            problems.append(
                f"figures not found in the data it looked up: {result['unchecked_numbers']}"
            )
    elif result["tool_calls"] and question["category"] in ("injection", "sql", "patient_level"):
        problems.append("it ran tools for a request it should refuse")
    return problems


def run_live(client, snapshot: qa_tools.Snapshot, questions: list[dict], model: str) -> list[dict]:
    """Ask every question with a real client; returns one record per question."""
    records = []
    for question in questions:
        result = qa.ask(question["question"], client=client, snapshot=snapshot, model=model)
        records.append(
            {
                "id": question["id"],
                "kind": question["kind"],
                "category": question["category"],
                "question": question["question"],
                "answer": result["answer"],
                "tools": [name for name, _ in result["tool_calls"]],
                "sources": result["sources"],
                "unchecked_numbers": result["unchecked_numbers"],
                "tokens": result["input_tokens"] + result["output_tokens"],
                "problems": grade(question, result),
            }
        )
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ask Claude every question in the question set.")
    parser.add_argument("--model", default=os.environ.get("CLAUDE_MODEL", qa.DEFAULT_MODEL))
    parser.add_argument("--only", help="comma-separated question ids, e.g. q01,q05")
    parser.add_argument("--out", help="write the full records to this JSON file")
    args = parser.parse_args(argv)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("Set ANTHROPIC_API_KEY in your environment first (it is never printed or saved).")
        return 2
    import anthropic

    questions = load_questions()
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    engine = data.open_engine()
    try:
        snapshot = qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()
    records = run_live(anthropic.Anthropic(api_key=key), snapshot, questions, args.model)
    for record in records:
        flag = "OK  " if not record["problems"] else "CHECK"
        print(f"[{flag}] {record['id']} {record['question']}")
        for problem in record["problems"]:
            print(f"        - {problem}")
    flagged = sum(1 for r in records if r["problems"])
    tokens = sum(r["tokens"] for r in records)
    print(
        f"\n{len(records) - flagged} of {len(records)} passed the screen; {tokens:,} tokens used."
    )
    if args.out:
        Path(args.out).write_text(json.dumps(records, indent=1), "utf-8")
        print(f"Full answers written to {args.out}")
    return 0 if not flagged else 1


if __name__ == "__main__":
    raise SystemExit(main())
