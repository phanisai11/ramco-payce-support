"""LangSmith-style LLM call tracing for payce.

Wraps any ``LanguageModel`` and records every ``complete()`` call as a
JSON line — timestamp, model, messages, tool defs, response text/tool
calls, token usage, latency, finish reason, and errors — to a JSONL file
(``PAYCE_LLM_TRACE_PATH``, default ``data/llm_traces.jsonl``) plus a
structured log line. The file ships to CloudWatch via the agent config;
keeps the raw trace durable and greppable.

Prompt/response payloads are truncated (``PAYCE_LLM_TRACE_MAX_CHARS``,
default 8000 per field) so traces stay bounded.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from payce_support.ai_contracts import (
    AiCallError,
    ChatMessage,
    CompletionRequest,
    CompletionResult,
)

logger = logging.getLogger("payce_support.llm_tracer")

_DEFAULT_TRACE_PATH = "data/llm_traces.jsonl"


def _truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…[+{len(value) - limit} chars]"
    return value


def _message_dict(m: ChatMessage) -> dict[str, Any]:
    return {
        "role": m.role.value,
        "content": _truncate(m.content, 8000),
        "tool_call_id": m.tool_call_id,
        "tool_calls": [
            {"call_id": tc.call_id, "name": tc.name,
             "arguments_json": _truncate(tc.arguments_json, 4000)}
            for tc in m.tool_calls
        ] or None,
    }


class TracingLanguageModel:
    """``LanguageModel`` decorator that records every call to JSONL."""

    def __init__(
        self,
        inner: Any,
        *,
        trace_path: str | Path | None = None,
        max_chars: int | None = None,
    ) -> None:
        self._inner = inner
        self._path = Path(
            trace_path
            or os.environ.get("PAYCE_LLM_TRACE_PATH")
            or _DEFAULT_TRACE_PATH
        )
        self._max_chars = max_chars or int(
            os.environ.get("PAYCE_LLM_TRACE_MAX_CHARS") or 8000
        )

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def inner(self) -> Any:
        return self._inner

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        started = time.monotonic()
        trace: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model_id": self.model_id,
            "messages": [_message_dict(m) for m in request.messages],
            "max_output_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "tool_choice": request.tool_choice.value,
            "tools": [
                {"name": t.name, "description": _truncate(t.description, 500)}
                for t in request.tools
            ] or None,
            "has_response_schema": request.response_schema is not None,
        }
        try:
            result = await self._inner.complete(request)
        except AiCallError as exc:
            trace.update({
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            })
            self._write(trace)
            raise

        trace.update({
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "text": _truncate(result.text, self._max_chars),
            "tool_calls": [
                {"call_id": tc.call_id, "name": tc.name,
                 "arguments_json": _truncate(tc.arguments_json, self._max_chars)}
                for tc in result.tool_calls
            ] or None,
            "finish_reason": result.finish_reason,
            "usage": {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "total_tokens": result.usage.total_tokens,
            },
        })
        self._write(trace)
        return result

    def _write(self, trace: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(trace, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("llm trace write failed", exc_info=True)
        logger.info(
            "llm_call model=%s ok=%s latency_ms=%s in=%s out=%s",
            trace.get("model_id"), trace.get("ok"), trace.get("latancy_ms"),
            (trace.get("usage") or {}).get("input_tokens"),
            (trace.get("usage") or {}).get("output_tokens"),
        )
