"""Provider-neutral contracts for language models, embeddings, tools, and retrieval."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, model_validator

from payce_support.models import AccessContext, ImmutableModel, Region


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolChoice(StrEnum):
    NONE = "none"
    AUTO = "auto"
    REQUIRED = "required"


class RetrievalScope(StrEnum):
    SHARED_KB = "shared_kb"
    TENANT_PRIVATE = "tenant_private"


class ToolCall(ImmutableModel):
    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    arguments_json: str


class ChatMessage(ImmutableModel):
    role: MessageRole
    content: str | None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def tool_messages_require_call_id(self) -> ChatMessage:
        if self.role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.tool_calls and self.role is not MessageRole.ASSISTANT:
            raise ValueError("only assistant messages may contain tool calls")
        return self


class ToolDefinition(ImmutableModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=1024)
    input_schema: dict[str, Any]


class TokenUsage(ImmutableModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class CompletionRequest(ImmutableModel):
    messages: tuple[ChatMessage, ...] = Field(min_length=1, max_length=64)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    response_schema_name: str | None = Field(default=None, max_length=64)
    response_schema: dict[str, Any] | None = None
    tools: tuple[ToolDefinition, ...] = Field(default=(), max_length=16)
    tool_choice: ToolChoice = ToolChoice.NONE

    @model_validator(mode="after")
    def validate_optional_features(self) -> CompletionRequest:
        if (self.response_schema_name is None) != (self.response_schema is None):
            raise ValueError("response schema name and schema must be supplied together")
        if self.tool_choice is not ToolChoice.NONE and not self.tools:
            raise ValueError("tool_choice requires at least one tool definition")
        return self


class CompletionResult(ImmutableModel):
    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    model_id: str
    finish_reason: str | None = None
    provider_request_id: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)


class EmbeddingRequest(ImmutableModel):
    texts: tuple[str, ...] = Field(min_length=1, max_length=128)
    input_type: str | None = Field(default=None, max_length=64)
    dimensions: int | None = Field(default=None, ge=1, le=65536)


class EmbeddingResult(ImmutableModel):
    vectors: tuple[tuple[float, ...], ...]
    model_id: str
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @model_validator(mode="after")
    def vectors_have_consistent_dimensions(self) -> EmbeddingResult:
        dimensions = {len(vector) for vector in self.vectors}
        if not self.vectors or len(dimensions) != 1 or 0 in dimensions:
            raise ValueError("embedding response must contain non-empty equal-sized vectors")
        return self


class RetrievalQuery(ImmutableModel):
    context: AccessContext
    text: str = Field(min_length=1, max_length=20_000)
    as_of: datetime
    scope: RetrievalScope = RetrievalScope.SHARED_KB
    snapshot_version: str = Field(min_length=1, max_length=128)
    embedding_profile: str = Field(min_length=1, max_length=128)
    minimum_score: float = Field(default=0.75, ge=0.0, le=1.0)
    top_k: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def as_of_is_timezone_aware(self) -> RetrievalQuery:
        if self.as_of.tzinfo is None:
            raise ValueError("retrieval as_of must include a timezone")
        return self


class RetrievedChunk(ImmutableModel):
    chunk_id: str = Field(min_length=1, max_length=256)
    kb_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=20_000)
    score: float = Field(ge=0.0, le=1.0)
    region: Region
    valid_from: datetime
    valid_to: datetime | None = None
    tenant_id: str | None = Field(default=None, pattern=r"^C-[0-9]{4,}$")
    snapshot_version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validity_window_is_aware_and_ordered(self) -> RetrievedChunk:
        if self.valid_from.tzinfo is None:
            raise ValueError("chunk valid_from must include a timezone")
        if self.valid_to is not None:
            if self.valid_to.tzinfo is None:
                raise ValueError("chunk valid_to must include a timezone")
            if self.valid_to <= self.valid_from:
                raise ValueError("chunk valid_to must be after valid_from")
        return self


class LanguageModel(Protocol):
    @property
    def model_id(self) -> str: ...

    async def complete(self, request: CompletionRequest) -> CompletionResult: ...


class EmbeddingModel(Protocol):
    @property
    def model_id(self) -> str: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...


class KnowledgeRetriever(Protocol):
    async def retrieve(
        self,
        query: RetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> tuple[RetrievedChunk, ...]: ...


class AiCallError(RuntimeError):
    """Base error for model-provider failures."""


class AiRateLimitError(AiCallError):
    pass


class AiTransientError(AiCallError):
    pass


class AiResponseError(AiCallError):
    pass
