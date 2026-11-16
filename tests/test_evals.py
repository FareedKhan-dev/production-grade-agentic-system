"""The evals turn Langfuse traces into judge input without crashing on unusual traces."""

from types import SimpleNamespace

import evals.evaluator  # noqa: F401  (the CLI's imports resolve with the locked versions)
from evals.helpers import (
    format_messages,
    get_input_output,
)
from evals.metrics import metrics


def test_tool_calls_are_described_without_provider_specific_fields():
    # LangChain keeps tool calls in "tool_calls"; only some providers also fill additional_kwargs
    messages = [
        {"type": "human", "content": "How many people live in Lisbon?"},
        {"type": "ai", "content": "", "tool_calls": [{"name": "search", "args": {"query": "Lisbon"}, "id": "c1"}]},
        {"type": "tool", "name": "search", "tool_call_id": "c1", "content": "r" * 150},
        {"type": "ai", "content": "About 545,000."},
    ]
    formatted = format_messages(messages)
    assert 'tool search input: {"query": "Lisbon"} ' + "r" * 100 + "..." in formatted
    assert formatted.endswith("ai: About 545,000.")


def test_traces_without_messages_are_skipped():
    assert get_input_output(SimpleNamespace(output={"messages": []})) == (None, None)
    assert get_input_output(SimpleNamespace(output="not a dict")) == (None, None)


def test_the_last_message_is_the_output():
    trace = SimpleNamespace(output={"messages": [{"type": "human", "content": "Hi"}, {"type": "ai", "content": "Hello"}]})
    assert get_input_output(trace) == ("human: Hi", "ai: Hello")


def test_every_metric_has_a_prompt():
    assert [m["name"] for m in metrics] == ["conciseness", "hallucination", "helpfulness", "relevancy", "toxicity"]
    assert all(m["prompt"].strip() for m in metrics)
