import pytest
from src.parsing import parse_docs_catalog_from_html, html_table_to_dicts


class TestHtmlTableToDicts:
    def test_empty(self):
        class MockTable:
            def find_all(self, tag):
                return []
        assert html_table_to_dicts(MockTable()) == []

    def test_basic_table(self):
        class MockCell:
            def __init__(self, text):
                self._text = text
            def get_text(self, *args, **kwargs):
                return self._text
        class MockRow:
            def __init__(self, cells):
                self._cells = cells
            def find_all(self, tag):
                return self._cells
        class MockTable:
            def find_all(self, tag):
                return [
                    MockRow([MockCell("name"), MockCell("age")]),
                    MockRow([MockCell("Alice"), MockCell("30")]),
                ]

        result = html_table_to_dicts(MockTable())
        assert result[0] == {"name": "Alice", "age": "30"}


class TestParseDocsCatalogFromHtml:
    def test_empty_html(self):
        result = parse_docs_catalog_from_html("<html><body></body></html>")
        assert result["routes"] == {}
        assert result["pricing_free_names"] == {}
        assert result["bullet_free_names"] == []
        assert result["free_route_ids"] == set()

    def test_pricing_free_table(self):
        html = """
        <html><body>
        <table>
            <tr><th>model</th><th>input</th><th>output</th><th>cached read</th><th>cached write</th></tr>
            <tr><td>Free Model</td><td>free</td><td>free</td><td>free</td><td>free</td></tr>
        </table>
        </body></html>
        """
        result = parse_docs_catalog_from_html(html)
        assert "Free Model" in result["pricing_free_names"]

    def test_free_models_bullet_list(self):
        html = """
        <html><body>
        <p>The free models:</p>
        <ul><li>FreeBot is a free model</li></ul>
        </body></html>
        """
        result = parse_docs_catalog_from_html(html)
        assert "FreeBot" in result["bullet_free_names"]

    def test_routes_extraction(self):
        html = """
        <html><body>
        <table>
            <tr><th>model</th><th>model id</th><th>endpoint</th><th>ai sdk package</th></tr>
            <tr><td>Test Model</td><td>test-model</td><td>https://api.opencode.ai/zen/v1/messages</td><td>anthropic</td></tr>
        </table>
        </body></html>
        """
        result = parse_docs_catalog_from_html(html)
        assert "test-model" in result["routes"]
        assert result["routes"]["test-model"]["family"] == "anthropic_messages"
