"""
modules/episodic_memory.py – Memoria episodica strutturata di Cipher

Registra eventi significativi con timestamp e tag, permettendo a Cipher
di ricordare episodi specifici ("quel giorno Simone era nervoso...") invece
di solo lo stato emotivo corrente.

SECURITY-STEP4: accetta mem_dir in __init__ per path per-utente.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import Config
from modules.auth import get_user_memory_dir, get_system_owner_id
from modules.utils import write_json_atomic


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
    def __init__(self, mem_dir: "Path | None" = None):
        _dir = mem_dir or get_user_memory_dir(get_system_owner_id())
        self._file = _dir / "episodes.json"
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
        write_json_atomic(self._file, self._episodes, permissions=0o600)

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

    def recall_relevant(self, query: str, n: int = 3) -> list[dict]:
        """Cerca episodi rilevanti per la query, ordinati per rilevanza.

        Usa ricerca keyword su content e tags. Score = numero di keyword matchate
        + bonus per match esatto nei tag.
        """
        if not query or not self._episodes:
            return []

        keywords = [w.lower() for w in query.split() if len(w) > 2]
        if not keywords:
            return []

        scored: list[tuple[float, dict]] = []
        for ep in self._episodes:
            content_lower = ep.get("content", "").lower()
            tags_lower = [t.lower() for t in ep.get("tags", [])]
            score = 0.0
            for kw in keywords:
                if kw in content_lower:
                    score += 1.0
                if any(kw in tag for tag in tags_lower):
                    score += 1.5  # tag match vale di più
            if score > 0:
                scored.append((score, ep))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:n]]

    def build_context(self, n: int = 6, query: str = "") -> str:
        """Costruisce un blocco di testo per il system prompt.

        Se query è fornita, include episodi rilevanti oltre ai più recenti.
        """
        recent = self.get_recent(n)
        episodes = list(recent)

        if query:
            relevant = self.recall_relevant(query, n=3)
            seen_ids = {e.get("id") for e in episodes}
            for ep in relevant:
                if ep.get("id") not in seen_ids:
                    episodes.append(ep)
                    seen_ids.add(ep.get("id"))

        if not episodes:
            return ""
        lines = ["## Episodi recenti ricordati da Cipher:"]
        for ep in episodes:
            dt = ep["timestamp"][:16].replace("T", " ")
            lines.append(f"- [{dt}] ({ep['type']}) {ep['content']}")
        return "\n".join(lines)

    def get_all(self) -> list:
        return list(self._episodes)
