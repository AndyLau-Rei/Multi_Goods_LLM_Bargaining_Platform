"""Provider-neutral raw LLM generation adapters."""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AdapterError(RuntimeError):
    """Raised when a provider call cannot produce raw output."""


@dataclass(frozen=True)
class GenerationResult:
    """Raw model output and provider-independent generation metadata."""

    text: str
    metadata: dict[str, Any]


class BaseLLMAdapter(ABC):
    """Interface consumed by future agent call flow code."""

    @abstractmethod
    def generate(self, prompt: str, action_schema: dict) -> GenerationResult:
        """Generate raw text; parsing and validation belong to later tickets."""


class MockLLMAdapter(BaseLLMAdapter):
    """Return queued raw outputs without making a network call."""

    def __init__(self, responses: list[str] | tuple[str, ...], model_version: str = "mock"):
        self._responses = deque(responses)
        self.model_version = model_version

    def generate(self, prompt: str, action_schema: dict) -> GenerationResult:
        started = time.perf_counter()
        if not self._responses:
            raise AdapterError("Mock response queue is empty")
        text = self._responses.popleft()
        if not isinstance(text, str):
            raise AdapterError("Mock response must be a string")
        return GenerationResult(
            text=text,
            metadata=_metadata(
                self.model_version,
                prompt,
                text,
                (time.perf_counter() - started) * 1000,
            ),
        )


class OpenAIAdapter(BaseLLMAdapter):
    """Call an OpenAI-compatible chat-completions endpoint using urllib only."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: str | None = None,
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        timeout: float = 30.0,
        max_api_retries: int = 3,
        backoff_seconds: float = 0.5,
    ):
        if max_api_retries < 0:
            raise ValueError("max_api_retries must be non-negative")
        self.model = model
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_api_retries = max_api_retries
        self.backoff_seconds = backoff_seconds

    def generate(self, prompt: str, action_schema: dict) -> GenerationResult:
        if not self.api_key:
            raise AdapterError("LLM_API_KEY is not configured")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "action",
                    "strict": False,
                    "schema": action_schema,
                },
            },
        }
        started = time.perf_counter()
        for attempt in range(self.max_api_retries + 1):
            try:
                response = self._request_json(payload)
                break
            except Exception as exc:
                if not _retryable(exc) or attempt >= self.max_api_retries:
                    raise AdapterError("LLM API request failed") from exc
                time.sleep(self.backoff_seconds * (2**attempt))
        text = _response_text(response)
        usage = response.get("usage") or {}
        metadata = _metadata(
            response.get("model", self.model),
            prompt,
            text,
            (time.perf_counter() - started) * 1000,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        metadata["api_attempts"] = attempt + 1
        return GenerationResult(text=text, metadata=metadata)

    def _request_json(self, payload: dict) -> dict:
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def _retryable(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code == 408 or error.code == 429 or error.code >= 500
    return isinstance(error, (URLError, TimeoutError, ConnectionError))


def _response_text(response: dict) -> str:
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AdapterError("LLM API response has no message content") from exc
    if not isinstance(text, str):
        raise AdapterError("LLM API message content is not text")
    return text


def _metadata(
    model_version: str,
    prompt: str,
    text: str,
    latency_ms: float,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> dict[str, Any]:
    return {
        "model_version": model_version,
        "prompt_tokens": prompt_tokens if prompt_tokens is not None else _estimate_tokens(prompt),
        "completion_tokens": completion_tokens
        if completion_tokens is not None
        else _estimate_tokens(text),
        "latency_ms": max(0.0, latency_ms),
    }


def _estimate_tokens(text: str) -> int:
    return len(text.split())
