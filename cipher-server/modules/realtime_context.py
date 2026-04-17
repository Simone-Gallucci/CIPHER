"""
modules/realtime_context.py – Contesto in tempo reale per Cipher

Recupera ogni ora:
  - Meteo attuale (wttr.in, nessuna API key necessaria)
  - Notizie rilevanti per Simone (DDGS news: cybersecurity + tech italia)

Salva uno snapshot in memory/realtime_context.json.
Brain lo include nel system prompt per rendere Cipher più contestuale.
"""

import json
from datetime import datetime
from typing import Optional

import requests
from rich.console import Console

from config import Config
from modules.auth import get_user_memory_dir, get_system_owner_id
from modules.utils import write_json_atomic

console = Console()

REALTIME_FILE = get_user_memory_dir(get_system_owner_id()) / "realtime_context.json"

# Topic notizie: abbina interessi fissi + campo di Simone
NEWS_QUERIES = [
    "cybersecurity news oggi",
    "tech italia oggi",
]


class RealtimeContext:
    def __init__(self, cipher_interests=None):
        self._interests = cipher_interests

    # ── API pubblica ──────────────────────────────────────────────────

    def refresh(self) -> None:
        """Recupera meteo e notizie e salva lo snapshot."""
        weather = self._fetch_weather()
        news    = self._fetch_news()
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "weather":   weather,
            "news":      news,
        }
        write_json_atomic(REALTIME_FILE, snapshot, permissions=0o600)
        console.print("[dim]🌐 Contesto real-time aggiornato[/dim]")

    def build_context(self) -> str:
        """Restituisce un blocco testo da includere nel system prompt."""
        if not REALTIME_FILE.exists():
            return ""
        try:
            data = json.loads(REALTIME_FILE.read_text(encoding="utf-8"))
        except Exception:
            return ""

        # Non usare dati più vecchi di 2 ore
        try:
            ts = datetime.fromisoformat(data.get("timestamp", ""))
            age_hours = (datetime.now() - ts).total_seconds() / 3600
            if age_hours > 2:
                return ""
        except Exception:
            return ""

        lines = ["## Contesto in tempo reale:"]

        weather = data.get("weather", "")
        if weather:
            lines.append(f"- Meteo: {weather}")

        news = data.get("news", [])
        if news:
            lines.append("- Notizie rilevanti:")
            for item in news[:4]:
                lines.append(f"  • {item}")

        return "\n".join(lines) if len(lines) > 1 else ""

    # ── Fetch meteo ───────────────────────────────────────────────────

    def _fetch_weather(self) -> str:
        """
        Usa wttr.in con format=3 → risposta tipo "Rome: ⛅️ +18°C"
        Nessuna API key richiesta.
        """
        city = "Ancona"
        try:
            r = requests.get(
                f"https://wttr.in/{city}?format=3&lang=it",
                timeout=8,
                headers={"User-Agent": "Cipher-AI/1.0"},
            )
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text.strip()
        except Exception as e:
            console.print(f"[dim]⚠️  Meteo non disponibile: {e}[/dim]")
        return ""

    # ── Fetch notizie ─────────────────────────────────────────────────

    def _fetch_news(self) -> list[str]:
        """
        Cerca notizie recenti su DDGS per i topic rilevanti.
        Restituisce titoli brevi.
        """
        queries = list(NEWS_QUERIES)

        # Aggiunge un topic dagli interessi di Cipher se disponibile
        if self._interests:
            try:
                active = self._interests.get_active_interests(min_intensity=0.6)
                if active:
                    top = sorted(active, key=lambda x: x.get("intensity", 0), reverse=True)
                    queries.append(f"{top[0]['topic']} news")
            except Exception:
                pass

        headlines: list[str] = []
        seen: set[str] = set()

        for query in queries[:3]:
            try:
                from modules.web_search import news_search
                results = news_search(query, max_results=2)
                for r in results:
                    title = r.get("title", "").strip()
                    _skip = (
                        not title
                        or title in seen
                        or title.startswith("BUG:")
                        or any(c in title for c in ("â", "Â", "Ã", "\x00"))
                    )
                    if not _skip:
                        seen.add(title)
                        headlines.append(title)
            except Exception:
                continue

        return headlines[:5]
