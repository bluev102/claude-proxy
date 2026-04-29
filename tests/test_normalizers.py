import pytest
from core.normalizers import normalize_anthropic_messages, normalize_anthropic_tools, normalize_anthropic_request
from core.errors import ProxyValidationError


class TestNormalizeAnthropicMessages:
    def test_valid_messages(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = normalize_anthropic_messages(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][0]["text"] == "hello"

    def test_string_content(self):
        msgs = [{"role": "user", "content": "hello"}]
        result = normalize_anthropic_messages(msgs)
        assert result[0]["content"] == [{"type": "text", "text": "hello"}]

    def test_blocks_content(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = normalize_anthropic_messages(msgs)
        assert result[0]["content"][0]["text"] == "hi"

    def test_tool_use_block(self):
        msgs = [{"role": "user", "content": [{"type": "tool_use", "name": "get_weather", "input": {"city": "NYC"}}]}]
        result = normalize_anthropic_messages(msgs)
        block = result[0]["content"][0]
        assert block["type"] == "tool_use"
        assert block["name"] == "get_weather"
        assert block["input"] == {"city": "NYC"}

    def test_tool_result_block(self):
        msgs = [
            {"role": "user", "content": [{"type": "tool_use", "name": "get_weather", "id": "toolu_123", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_123", "content": "sunny"}]},
        ]
        result = normalize_anthropic_messages(msgs)
        assert result[1]["content"][0]["type"] == "tool_result"
        assert result[1]["content"][0]["content"] == "sunny"

    def test_not_list_raises(self):
        with pytest.raises(ProxyValidationError):
            normalize_anthropic_messages("not a list")

    def test_invalid_role_raises(self):
        with pytest.raises(ProxyValidationError):
            normalize_anthropic_messages([{"role": "system", "content": "hi"}])

    def test_empty_content_raises(self):
        with pytest.raises(ProxyValidationError):
            normalize_anthropic_messages([{"role": "user", "content": ""}])


class TestNormalizeAnthropicTools:
    def test_valid_tool(self):
        tools = [{"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object"}}]
        result = normalize_anthropic_tools(tools)
        assert result[0]["name"] == "get_weather"
        assert result[0]["input_schema"]["type"] == "object"

    def test_none(self):
        assert normalize_anthropic_tools(None) == []

    def test_not_list_raises(self):
        with pytest.raises(ProxyValidationError):
            normalize_anthropic_tools("not a list")

    def test_drops_empty_name(self):
        tools = [{"name": "", "description": "desc"}]
        assert normalize_anthropic_tools(tools) == []

    def test_invalid_schema_becomes_object(self):
        tools = [{"name": "get_weather", "input_schema": "not a dict"}]
        result = normalize_anthropic_tools(tools)
        assert result[0]["input_schema"]["type"] == "object"


class TestNormalizeAnthropicRequest:
    def test_basic_request(self):
        body = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 100,
            "temperature": 0.7,
        }
        result = normalize_anthropic_request(body, "test-model")
        assert result["model"] == "test-model"
        assert result["max_tokens"] == 100
        assert result["temperature"] == 0.7
        assert result["stream"] is False

    def test_stream_flag(self):
        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        result = normalize_anthropic_request(body, "test")
        assert result["stream"] is True

    def test_system_string(self):
        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "system": "You are helpful"}
        result = normalize_anthropic_request(body, "test")
        assert result["system"] == "You are helpful"

    def test_max_tokens_zero_raises(self):
        body = {"model": "test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 0}
        with pytest.raises(ProxyValidationError):
            normalize_anthropic_request(body, "test")
