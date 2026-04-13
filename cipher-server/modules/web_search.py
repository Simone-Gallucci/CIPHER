"""
modules/web_search.py – Ricerca web centralizzata via DuckDuckGo (DDGS)

Singola istanza DDGS condivisa. Fornisce ricerca testo e news con
gestione errori uniforme.
"""

import logging
from typing import Optional

from ddgs import DDGS
from rich.console import Console

console = Console()
log = logging.getLogger("cipher.web_search")

_ddgs = DDGS()


def text_search(query: str, max_results: int = 4) -> str:
    """Ricerca testo via DDGS. Ritorna risultati formattati o messaggio di errore."""
    console.print(f"[cyan]🔍 Cerco:[/cyan] {query}")
    try:
        results = list(_ddgs.text(query, max_results=max_results))
        if not results:
            return "Nessun risultato trovato."
        parts = [
            f"• {r.get('title', '')}\n  {r.get('body', '')}\n  ({r.get('href', '')})"
            for r in results
        ]
        return "Risultati ricerca:\n" + "\n\n".join(parts)
    except Exception as e:
        log.warning("text_search error: %s", e)
        return f"Errore ricerca: {e}"


def news_search(query: str, max_results: int = 2) -> list[dict]:
    """Ricerca news via DDGS. Ritorna lista di risultati raw (dict con title, body, url, ecc.)."""
    try:
        return list(_ddgs.news(query, max_results=max_results))
    except Exception as e:
        log.warning("news_search error for '%s': %s", query, e)
        return []
