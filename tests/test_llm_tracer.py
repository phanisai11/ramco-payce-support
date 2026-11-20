"""LLM tracer tests — JSONL trace records for success and failure paths."""

from __future__ import annotations

import asyncio
import json

import pytest

from payce_support.ai_contracts import (
    AiCallError,
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    MessageRole,
    TokenUsage,
    ToolCall,
    ToolChoice,
    ToolDefinition,
)
from payce_support.llm_tracer import TracingLanguageModel


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        messages=(ChatMessage(role=MessageRole.USER, content="hello"),),
        **kw,
    )


class _FakeModel:
    model_id = "fake-model-v1"

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


def _read_traces(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_success_trace_records_response_usage_and_latency(tmp_path):
    inner = _FakeModel(result=CompletionResult(
        text="dock slot confirmed",
        model_id="fake-model-v1",
        finish_reason="stop",
        usage=TokenUsage(input_tokens=12, output_tokens=7, total_tokens=19),
    ))
    tracer = TracingLanguageModel(inner, trace_path=tmp_path / "t.jsonl")

    result = asyncio.run(tracer.complete(_req(temperature=0.2)))

    assert result.text == "dock slot confirmed"
    assert inner.calls == 1
    assert tracer.model_id == "fake-model-v1"

    (trace,) = _read_traces(tmp_path / "t.jsonl")
    assert trace["ok"] is True
    assert trace["model_id"] == "fake-model-v1"
    assert trace["text"] == "dock slot confirmed"
    assert trace["finish_reason"] == "stop"
    assert trace["usage"] == {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}
    assert trace["latency_ms"] >= 0
    assert trace["messages"][0]["role"] == "user"
    assert trace["messages"][0]["content"] == "hello"
    assert trace["tool_calls"] is None
    assert trace["has_response_schema"] is False
    assert trace["temperature"] == 0.2
    assert trace["tool_choice"] == "none"


def test_tool_calls_and_tools_are_traced(tmp_path):
    tool = ToolDefinition(name="check_dock", description="check availability",
                          input_schema={"type": "object"})
    inner = _FakeModel(result=CompletionResult(
        text=None,
        tool_calls=(ToolCall(call_id="c1", name="check_dock",
                             arguments_json='{"dock":"D-4"}'),),
        model_id="fake-model-v1",
        finish_reason="tool_calls",
        usage=TokenUsage(input_tokens=30, output_tokens=11, total_tokens=41),
    ))
    tracer = TracingLanguageModel(inner, trace_path=tmp_path / "t.jsonl")

    req = _req(tools=(tool,), tool_choice=ToolChoice.AUTO)
    asyncio.run(tracer.complete(req))

    (trace,) = _read_traces(tmp_path / "t.jsonl")
    assert trace["ok"] is True
    assert trace["tool_choice"] == "auto"
    assert trace["tools"] == [{"name": "check_dock", "description": "check availability"}]
    assert trace["tool_calls"] == [
        {"call_id": "c1", "name": "check_dock", "arguments_json": '{"dock":"D-4"}'}
    ]


def test_error_is_traced_and_reraised(tmp_path):
    inner = _FakeModel(error=AiCallError("provider exploded"))
    tracer = TracingLanguageModel(inner, trace_path=tmp_path / "t.jsonl")

    with pytest.raises(AiCallError, match="provider exploded"):
        asyncio.run(tracer.complete(_req()))

    (trace,) = _read_traces(tmp_path / "t.jsonl")
    assert trace["ok"] is False
    assert "provider exploded" in trace["error"]
    assert trace["latency_ms"] >= 0
    assert "text" not in trace


def test_long_fields_are_truncated(tmp_path):
    inner = _FakeModel(result=CompletionResult(
        text="x" * 500, model_id="fake-model-v1"))
    tracer = TracingLanguageModel(inner, trace_path=tmp_path / "t.jsonl", max_chars=100)

    asyncio.run(tracer.complete(_req()))

    (trace,) = _read_traces(tmp_path / "t.jsonl")
    assert trace["text"].startswith("x" * 100)
    assert "chars]" in trace["text"]


def test_unwritable_path_does_not_break_call(tmp_path):
    inner = _FakeModel(result=CompletionResult(text="ok", model_id="fake-model-v1"))
    tracer = TracingLanguageModel(inner, trace_path=tmp_path / "missing" / "deep" / "t.jsonl")
    # parent mkdir handles this; use a path that is a file to force OSError
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    tracer = TracingLanguageModel(inner, trace_path=blocker / "t.jsonl")

    result = asyncio.run(tracer.complete(_req()))
    assert result.text == "ok"
