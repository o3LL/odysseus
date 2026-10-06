"""Which wording opts a turn out of the web.

Both the chat route and the agent loop use ``message_avoids_web_lookup``. A
false positive strips web_search from a request that asked for the web; a false
negative offers it to a request that ruled it out.
"""
import pytest

from routes.chat_routes import _explicitly_denies_web_lookup
from src.agent_loop import _explicitly_avoids_web_lookup
from src.tool_policy import message_avoids_web_lookup

AVOIDS_WEB = [
    "no web search please, just tell me what you know about Rust",
    "No web!",
    "answer from memory only: capital of Peru",
    "Answer from memory only, don't search online.",
    "answer from memory, no web please",
    "just from memory: who wrote Dune?",
    "From memory, what's the capital of Peru?",
    "don't search, just guess",
    "don’t search, just tell me",
    "Summarise what you already know. Do not search the web.",
    "Do not search or fetch https://example.com/private",
    "don't use the web for this",
    "don't go online for this, I just want your take",
    "without searching, what's 2+2",
    "tell me about Rome without looking it up",
]

WANTS_WEB = [
    "search the web for the latest Python release",
    "don't search my notes, search the web for the 2026 F1 calendar",
    "do not search reddit, search official docs for FastAPI lifespan",
    "Find recent news about the Gemini launch, don't search twitter",
    "look it up online: tickets for the Louvre, not from memory",
    "Do not answer from memory. After each search, check the snippets.",
    "I can't recall the lyrics from memory, can you search for them?",
    "search for articles about learning piano from memory",
    "from memory of our last chat, search the web for that restaurant",
    "Search the web for flash memory prices from memory makers like Micron",
    "don't use the web version of Spotify, find me the desktop download link",
    "no website yet, can you search for domain registrars?",
    "I have no web presence yet, can you look up how to build a site?",
    "Can you find what happened to the no web app policy at Apple?",
    "Search online for the best laptops, no web3 stuff",
]


@pytest.mark.parametrize("text", AVOIDS_WEB)
def test_wording_that_rules_out_the_web(text):
    assert message_avoids_web_lookup(text)
    assert _explicitly_avoids_web_lookup(text)
    assert _explicitly_denies_web_lookup(text)


@pytest.mark.parametrize("text", WANTS_WEB)
def test_negating_something_else_keeps_the_web(text):
    assert not message_avoids_web_lookup(text)
    assert not _explicitly_avoids_web_lookup(text)
    assert not _explicitly_denies_web_lookup(text)


def test_route_still_treats_a_tool_ban_as_a_web_ban():
    assert _explicitly_denies_web_lookup("no tools, just answer")
    assert not _explicitly_avoids_web_lookup("no tools, just answer")
