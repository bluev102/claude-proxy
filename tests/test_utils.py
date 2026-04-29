import pytest
from src.utils import (
    clamp_number,
    ensure_dict,
    make_object_schema,
    normalize_label,
    normalize_model_id,
    normalize_name_key,
    normalize_system_to_text,
    safe_json_loads,
    text_from_block_like,
)
from src.errors import ProxyValidationError


class TestNormalizeModelId:
    def test_strips_opencode_prefix(self):
        assert normalize_model_id("opencode/claude-sonnet") == "claude-sonnet"

    def test_no_prefix(self):
        assert normalize_model_id("claude-sonnet") == "claude-sonnet"

    def test_strips_whitespace(self):
        assert normalize_model_id("  claude-sonnet  ") == "claude-sonnet"

    def test_empty(self):
        assert normalize_model_id("") == ""


class TestNormalizeLabel:
    def test_lowercase(self):
        assert normalize_label("Hello World") == "hello world"

    def test_strips_whitespace(self):
        assert normalize_label("  hello  ") == "hello"

    def test_collapse_spaces(self):
        assert normalize_label("hello   world") == "hello world"


class TestNormalizeNameKey:
    def test_removes_parentheses(self):
        assert normalize_name_key("claude (sonnet) 4") == "claude 4"

    def test_removes_free(self):
        assert normalize_name_key("claude free model") == "claude model"

    def test_removes_flash(self):
        assert normalize_name_key("claude flash model") == "claude model"

    def test_removes_special_chars(self):
        assert normalize_name_key("claude-sonnet-4.0") == "claude sonnet 4 0"


class TestEnsureDict:
    def test_valid_dict(self):
        assert ensure_dict({"a": 1}, "body") == {"a": 1}

    def test_invalid_type(self):
        with pytest.raises(ProxyValidationError):
            ensure_dict("not a dict", "body")

    def test_none(self):
        with pytest.raises(ProxyValidationError):
            ensure_dict(None, "body")


class TestClampNumber:
    def test_int(self):
        assert clamp_number(5, "x") == 5.0

    def test_float(self):
        assert clamp_number(3.14, "x") == 3.14

    def test_none(self):
        assert clamp_number(None, "x") is None

    def test_bool_raises(self):
        with pytest.raises(ProxyValidationError):
            clamp_number(True, "x")


class TestSafeJsonLoads:
    def test_valid_json(self):
        assert safe_json_loads('{"a": 1}') == {"a": 1}

    def test_invalid_json(self):
        assert safe_json_loads("not json") is None


class TestTextFromBlockLike:
    def test_str(self):
        assert text_from_block_like("hello") == "hello"

    def test_none(self):
        assert text_from_block_like(None) == ""

    def test_list_of_text_blocks(self):
        assert text_from_block_like([{"type": "text", "text": "hi"}]) == "hi"

    def test_list_mixed(self):
        assert text_from_block_like([{"type": "input_text", "text": "hi"}]) == "hi"

    def test_dict(self):
        assert text_from_block_like({"type": "text", "text": "hi"}) == "hi"


class TestNormalizeSystemToText:
    def test_none(self):
        assert normalize_system_to_text(None) is None

    def test_str(self):
        assert normalize_system_to_text("  hello  ") == "hello"

    def test_empty_str(self):
        assert normalize_system_to_text("   ") is None

    def test_list(self):
        assert normalize_system_to_text(["hello", {"type": "text", "text": " world"}]) == "hello\nworld"


class TestMakeObjectSchema:
    def test_dict_with_type(self):
        result = make_object_schema({"type": "object", "properties": {"a": {}}})
        assert result["type"] == "object"
        assert isinstance(result["properties"], dict)

    def test_non_dict(self):
        result = make_object_schema("not a dict")
        assert result == {"type": "object", "properties": {}}

    def test_invalid_required(self):
        result = make_object_schema({"type": "object", "required": "not a list"})
        assert "required" not in result
