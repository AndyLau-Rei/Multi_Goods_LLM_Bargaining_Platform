from bargaining_platform.model_adapter import (
    BaseLLMAdapter,
    GenerationResult,
    OpenAIAdapter,
    MockLLMAdapter,
)


def test_mock_adapter_returns_queued_raw_output_and_metadata():
    adapter = MockLLMAdapter(['{"action_type":"WALK_AWAY"}'], model_version="mock-v1")

    result = adapter.generate("offer", {"type": "object"})

    assert isinstance(adapter, BaseLLMAdapter)
    assert isinstance(result, GenerationResult)
    assert result.text == '{"action_type":"WALK_AWAY"}'
    assert result.metadata["model_version"] == "mock-v1"
    assert result.metadata["prompt_tokens"] == 1
    assert result.metadata["completion_tokens"] == 1
    assert result.metadata["latency_ms"] >= 0


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
