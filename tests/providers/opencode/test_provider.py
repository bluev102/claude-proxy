import pytest

from providers import load_provider


class TestOpenCodeProvider:
    def test_provider_name(self):
        p = load_provider("opencode")
        assert p.provider_name == "opencode"

    def test_normalize_model_id_strips_prefix(self):
        p = load_provider("opencode")
        assert p.normalize_model_id("opencode/claude-sonnet") == "claude-sonnet"

    def test_normalize_model_id_no_prefix(self):
        p = load_provider("opencode")
        assert p.normalize_model_id("claude-sonnet") == "claude-sonnet"

    def test_normalize_model_id_empty(self):
        p = load_provider("opencode")
        assert p.normalize_model_id("") == ""

    def test_normalize_model_id_whitespace(self):
        p = load_provider("opencode")
        assert p.normalize_model_id("  opencode/minimax-2.5  ") == "minimax-2.5"

    def test_owned_by(self):
        p = load_provider("opencode")
        assert p.owned_by() == "opencode-proxy"

    def test_base_url(self):
        p = load_provider("opencode")
        assert "opencode.ai" in p.base_url

    def test_default_model_not_empty(self):
        p = load_provider("opencode")
        assert p.default_model != ""
