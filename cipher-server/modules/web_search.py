"""
modules/web_search.py – Ricerca web centralizzata via DuckDuckGo (DDGS)

Singola istanza DDGS condivisa. Fornisce ricerca testo e news con
gestione errori uniforme.

text_search ritorna list[dict] con keys "title", "snippet", "url".
format_search_results converte in stringa con wrapping per-snippet.
"""

import logging
import re

from ddgs import DDGS
from rich.console import Console

console = Console()
log = logging.getLogger("cipher.web_search")

_ddgs = DDGS()

# Neutralizzazione tag gemelli nei risultati di ricerca
_WS_TAG_RE = re.compile(r'</?web_search_result[^>]*>', re.IGNORECASE)


def text_search(query: str, max_results: int = 4) -> list[dict]:
    """Ricerca testo via DDGS. Ritorna lista di dict con title/snippet/url."""
    console.print(f"[cyan]🔍 Cerco:[/cyan] {query}")
    try:
        results = list(_ddgs.text(query, max_results=max_results))
        if not results:
            return []
        return [
            {
                "title":   r.get("title", ""),
                "snippet": r.get("body", ""),
                "url":     r.get("href", ""),
            }
            for r in results
        ]
    except Exception as e:
        log.warning("text_search error: %s", e)
        return []


def format_search_results(results: list[dict]) -> str:
    """Formatta risultati di ricerca con wrapping per-snippet e sanitizzazione.

    Ogni snippet viene sanitizzato individualmente e wrappato in un tag
    <web_search_result source="URL"> separato. Tag gemelli nel body/titolo
    vengono neutralizzati per prevenire chiusure premature.
    """
    if not results:
        return ""
    from modules.prompt_sanitizer import sanitize_memory_field
    parts = []
    for r in results:
        sanitized, _ = sanitize_memory_field(r["snippet"], source="web_search")
        safe_title = _WS_TAG_RE.sub(lambda m: m.group().replace("<", "<\\"), r.get("title", ""))
        safe_body  = _WS_TAG_RE.sub(lambda m: m.group().replace("<", "<\\"), sanitized)
        url = r.get("url", "")
        parts.append(
            f'<web_search_result source="{url}">\n'
            f"{safe_title}\n{safe_body}\n"
            f"</web_search_result>"
        )
    return "\n\n".join(parts)


def news_search(query: str, max_results: int = 2) -> list[dict]:
    """Ricerca news via DDGS. Ritorna lista di risultati raw (dict con title, body, url, ecc.)."""
    try:
        return list(_ddgs.news(query, max_results=max_results))
    except Exception as e:
        log.warning("news_search error for '%s': %s", query, e)
        return []
