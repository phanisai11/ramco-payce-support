"""Bedrock adapter tests — fake bedrock-runtime client, no AWS calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from payce_support.ai_contracts import (
    AiCallError,
    AiResponseError,
    ChatMessage,
    CompletionRequest,
    EmbeddingRequest,
    MessageRole,
    TokenUsage,
    ToolChoice,
    ToolDefinition,
)
from payce_support.bedrock import (
    TITAN_EMBED_V1,
    TITAN_EMBED_V2,
    BedrockChatModel,
    BedrockEmbeddingModel,
    resolve_nova_model,
    _merge_adjacent,
    _message_turns,
)


class FakeBedrockClient:
    """Stands in for boto3 bedrock-runtime; captures converse() kwargs."""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.response = response or {}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    def invoke_model(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        body = getattr(self, "invoke_body", None)
        if body is None:
            body = json.dumps(
                {"embedding": [0.1] * 1536, "inputTextTokenCount": 7}
            ).encode()
        return {"body": _FakeStream(body), "contentType": "application/json"}


class _FakeStream:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def _text_response(text: str, *, in_tok: int = 10, out_tok: int = 5) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": in_tok, "outputTokens": out_tok, "totalTokens": in_tok + out_tok},
    }


def _req(*messages: ChatMessage, **kw: Any) -> CompletionRequest:
    return CompletionRequest(messages=messages, **kw)


def test_model_id_property_and_tier_resolution() -> None:
    assert resolve_nova_model("lite") == "amazon.nova-lite-v1:0"
    assert resolve_nova_model("micro") == "amazon.nova-micro-v1:0"
    assert resolve_nova_model("PRO") == "amazon.nova-pro-v1:0"
    with pytest.raises(ValueError):
        resolve_nova_model("gpt")
    model = BedrockChatModel(model_id="amazon.nova-lite-v1:0", client=FakeBedrockClient())
    assert model.model_id == "amazon.nova-lite-v1:0"


def test_complete_maps_text_and_usage() -> None:
    client = FakeBedrockClient(_text_response("hello", in_tok=12, out_tok=7))
    model = BedrockChatModel(model_id="amazon.nova-lite-v1:0", client=client)

    result = asyncio.run(model.complete(_req(ChatMessage(role=MessageRole.USER, content="hi", tool_call_id=None, tool_calls=()))))

    assert result.text == "hello"
    assert result.usage == TokenUsage(input_tokens=12, output_tokens=7, total_tokens=19)
    assert result.finish_reason == "end_turn"
    # request translation sanity
    call = client.calls[0]
    assert call["modelId"] == "amazon.nova-lite-v1:0"
    assert call["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert call["inferenceConfig"]["maxTokens"] == 1024


def test_system_messages_become_system_param() -> None:
    client = FakeBedrockClient(_text_response("ok"))
    model = BedrockChatModel(model_id="m", client=client)
    asyncio.run(model.complete(_req(
        ChatMessage(role=MessageRole.SYSTEM, content="be terse", tool_call_id=None, tool_calls=()),
        ChatMessage(role=MessageRole.USER, content="hi", tool_call_id=None, tool_calls=()),
    )))
    call = client.calls[0]
    assert call["system"] == [{"text": "be terse"}]
    assert call["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]


def test_tool_calls_round_trip() -> None:
    client = FakeBedrockClient({
        "output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"id": "SHP-1"}}}
        ]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7},
    })
    model = BedrockChatModel(model_id="m", client=client)
    result = asyncio.run(model.complete(_req(
        ChatMessage(role=MessageRole.USER, content="find SHP-1", tool_call_id=None, tool_calls=()),
        tools=(ToolDefinition(name="lookup", description="d", input_schema={"type": "object"}),),
        tool_choice=ToolChoice.AUTO,
    )))
    assert result.tool_calls[0].name == "lookup"
    assert json.loads(result.tool_calls[0].arguments_json) == {"id": "SHP-1"}
    # toolConfig sent
    cfg = client.calls[0]["toolConfig"]
    assert cfg["tools"][0]["toolSpec"]["name"] == "lookup"


def test_response_schema_forces_structured_tool() -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    client = FakeBedrockClient({
        "output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "s1", "name": "answer_schema",
                          "input": {"answer": "Use Settings."}}}
        ]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 5, "outputTokens": 6, "totalTokens": 11},
    })
    model = BedrockChatModel(model_id="m", client=client)
    result = asyncio.run(model.complete(_req(
        ChatMessage(role=MessageRole.USER, content="q", tool_call_id=None, tool_calls=()),
        response_schema_name="answer_schema", response_schema=schema,
    )))
    # structured output surfaces as JSON text (same shape as OpenRouter json_schema)
    assert json.loads(result.text) == {"answer": "Use Settings."}
    assert result.tool_calls == ()
    cfg = client.calls[0]["toolConfig"]
    assert cfg["toolChoice"] == {"tool": {"name": "answer_schema"}}


def test_tool_role_message_maps_to_toolresult() -> None:
    turns = _message_turns(ChatMessage(
        role=MessageRole.TOOL, content="result text", tool_call_id="call-9", tool_calls=()))
    assert turns[0]["role"] == "user"
    assert turns[0]["content"][0]["toolResult"]["toolUseId"] == "call-9"


def test_adjacent_same_role_merged() -> None:
    turns = [
        {"role": "user", "content": [{"text": "a"}]},
        {"role": "user", "content": [{"text": "b"}]},
        {"role": "assistant", "content": [{"text": "c"}]},
    ]
    merged = _merge_adjacent(turns)
    assert len(merged) == 2
    assert merged[0]["content"] == [{"text": "a"}, {"text": "b"}]


def test_error_mapping() -> None:
    class FakeClientError(Exception):
        def __init__(self, code: str):
            super().__init__(code)
            self.response = {"Error": {"Code": code}}

    model = BedrockChatModel(model_id="m", client=FakeBedrockClient(error=FakeClientError("ThrottlingException")))
    with pytest.raises(AiCallError):
        asyncio.run(model.complete(_req(ChatMessage(role=MessageRole.USER, content="x", tool_call_id=None, tool_calls=()))))

    model2 = BedrockChatModel(model_id="m", client=FakeBedrockClient(error=FakeClientError("AccessDeniedException")))
    with pytest.raises(AiCallError):
        asyncio.run(model2.complete(_req(ChatMessage(role=MessageRole.USER, content="x", tool_call_id=None, tool_calls=()))))


def test_empty_response_raises() -> None:
    client = FakeBedrockClient({"output": {"message": {"content": []}}, "usage": {}})
    model = BedrockChatModel(model_id="m", client=client)
    with pytest.raises(AiResponseError):
        asyncio.run(model.complete(_req(ChatMessage(role=MessageRole.USER, content="x", tool_call_id=None, tool_calls=()))))


# ── BedrockEmbeddingModel (Titan via InvokeModel) ────────────────────────


def test_embedding_v1_batch_and_usage() -> None:
    client = FakeBedrockClient()
    model = BedrockEmbeddingModel(model_id=TITAN_EMBED_V1, client=client)
    assert model.dimensions == 1536

    result = asyncio.run(model.embed(EmbeddingRequest(texts=("alpha", "beta"))))

    assert len(result.vectors) == 2
    assert all(len(v) == 1536 for v in result.vectors)
    assert result.usage.input_tokens == 14  # 7 per text
    assert result.usage.total_tokens == 14
    assert len(client.calls) == 2  # Titan InvokeModel is single-text
    body = json.loads(client.calls[0]["body"])
    assert body == {"inputText": "alpha"}  # v1: no dimensions/normalize params
    assert client.calls[0]["modelId"] == TITAN_EMBED_V1


def test_embedding_v2_dimensions_and_normalize() -> None:
    client = FakeBedrockClient()
    client.invoke_body = json.dumps(
        {"embedding": [0.2] * 512, "inputTextTokenCount": 3}
    ).encode()
    model = BedrockEmbeddingModel(
        model_id=TITAN_EMBED_V2, default_dimensions=512, client=client
    )
    assert model.dimensions == 512

    result = asyncio.run(
        model.embed(EmbeddingRequest(texts=("q",), dimensions=512))
    )
    assert len(result.vectors[0]) == 512
    body = json.loads(client.calls[0]["body"])
    assert body == {"inputText": "q", "dimensions": 512, "normalize": True}


def test_embedding_rejects_non_titan_model() -> None:
    with pytest.raises(ValueError):
        BedrockEmbeddingModel(model_id="cohere.embed-english-v3", client=FakeBedrockClient())


def test_embedding_v1_rejects_non_1536_dimensions() -> None:
    model = BedrockEmbeddingModel(model_id=TITAN_EMBED_V1, client=FakeBedrockClient())
    with pytest.raises(ValueError):
        asyncio.run(model.embed(EmbeddingRequest(texts=("x",), dimensions=1024)))


def test_embedding_error_mapping() -> None:
    class FakeClientError(Exception):
        def __init__(self, code: str):
            super().__init__(code)
            self.response = {"Error": {"Code": code}}

    model = BedrockEmbeddingModel(
        model_id=TITAN_EMBED_V1,
        client=FakeBedrockClient(error=FakeClientError("ThrottlingException")),
    )
    with pytest.raises(AiCallError):
        asyncio.run(model.embed(EmbeddingRequest(texts=("x",))))
