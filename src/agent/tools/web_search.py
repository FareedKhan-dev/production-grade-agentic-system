"""Web search tool for LangGraph, backed by DuckDuckGo (no API key needed).

It calls the ddgs package directly. The tool used to come from langchain-community, which
the LangChain team is sunsetting.
"""

import json

from ddgs import DDGS
from langchain_core.tools import tool

from src.system.logs import logger

MAX_RESULTS = 10


@tool("web_search")
def duckduckgo_search_tool(query: str) -> str:
    """Search the web with DuckDuckGo. Use it for recent events and for facts you are not sure about.

    Returns a JSON list of results, each with a title, a link and a snippet.
    """
    try:
        results = DDGS().text(query, max_results=MAX_RESULTS)
    except Exception as e:
        # Network errors and rate limits: tell the model, which can answer without the search
        logger.warning("web_search_failed", error=str(e))
        return f"The search failed: {e}"
    return json.dumps(
        [{"title": r.get("title"), "link": r.get("href"), "snippet": r.get("body")} for r in results],
        ensure_ascii=False,
    )
