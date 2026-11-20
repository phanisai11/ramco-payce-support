"""Amazon Bedrock language-model adapter (Nova family) for payce.

Implements the ``LanguageModel`` protocol from ``ai_contracts`` using the
Bedrock ``Converse`` API via boto3 ``bedrock-runtime``. boto3 is imported
lazily so this module loads without the ``aws`` extra installed; inject a
fake client in tests.

Auth is the standard boto3 chain (env vars, shared config, IAM role,
SSO). Region resolution: ``region_name`` arg → ``AWS_REGION`` /
``AWS_DEFAULT_REGION`` → ``us-east-1``.

Mapping notes (Converse API):
- ``system`` role messages become the Converse ``system`` param.
- ``tool`` role messages become ``toolResult`` blocks inside a ``user``
  turn (Converse has no tool role).
- Assistant ``tool_calls`` become ``toolUse`` content blocks.
- ``response_schema`` is enforced via a forced single-tool call
  (``toolChoice={"tool": ...}``) — Converse has no JSON-schema response
  format; the tool input IS the structured output.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from payce_support.ai_contracts import (
    AiCallError,
    AiRateLimitError,
    AiResponseError,
    AiTransientError,
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    MessageRole,
    TokenUsage,
    ToolCall,
)

DEFAULT_REGION = "us-east-1"

# Titan Embed Text v1 emits fixed 1536-dim vectors — matches the
# ``app.shared_kb_chunk.embedding vector(1536)`` pgvector column, so no
# schema migration is needed when switching embeddings to Bedrock.
TITAN_EMBED_V1 = "amazon.titan-embed-text-v1"
TITAN_EMBED_V2 = "amazon.titan-embed-text-v2:0"
DEFAULT_EMBEDDING_MODEL = TITAN_EMBED_V1
_TITAN_V1_DIMS = 1536
_TITAN_V2_DIMS = (256, 512, 1024)

_NOVA_MODELS = {
    "micro": "amazon.nova-micro-v1:0",
    "lite": "amazon.nova-lite-v1:0",
    "pro": "amazon.nova-pro-v1:0",
}

_THROTTLE_CODES = {
    "ThrottlingException",
    "ServiceQuotaExceededException",
    "ModelTimeoutException",
    "ModelNotReadyException",
}
_TRANSIENT_CODES = {"InternalServerException", "ModelErrorException", "ServiceUnavailableException"}


def resolve_nova_model(tier: str = "lite") -> str:
    """Map a Nova tier name to its Bedrock model id."""
    key = tier.strip().lower()
    if key not in _NOVA_MODELS:
        raise ValueError(f"unknown Nova tier {tier!r}; expected one of {sorted(_NOVA_MODELS)}")
    return _NOVA_MODELS[key]


class BedrockChatModel:
    """``LanguageModel`` protocol implementation backed by Bedrock Converse."""

    def __init__(
        self,
        *,
        model_id: str,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        self._model_id = model_id
        self._region = (
            region_name
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or DEFAULT_REGION
        )
        self._client = client  # injectable for tests; created lazily

    @property
    def model_id(self) -> str:
        return self._model_id

    def _bedrock_client(self) -> Any:
        if self._client is None:
            import boto3  # lazy — requires the `aws` extra

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        kwargs = self._converse_kwargs(request)
        _, forced_tool = _tool_config(request)
        try:
            data = await asyncio.to_thread(self._bedrock_client().converse, **kwargs)
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise _map_bedrock_error(exc) from exc
        return _completion_result(data, self._model_id, forced_tool)

    # ── request translation ────────────────────────────────────────────

    def _converse_kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        system_texts: list[str] = []
        turns: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.SYSTEM:
                if message.content:
                    system_texts.append(message.content)
                continue
            turns.extend(_message_turns(message))
        merged = _merge_adjacent(turns)

        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": merged,
            "inferenceConfig": {
                "maxTokens": request.max_output_tokens,
                "temperature": request.temperature,
            },
        }
        if system_texts:
            kwargs["system"] = [{"text": "\n\n".join(system_texts)}]

        tool_spec, forced_tool = _tool_config(request)
        if tool_spec is not None:
            kwargs["toolConfig"] = tool_spec
        return kwargs


# ── message/tool translation helpers ────────────────────────────────────


def _message_turns(message: ChatMessage) -> list[dict[str, Any]]:
    """One ChatMessage → one or more Converse turns (tool results split)."""
    if message.role is MessageRole.TOOL:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": message.tool_call_id,
                            "content": [{"text": message.content or ""}],
                        }
                    }
                ],
            }
        ]
    if message.role is MessageRole.ASSISTANT:
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"text": message.content})
        for call in message.tool_calls:
            try:
                tool_input = json.loads(call.arguments_json)
            except json.JSONDecodeError as exc:
                raise AiResponseError(
                    f"assistant tool_call {call.call_id} has invalid JSON arguments"
                ) from exc
            blocks.append(
                {
                    "toolUse": {
                        "toolUseId": call.call_id,
                        "name": call.name,
                        "input": tool_input,
                    }
                }
            )
        return [{"role": "assistant", "content": blocks or [{"text": ""}]}]
    # USER (and any future roles treated as user text)
    return [{"role": "user", "content": [{"text": message.content or ""}]}]


def _merge_adjacent(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converse requires alternating roles — merge consecutive same-role turns."""
    merged: list[dict[str, Any]] = []
    for turn in turns:
        if merged and merged[-1]["role"] == turn["role"]:
            merged[-1]["content"].extend(turn["content"])
        else:
            merged.append({"role": turn["role"], "content": list(turn["content"])})
    return merged


def _tool_config(request: CompletionRequest) -> tuple[dict[str, Any] | None, str | None]:
    """Build Converse toolConfig; returns (config, forced_tool_name)."""
    if request.response_schema is not None:
        name = request.response_schema_name or "structured_output"
        return (
            {
                "tools": [
                    {
                        "toolSpec": {
                            "name": name,
                            "description": "Return the structured response.",
                            "inputSchema": {"json": request.response_schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": name}},
            },
            name,
        )
    if request.tools:
        specs = [
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": tool.input_schema},
                }
            }
            for tool in request.tools
        ]
        choice: dict[str, Any]
        if request.tool_choice.value == "none":
            return None, None
        if request.tool_choice.value == "required":
            choice = {"any": {}}
        elif request.tool_choice.value == "auto":
            choice = {"auto": {}}
        else:
            choice = {"auto": {}}
        return {"tools": specs, "toolChoice": choice}, None
    return None, None


# ── response translation ────────────────────────────────────────────────


def _completion_result(
    data: dict[str, Any], model_id: str, structured_tool: str | None = None
) -> CompletionResult:
    output = data.get("output") or {}
    message = output.get("message") or {}
    blocks = message.get("content") or []

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    structured_payload: dict[str, Any] | None = None
    for block in blocks:
        if "text" in block and block["text"]:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            use = block["toolUse"]
            if structured_tool is not None and use.get("name") == structured_tool:
                structured_payload = use.get("input")
                continue
            tool_calls.append(
                ToolCall(
                    call_id=str(use.get("toolUseId", "")),
                    name=str(use.get("name", "")),
                    arguments_json=json.dumps(use.get("input") or {}),
                )
            )

    text = "\n".join(text_parts) if text_parts else None
    if structured_payload is not None:
        # Forced structured-output tool: surface its input as JSON text so
        # downstream pydantic validation sees the same shape as OpenRouter's
        # json_schema mode.
        text = json.dumps(structured_payload)
        tool_calls = ()
    if text is None and not tool_calls:
        raise AiResponseError("Bedrock response contained neither text nor tool calls")

    usage_raw = data.get("usage") or {}
    input_tokens = int(usage_raw.get("inputTokens", 0))
    output_tokens = int(usage_raw.get("outputTokens", 0))
    return CompletionResult(
        text=text,
        tool_calls=tuple(tool_calls),
        model_id=model_id,
        finish_reason=data.get("stopReason"),
        provider_request_id=None,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def _map_bedrock_error(exc: Exception) -> AiCallError:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    if code in _THROTTLE_CODES:
        return AiTransientError(f"Bedrock transient/throttle: {code}: {exc}")
    if code in _TRANSIENT_CODES:
        return AiTransientError(f"Bedrock transient error: {code}: {exc}")
    if code in {"AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"}:
        return AiCallError(f"Bedrock auth/permission failure: {code}: {exc}")
    return AiResponseError(f"Bedrock call failed: {code or type(exc).__name__}: {exc}")


class BedrockEmbeddingModel:
    """``EmbeddingModel`` protocol implementation backed by Bedrock Titan.

    Uses ``bedrock-runtime:InvokeModel`` (Titan Embed models do not support
    the Converse API). Titan v1 is fixed at 1536 dims — matching the
    pgvector column; v2 supports 256/512/1024 via ``dimensions``.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_EMBEDDING_MODEL,
        region_name: str | None = None,
        default_dimensions: int | None = None,
        client: Any | None = None,
    ) -> None:
        model_id = model_id.strip()
        if "titan-embed-text" not in model_id:
            raise ValueError(
                f"unsupported Bedrock embedding model {model_id!r}; "
                "expected an amazon.titan-embed-text-* model"
            )
        self._model_id = model_id
        self._is_v2 = "titan-embed-text-v2" in model_id
        self._region = (
            region_name
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or DEFAULT_REGION
        )
        self._default_dimensions = default_dimensions
        self._client = client  # injectable for tests; created lazily

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        """Vector width this embedder produces by default."""
        return self._resolve_dimensions(None)

    def _bedrock_client(self) -> Any:
        if self._client is None:
            import boto3  # lazy — requires the `aws` extra

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _resolve_dimensions(self, requested: int | None) -> int:
        dims = requested or self._default_dimensions
        if self._is_v2:
            out = dims or 1024
            if out not in _TITAN_V2_DIMS:
                raise ValueError(
                    f"titan-embed-text-v2 supports dimensions {sorted(_TITAN_V2_DIMS)}, got {out}"
                )
            return out
        if dims is not None and dims != _TITAN_V1_DIMS:
            raise ValueError(
                f"{self._model_id} emits fixed {_TITAN_V1_DIMS}-dim vectors, got {dims}"
            )
        return _TITAN_V1_DIMS

    def _invoke_body(self, text: str, dimensions: int) -> bytes:
        payload: dict[str, Any] = {"inputText": text}
        if self._is_v2:
            payload["dimensions"] = dimensions
            payload["normalize"] = True
        return json.dumps(payload).encode("utf-8")

    async def _embed_one(self, text: str, dimensions: int) -> tuple[tuple[float, ...], int]:
        try:
            response = await asyncio.to_thread(
                self._bedrock_client().invoke_model,
                modelId=self._model_id,
                body=self._invoke_body(text, dimensions),
                contentType="application/json",
                accept="application/json",
            )
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise _map_bedrock_error(exc) from exc
        try:
            body = response["body"].read()
            payload = json.loads(body)
            vector = tuple(float(v) for v in payload["embedding"])
            tokens = int(payload.get("inputTextTokenCount", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise AiResponseError("Bedrock embedding response is malformed") from exc
        if len(vector) != dimensions:
            raise AiResponseError(
                f"Bedrock embedding returned {len(vector)} dims, expected {dimensions}"
            )
        return vector, tokens

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        dimensions = self._resolve_dimensions(request.dimensions)
        # Titan InvokeModel is single-text; bound concurrency to stay under
        # Bedrock TPS quotas on larger ingest batches.
        semaphore = asyncio.Semaphore(8)

        async def _bounded(text: str) -> tuple[tuple[float, ...], int]:
            async with semaphore:
                return await self._embed_one(text, dimensions)

        results = await asyncio.gather(*(_bounded(t) for t in request.texts))
        vectors = tuple(r[0] for r in results)
        tokens = sum(r[1] for r in results)
        return EmbeddingResult(
            vectors=vectors,
            model_id=self._model_id,
            usage=TokenUsage(
                input_tokens=tokens,
                output_tokens=0,
                total_tokens=tokens,
            ),
        )
