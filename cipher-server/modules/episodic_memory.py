"""
modules/episodic_memory.py – Memoria episodica strutturata di Cipher

Registra eventi significativi con timestamp e tag, permettendo a Cipher
di ricordare episodi specifici ("quel giorno Simone era nervoso...") invece
di solo lo stato emotivo corrente.
"""

import json
from datetime import datetime
from typing import Optional

from config import Config


EPISODE_TYPES = {
    "conversation":     "Momento saliente di una conversazione",
    "emotion_shift":    "Cambiamento di stato emotivo",
    "action_taken":     "Azione autonoma eseguita",
    "goal_completed":   "Obiettivo completato",
    "pattern_detected": "Pattern comportamentale rilevato",
    "daily_summary":    "Sommario giornaliero",
    "observation":      "Osservazione autonoma su Simone o sul mondo",
}

MAX_EPISODES = 500


class EpisodicMemory:
    def __init__(self):
        self._file = Config.MEMORY_DIR / "episodes.json"
        self._episodes: list[dict] = self._load()

    # ── Persistenza ───────────────────────────────────────────────────

    def _load(self) -> list:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(self):
        self._file.write_text(
            json.dumps(self._episodes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── API pubblica ──────────────────────────────────────────────────

    def add_episode(
        self,
        content: str,
        episode_type: str,
        tags: Optional[list] = None,
        emotional_state: str = "neutral",
    ) -> dict:
        """
        Registra un episodio significativo.

        Args:
            content:        Descrizione testuale dell'episodio.
            episode_type:   Uno dei tipi in EPISODE_TYPES.
            tags:           Lista di tag (argomenti, persone, luoghi).
            emotional_state: Stato emotivo al momento dell'episodio.
        """
        episode = {
            "id": len(self._episodes) + 1,
            "timestamp": datetime.now().isoformat(),
            "type": episode_type,
            "content": content,
            "tags": tags or [],
            "emotional_state": emotional_state,
        }
        self._episodes.append(episode)
        if len(self._episodes) > MAX_EPISODES:
            self._episodes = self._episodes[-MAX_EPISODES:]
        self._save()
        return episode

    def get_recent(self, n: int = 10, episode_type: Optional[str] = None) -> list:
        """Ritorna gli N episodi più recenti, opzionalmente filtrati per tipo."""
        filtered = (
            self._episodes
            if not episode_type
            else [e for e in self._episodes if e["type"] == episode_type]
        )
        return filtered[-n:]

    def search_by_tag(self, tag: str) -> list:
        """Ritorna tutti gli episodi che contengono il tag (case-insensitive)."""
        tag_lower = tag.lower()
        return [
            e for e in self._episodes
            if tag_lower in [t.lower() for t in e.get("tags", [])]
        ]

    def build_context(self, n: int = 6) -> str:
        """Costruisce un blocco di testo con gli N episodi più recenti per il system prompt."""
        recent = self.get_recent(n)
        if not recent:
            return ""
        lines = ["## Episodi recenti ricordati da Cipher:"]
        for ep in recent:
            dt = ep["timestamp"][:16].replace("T", " ")
            lines.append(f"- [{dt}] ({ep['type']}) {ep['content']}")
        return "\n".join(lines)

    def get_all(self) -> list:
        return list(self._episodes)
