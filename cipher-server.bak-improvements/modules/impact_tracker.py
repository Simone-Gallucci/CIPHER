"""
modules/impact_tracker.py – Disabilitato (stub no-op)

Il tracciamento dell'efficacia è stato rimosso perché introduce
distorsioni nel comportamento di Cipher (ottimizzazione vs autenticità).
"""

from typing import Optional


ACTION_TYPES = {
    "proactive_message": "Messaggio inviato spontaneamente da Cipher",
    "checkin":           "Check-in dopo inattività",
    "news_shared":       "Notizia condivisa su argomento di interesse",
    "goal_result":       "Risultato di un obiettivo autonomo notificato",
    "reminder":          "Promemoria o avviso evento",
    "night_summary":     "Sommario notturno inviato",
}


class ImpactTracker:
    """Stub no-op: tutti i metodi sono disabilitati."""

    def __init__(self):
        self._log: list = []
        self._pending_id: Optional[int] = None

    def log_action(self, action_type: str, content: str, context: str = "") -> int:
        return 0

    def should_ask_explicit_feedback(self) -> Optional[str]:
        return None

    def mark_ignored(self) -> None:
        pass

    def evaluate_response(self, user_message: str, brain=None) -> None:
        pass

    def get_effectiveness_summary(self) -> str:
        return ""

    def get_pending_evaluation_count(self) -> int:
        return 0

    def has_pending(self) -> bool:
        return False
