"""The web search tool, with DuckDuckGo replaced so the tests need no network."""

import json

import src.agent.tools.web_search as web_search_module
from src.agent.tools import tools


class FakeDDGS:
    def text(self, query, max_results):
        return [{"title": "Lisbon", "href": "https://example.org/lisbon", "body": f"About {query}"}][:max_results]


class BrokenDDGS:
    def text(self, query, max_results):
        raise RuntimeError("202 Ratelimit")


def test_the_agent_has_the_web_search_tool():
    assert [tool.name for tool in tools] == ["web_search"]


def test_results_have_a_title_a_link_and_a_snippet(monkeypatch):
    monkeypatch.setattr(web_search_module, "DDGS", FakeDDGS)
    results = json.loads(web_search_module.duckduckgo_search_tool.invoke({"query": "Lisbon population"}))
    assert results == [{"title": "Lisbon", "link": "https://example.org/lisbon", "snippet": "About Lisbon population"}]


def test_a_failed_search_is_reported_to_the_model(monkeypatch):
    monkeypatch.setattr(web_search_module, "DDGS", BrokenDDGS)
    answer = web_search_module.duckduckgo_search_tool.invoke({"query": "Lisbon population"})
    assert answer == "The search failed: 202 Ratelimit"
