import json

from bargaining_platform.model_adapter import (
    GeminiAdapter,
    GenerationResult,
    MockLLMAdapter,
    OpenAIAdapter,
)


def test_mock_adapter_returns_queued_raw_output_and_metadata():
    adapter = MockLLMAdapter(['{"action_type":"WALK_AWAY"}'], model_version="mock-v1")

    result = adapter.generate("offer", {"type": "object"})

    assert isinstance(result, GenerationResult)
    assert result.text == '{"action_type":"WALK_AWAY"}'
    assert result.metadata["model_version"] == "mock-v1"
    assert result.metadata["prompt_tokens"] == 1
    assert result.metadata["completion_tokens"] == 1
    assert result.metadata["latency_ms"] >= 0


def test_gemini_adapter_uses_header_key_and_parses_json_response(monkeypatch):
    from io import BytesIO

    calls = []

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_urlopen(request, timeout):
        calls.append(request)
        return Response(json.dumps({
            "candidates": [{"content": {"parts": [{"text": '{"action_type":"WALK_AWAY"}'}]}}],
            "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 2},
        }).encode())

    monkeypatch.setattr("bargaining_platform.model_adapter.urlopen", fake_urlopen)
    schema = {"type": "object", "required": ["action_type"]}
    result = GeminiAdapter("gemini-test", "private-key", "https://gemini.test/v1beta").generate(
        "prompt", schema
    )

    headers = {key.lower(): value for key, value in calls[0].headers.items()}
    request_payload = json.loads(calls[0].data)
    assert calls[0].full_url == "https://gemini.test/v1beta/models/gemini-test:generateContent"
    assert headers["x-goog-api-key"] == "private-key"
    assert "private-key" not in calls[0].full_url
    assert request_payload["generationConfig"]["responseJsonSchema"] == schema
    assert result.text == '{"action_type":"WALK_AWAY"}'
    assert result.metadata["prompt_tokens"] == 8
    assert result.metadata["completion_tokens"] == 2


def test_gemini_adapter_maps_role_history_to_system_instruction_and_contents(monkeypatch):
    from io import BytesIO

    calls = []

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return Response(json.dumps({
            "candidates": [{"content": {"parts": [{"text": '{"action_type":"ACCEPT"}'}]}}],
        }).encode())

    monkeypatch.setattr("bargaining_platform.model_adapter.urlopen", fake_urlopen)
    GeminiAdapter("gemini-test", "private-key", "https://gemini.test/v1beta").generate(
        [
            {"role": "system", "content": "stable role instructions"},
            {"role": "user", "content": "first public offer"},
            {"role": "assistant", "content": "my prior action"},
            {"role": "user", "content": "new public offer"},
        ],
        {"type": "object"},
    )

    assert calls[0]["systemInstruction"]["parts"][0]["text"] == "stable role instructions"
    assert calls[0]["contents"] == [
        {"role": "user", "parts": [{"text": "first public offer"}]},
        {"role": "model", "parts": [{"text": "my prior action"}]},
        {"role": "user", "parts": [{"text": "new public offer"}]},
    ]


class _RetryingAdapter(OpenAIAdapter):
    def __init__(self):
        super().__init__(api_key="test-key", max_api_retries=2, backoff_seconds=0)
        self.calls = 0

    def _request_json(self, payload):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("simulated timeout")
        return {
            "model": "provider-v1",
            "choices": [{"message": {"content": '{"action_type":"ACCEPT"}'}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }


def test_api_retry_recovers_from_timeout_and_records_metadata():
    adapter = _RetryingAdapter()

    result = adapter.generate("accept?", {"type": "object"})

    assert adapter.calls == 2
    assert result.text == '{"action_type":"ACCEPT"}'
    assert result.metadata["model_version"] == "provider-v1"
    assert result.metadata["prompt_tokens"] == 7
    assert result.metadata["completion_tokens"] == 3
    assert result.metadata["api_attempts"] == 2
    assert result.metadata["latency_ms"] >= 0
