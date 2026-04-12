"""
modules/pattern_learner.py – Disabilitato (stub no-op)

Il tracciamento dei pattern comportamentali è stato rimosso perché
Cipher deve imparare a conoscere Simone attraverso la conversazione,
non attraverso statistiche di utilizzo.
"""

from typing import Optional


DAYS_IT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


class PatternLearner:
    """Stub no-op: tutti i metodi sono disabilitati."""

    def __init__(self, brain=None):
        self._brain = brain
        self._data: dict = {}

    def record_interaction(self, hour: int, weekday: int, topic: str) -> None:
        pass

    def analyze_today(self, conversations_text: str) -> None:
        pass

    def get_predictions(self, lookahead_hours: int = 3) -> list:
        return []

    def get_summary(self) -> str:
        return ""

    def get_engagement_signal(self) -> str:
        return ""
