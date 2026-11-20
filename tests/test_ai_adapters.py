"""Provider adapter and bounded RAG tests; no external network calls."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from payce_support.ai_contracts import (
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    MessageRole,
    RetrievalQuery,
    RetrievalScope,
    RetrievedChunk,
    TokenUsage,
    ToolChoice,
    ToolDefinition,
)
from payce_support.models import AccessContext, Lane, Region
from payce_support.openrouter import (
    OpenRouterChatModel,
    OpenRouterClient,
    OpenRouterEmbeddingModel,
)
from payce_support.postgres_retrieval import PostgresKnowledgeRetriever, _vector_literal
from payce_support.rag import (
    InMemoryRagResultCache,
    RagComposer,
    RagIntent,
    RagPolicy,
    RagRequest,
    RagStatus,
)

_NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def test_openrouter_chat_maps_structured_output_and_usage() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "generation-1",
                "model": "google/gemini-3-flash",
                "choices": [
                    {
                        "message": {"content": '{"answer":"Use Settings.","citations":["c1"]}'},
                        "finish_reason": "stop",
                    },
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 8,
                    "total_tokens": 28,
                },
            },
        )

    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://openrouter.ai/api/v1",
        )
        transport = OpenRouterClient(api_key="secret", client=client)
        model = OpenRouterChatModel(
            transport=transport,
            model_id="google/gemini-3-flash",
        )
        result = await model.complete(
            CompletionRequest(
                messages=(ChatMessage(role=MessageRole.USER, content="How?"),),
                response_schema_name="answer",
                response_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            ),
        )
        await client.aclose()

        assert result.usage.total_tokens == 28
        assert result.provider_request_id == "generation-1"

    asyncio.run(run())
    assert captured["authorization"] == "Bearer secret"
    assert captured["body"]["stream"] is False
    assert captured["body"]["response_format"]["type"] == "json_schema"


def test_openrouter_tool_call_is_returned_but_not_executed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == "auto"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "retrieve_kb",
                                        "arguments": '{"query":"approver"}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            },
        )

    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://openrouter.ai/api/v1",
        )
        transport = OpenRouterClient(api_key="secret", client=client)
        model = OpenRouterChatModel(transport=transport, model_id="model")
        result = await model.complete(
            CompletionRequest(
                messages=(ChatMessage(role=MessageRole.USER, content="How?"),),
                tools=(
                    ToolDefinition(
                        name="retrieve_kb",
                        description="Retrieve approved knowledge",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                ),
                tool_choice=ToolChoice.AUTO,
            ),
        )
        await client.aclose()
        assert result.text is None
        assert result.tool_calls[0].name == "retrieve_kb"

    asyncio.run(run())


def test_openrouter_retries_rate_limit_at_most_configured_attempts() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://openrouter.ai/api/v1",
        )
        transport = OpenRouterClient(
            api_key="secret",
            client=client,
            max_attempts=2,
            sleeper=sleeper,
        )
        model = OpenRouterChatModel(transport=transport, model_id="model")
        result = await model.complete(
            CompletionRequest(
                messages=(ChatMessage(role=MessageRole.USER, content="hello"),),
            ),
        )
        await client.aclose()
        assert result.text == "ok"

    asyncio.run(run())
    assert attempts == 2
    assert delays == [0.25]


def test_openrouter_embedding_batches_inputs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["input"] == ["one", "two"]
        assert body["dimensions"] == 3
        return httpx.Response(
            200,
            json={
                "model": "openai/text-embedding-3-small",
                "data": [
                    {"index": 1, "embedding": [4.0, 5.0, 6.0]},
                    {"index": 0, "embedding": [1.0, 2.0, 3.0]},
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://openrouter.ai/api/v1",
        )
        transport = OpenRouterClient(api_key="secret", client=client)
        model = OpenRouterEmbeddingModel(
            transport=transport,
            model_id="openai/text-embedding-3-small",
            default_dimensions=3,
        )
        result = await model.embed(EmbeddingRequest(texts=("one", "two")))
        await client.aclose()
        assert result.vectors[0] == (1.0, 2.0, 3.0)
        assert result.vectors[1] == (4.0, 5.0, 6.0)

    asyncio.run(run())


class _FakeEmbeddingModel:
    model_id = "embedding-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self.calls += 1
        return EmbeddingResult(vectors=((0.1, 0.2, 0.3),), model_id=self.model_id)


class _FakeRetriever:
    def __init__(self, chunks: tuple[RetrievedChunk, ...]) -> None:
        self.calls = 0
        self.chunks = chunks
        self.last_query: Any = None

    async def retrieve(self, query: Any, query_vector: Any) -> tuple[RetrievedChunk, ...]:
        self.calls += 1
        self.last_query = query
        assert query_vector == (0.1, 0.2, 0.3)
        return self.chunks


class _FakeLanguageModel:
    model_id = "chat-v1"

    def __init__(self, text: str) -> None:
        self.calls = 0
        self.text = text
        self.last_request: CompletionRequest | None = None

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        self.last_request = request
        return CompletionResult(
            text=self.text,
            model_id=self.model_id,
            usage=TokenUsage(input_tokens=30, output_tokens=10, total_tokens=40),
        )


def _access_context() -> AccessContext:
    return AccessContext(
        principal_id="agent-1",
        tenant_id="C-1001",
        region=Region.INDIA,
    )


def _chunk(*, region: Region = Region.INDIA, tenant_id: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="chunk-1",
        kb_id="KB-100",
        text="Open Settings, then choose Approvers.",
        score=0.91,
        region=region,
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        tenant_id=tenant_id,
        snapshot_version="kb-2026-11",
    )


def _rag_request() -> RagRequest:
    return RagRequest(
        context=_access_context(),
        question_masked="How do I add an approver?",
        intent=RagIntent.HOW_TO,
        lane=Lane.DRAFT_FOR_AGENT,
        as_of=_NOW,
    )


def test_rag_composes_once_and_cache_skips_all_paid_calls() -> None:
    embedding = _FakeEmbeddingModel()
    retriever = _FakeRetriever((_chunk(),))
    language_model = _FakeLanguageModel(
        '{"answer":"Open Settings and choose Approvers.","citations":["chunk-1"]}',
    )
    composer = RagComposer(
        embedding_model=embedding,
        retriever=retriever,
        language_model=language_model,
        cache=InMemoryRagResultCache(),
        policy=RagPolicy(
            prompt_version="prompt-v1",
            kb_snapshot_version="kb-2026-11",
            embedding_profile="embed-1536-v1",
            hyde=False,
        ),
        clock=lambda: _NOW,
    )

    async def run() -> None:
        first = await composer.compose(_rag_request())
        second = await composer.compose(_rag_request())

        assert first.status is RagStatus.ANSWERED
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert second.answer == first.answer

    asyncio.run(run())
    assert embedding.calls == 1
    assert retriever.calls == 1
    assert language_model.calls == 1
    assert retriever.last_query.context.tenant_id == "C-1001"
    assert language_model.last_request is not None
    assert language_model.last_request.temperature == 0.0


def test_rag_wrong_region_fails_closed_before_language_model() -> None:
    embedding = _FakeEmbeddingModel()
    retriever = _FakeRetriever((_chunk(region=Region.UAE),))
    language_model = _FakeLanguageModel(
        '{"answer":"unsafe","citations":["chunk-1"]}',
    )
    composer = RagComposer(
        embedding_model=embedding,
        retriever=retriever,
        language_model=language_model,
        cache=InMemoryRagResultCache(),
        policy=RagPolicy(
            prompt_version="prompt-v1",
            kb_snapshot_version="kb-2026-11",
            embedding_profile="embed-1536-v1",
            hyde=False,
        ),
        clock=lambda: _NOW,
    )

    result = asyncio.run(composer.compose(_rag_request()))

    assert result.status is RagStatus.ABSTAINED
    assert result.reason_code == "RETRIEVAL_SCOPE_VIOLATION"
    assert language_model.calls == 0


def test_rag_rejects_unknown_citation() -> None:
    language_model = _FakeLanguageModel(
        '{"answer":"unsafe","citations":["not-retrieved"]}',
    )
    composer = RagComposer(
        embedding_model=_FakeEmbeddingModel(),
        retriever=_FakeRetriever((_chunk(),)),
        language_model=language_model,
        cache=InMemoryRagResultCache(),
        policy=RagPolicy(
            prompt_version="prompt-v1",
            kb_snapshot_version="kb-2026-11",
            embedding_profile="embed-1536-v1",
            hyde=False,
        ),
        clock=lambda: _NOW,
    )

    result = asyncio.run(composer.compose(_rag_request()))

    assert result.status is RagStatus.ABSTAINED
    assert result.reason_code == "UNVERIFIED_CITATION"


def test_rag_is_not_available_to_status_lane() -> None:
    with pytest.raises(ValidationError):
        RagRequest(
            context=_access_context(),
            question_masked="Where is payroll?",
            intent=RagIntent.PROCESS,
            lane=Lane.STATUS,
            as_of=_NOW,
        )


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeCursor:
    def __init__(self) -> None:
        self.sql = ""
        self.parameters: tuple[Any, ...] = ()

    async def execute(self, sql: str, parameters: tuple[Any, ...]) -> None:
        self.sql = sql
        self.parameters = parameters

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return [
            (
                "chunk-db-1",
                "KB-200",
                "Approved masked content",
                0.93,
                "IN",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                None,
                "kb-2026-11",
            ),
        ]


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _AsyncContext:
        return _AsyncContext(self._cursor)


class _FakePool:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._connection = _FakeConnection(cursor)

    def connection(self) -> _AsyncContext:
        return _AsyncContext(self._connection)


def test_postgres_retriever_parameterizes_vector_and_filters() -> None:
    cursor = _FakeCursor()
    retriever = PostgresKnowledgeRetriever(pool=_FakePool(cursor))
    query = RetrievalQuery(
        context=_access_context(),
        text="How do I add an approver?",
        as_of=_NOW,
        scope=RetrievalScope.SHARED_KB,
        snapshot_version="kb-2026-11",
        embedding_profile="embed-1536-v1",
        minimum_score=0.8,
        top_k=3,
    )

    chunks = asyncio.run(retriever.retrieve(query, (0.1, 0.2, 0.3)))

    assert chunks[0].chunk_id == "chunk-db-1"
    assert chunks[0].region is Region.INDIA
    assert "region = %s" in cursor.sql
    assert "valid_from <= %s" in cursor.sql
    assert cursor.parameters[0] == "[0.10000000000000001,0.20000000000000001,0.29999999999999999]"
    assert cursor.parameters[1:4] == ("IN", "kb-2026-11", "embed-1536-v1")
    assert cursor.parameters[7:] == (3, 0.8)


def test_vector_literal_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        _vector_literal((float("nan"),))
