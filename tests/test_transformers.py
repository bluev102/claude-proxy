import pytest
from src.transformers import (
    build_anthropic_messages_request,
    build_openai_chat_request,
    build_openai_responses_request,
    anthropic_tools_to_openai_tools,
    anthropic_messages_to_openai_chat_messages,
    anthropic_messages_to_responses_input,
    extract_tool_result_text,
)
from src.utils import make_object_schema


class TestBuildAnthropicMessagesRequest:
    def test_basic(self):
        normalized = {
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }
        result = build_anthropic_messages_request(normalized, {})
        assert result["model"] == "test"
        assert result["stream"] is False

    def test_passes_through_extras(self):
        normalized = {"model": "test", "messages": [], "stream": False}
        raw = {"metadata": {"user_id": "123"}}
        result = build_anthropic_messages_request(normalized, raw)
        assert result["metadata"]["user_id"] == "123"


class TestAnthropicToolsToOpenaiTools:
    def test_converts_to_openai_format(self):
        tools = [{"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}]
        result = anthropic_tools_to_openai_tools(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "get_weather"


class TestAnthropicMessagesToOpenaiChatMessages:
    def test_system_message(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = anthropic_messages_to_openai_chat_messages(msgs, "You are helpful")
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"

    def test_user_message(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        result = anthropic_messages_to_openai_chat_messages(msgs, None)
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"

    def test_assistant_with_tool_calls(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "get_weather", "id": "call_123", "input": {"city": "NYC"}},
            ]}
        ]
        result = anthropic_messages_to_openai_chat_messages(msgs, None)
        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_tool_result_becomes_tool_role(self):
        msgs = [
            {"role": "assistant", "content": [{"type": "text", "text": "Let me check"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "sunny"}]},
        ]
        result = anthropic_messages_to_openai_chat_messages(msgs, None)
        tool_msg = [m for m in result if m["role"] == "tool"][0]
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "sunny"


class TestExtractToolResultText:
    def test_str(self):
        assert extract_tool_result_text("hello") == "hello"

    def test_list_of_text(self):
        assert extract_tool_result_text([{"type": "text", "text": "hello"}]) == "hello"

    def test_mixed_list(self):
        assert extract_tool_result_text(["hi", {"type": "text", "text": " there"}]) == "hi there"


class TestBuildOpenaiChatRequest:
    def test_basic_request(self):
        normalized = {
            "model": "test-model",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "stream": False,
        }
        result = build_openai_chat_request(normalized)
        assert result["model"] == "test-model"
        assert result["messages"][0]["role"] == "user"

    def test_stream_options(self):
        normalized = {
            "model": "test",
            "messages": [],
            "stream": True,
        }
        result = build_openai_chat_request(normalized)
        assert result["stream_options"]["include_usage"] is True


class TestAnthropicMessagesToResponsesInput:
    def test_system_message(self):
        msgs = []
        result = anthropic_messages_to_responses_input(msgs, "You are helpful")
        assert result[0]["role"] == "system"
        assert result[0]["content"][0]["type"] == "input_text"
        assert result[0]["content"][0]["text"] == "You are helpful"

    def test_user_message(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        result = anthropic_messages_to_responses_input(msgs, None)
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["text"] == "hello"


class TestBuildOpenaiResponsesRequest:
    def test_basic(self):
        normalized = {
            "model": "test",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "stream": False,
        }
        result = build_openai_responses_request(normalized)
        assert result["model"] == "test"
        assert result["stream"] is False
        assert "input" in result

    def test_max_tokens_maps_to_output_tokens(self):
        normalized = {
            "model": "test",
            "messages": [],
            "stream": False,
            "max_tokens": 100,
        }
        result = build_openai_responses_request(normalized)
        assert result["max_output_tokens"] == 100
