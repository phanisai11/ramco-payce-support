"""Async OpenRouter adapters implementing provider-neutral AI contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from payce_support.ai_contracts import (
    AiRateLimitError,
    AiResponseError,
    AiTransientError,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    ChatMessage,
    MessageRole,
    TokenUsage,
    ToolCall,
)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class OpenRouterClient:
    """Shared pooled transport used by chat and embedding adapters."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        app_url: str | None = None,
        app_name: str | None = None,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenRouter API key is required")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._max_attempts = max_attempts
        self._sleeper = sleeper
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if app_url:
            self._headers["HTTP-Referer"] = app_url
        if app_name:
            self._headers["X-Title"] = app_name

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_status: int | None = None
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.post(
                    path,
                    headers=self._headers,
                    json=payload,
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                if attempt + 1 == self._max_attempts:
                    raise AiTransientError("OpenRouter network request failed") from exc
                await self._backoff(attempt)
                continue

            last_status = response.status_code
            if response.status_code < 400:
                try:
                    result = response.json()
                except ValueError as exc:
                    raise AiResponseError("OpenRouter returned invalid JSON") from exc
                if not isinstance(result, dict):
                    raise AiResponseError("OpenRouter response must be a JSON object")
                return result

            if response.status_code not in _RETRYABLE_STATUS_CODES:
                raise AiResponseError(
                    f"OpenRouter rejected request with status {response.status_code}",
                )
            if attempt + 1 < self._max_attempts:
                await self._backoff(attempt)

        if last_status == 429:
            raise AiRateLimitError("OpenRouter rate limit exceeded")
        raise AiTransientError(f"OpenRouter unavailable after status {last_status}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _backoff(self, attempt: int) -> None:
        await self._sleeper(0.25 * (2**attempt))


class OpenRouterChatModel:
    def __init__(self, *, transport: OpenRouterClient, model_id: str) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        self._transport = transport
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [_message_payload(message) for message in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema_name,
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
            payload["tool_choice"] = request.tool_choice.value

        data = await self._transport.post("/chat/completions", payload)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiResponseError("OpenRouter chat response missing first choice") from exc

        tool_calls = tuple(_tool_call(item) for item in message.get("tool_calls", ()))
        text = message.get("content")
        if text is not None and not isinstance(text, str):
            raise AiResponseError("OpenRouter message content must be text or null")
        if text is None and not tool_calls:
            raise AiResponseError("OpenRouter response contained neither text nor tool calls")

        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            model_id=str(data.get("model", self._model_id)),
            finish_reason=choice.get("finish_reason"),
            provider_request_id=_optional_string(data.get("id")),
            usage=_token_usage(data.get("usage")),
        )


class OpenRouterEmbeddingModel:
    def __init__(
        self,
        *,
        transport: OpenRouterClient,
        model_id: str,
        default_dimensions: int | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        if default_dimensions is not None and default_dimensions < 1:
            raise ValueError("default_dimensions must be positive")
        self._transport = transport
        self._model_id = model_id
        self._default_dimensions = default_dimensions

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "input": list(request.texts),
            "encoding_format": "float",
        }
        dimensions = request.dimensions or self._default_dimensions
        if dimensions is not None:
            payload["dimensions"] = dimensions
        if request.input_type is not None:
            payload["input_type"] = request.input_type

        data = await self._transport.post("/embeddings", payload)
        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            vectors = tuple(
                tuple(float(value) for value in item["embedding"])
                for item in items
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AiResponseError("OpenRouter embedding response is malformed") from exc
        if len(vectors) != len(request.texts):
            raise AiResponseError("OpenRouter returned the wrong number of embeddings")

        return EmbeddingResult(
            vectors=vectors,
            model_id=str(data.get("model", self._model_id)),
            usage=_token_usage(data.get("usage")),
        )


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.role is MessageRole.TOOL:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.call_id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments_json,
                },
            }
            for tool_call in message.tool_calls
        ]
    return payload


def _tool_call(item: Any) -> ToolCall:
    try:
        function = item["function"]
        arguments = function["arguments"]
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, separators=(",", ":"), sort_keys=True)
        return ToolCall(
            call_id=str(item["id"]),
            name=str(function["name"]),
            arguments_json=arguments,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AiResponseError("OpenRouter tool call is malformed") from exc


def _token_usage(raw: Any) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(raw.get("prompt_tokens", 0)),
        output_tokens=int(raw.get("completion_tokens", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
    )


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None
