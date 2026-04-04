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
from ddgs import DDGS
from rich.console import Console

from config import Config

console = Console()

REALTIME_FILE = Config.MEMORY_DIR / "realtime_context.json"

# Topic notizie: abbina interessi fissi + campo di Simone
NEWS_QUERIES = [
    "cybersecurity news oggi",
    "tech italia oggi",
]


class RealtimeContext:
    def __init__(self, cipher_interests=None):
        self._interests = cipher_interests
        self._ddgs      = DDGS()

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
        REALTIME_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"[dim]🌐 Contesto real-time aggiornato ({Config.WEATHER_CITY})[/dim]")

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
            lines.append(f"- Meteo ({Config.WEATHER_CITY}): {weather}")

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
        city = Config.WEATHER_CITY.replace(" ", "+")
        try:
            r = requests.get(
                f"https://wttr.in/{city}?format=3&lang=it",
                timeout=8,
                headers={"User-Agent": "Cipher-AI/1.0"},
            )
            if r.status_code == 200:
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
                results = list(self._ddgs.news(query, max_results=2))
                for r in results:
                    title = r.get("title", "").strip()
                    if title and title not in seen:
                        seen.add(title)
                        headlines.append(title)
            except Exception:
                continue

        return headlines[:5]
