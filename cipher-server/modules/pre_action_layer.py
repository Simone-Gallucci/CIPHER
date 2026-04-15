"""
modules/pre_action_layer.py – Raccolta dati verificati prima di ogni LLM call

Inietta dati in tempo reale nel system prompt DOPO il TTL cache (300s),
garantendo che Cipher non risponda mai con dati stantii di memoria.

Livello 1 (sempre): datetime + calendar_today
Livello 2 (rule-based): email_unread quando keywords presenti
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("cipher.pre_action")

# ── Livello 2: rule-based expansion ──────────────────────────────────────────
_EMAIL_KEYWORDS: frozenset[str] = frozenset({
    "email", "mail", "gmail", "messaggio", "messaggi",
    "ha scritto", "risposta", "risposto", "scritto",
    "mandato", "inviato", "ricevuto", "risponde",
})

# Cache TTL in secondi
_TTL: dict[str, int] = {
    "calendar_today": 300,   # 5 minuti
    "email_unread":   60,    # 1 minuto
}

# Timeout globale gather() in secondi
_GATHER_TIMEOUT: float = 2.5


class PreActionLayer:
    """
    Raccoglie dati verificati in tempo reale prima di ogni chiamata LLM.
    Output: blocco testo da iniettare nel system prompt dopo _get_system_prompt().

    Livello 1 — sempre eseguito:
        • datetime corrente (gratuito, nessuna rete)
        • calendar_today — eventi di oggi/domani (cache 300s)

    Livello 2 — rule-based, nessuna LLM call:
        • email_unread — se message contiene keyword email (cache 60s)
    """

    def __init__(self) -> None:
        # Cache: key → (data_str, fetched_at timestamp)
        self._cache: dict[str, tuple[str, float]] = {}

    # ── API pubblica ─────────────────────────────────────────────────────────

    def gather(self, user_input: str, history: list[dict]) -> str:
        """
        Raccoglie e restituisce il blocco [DATI VERIFICATI].
        Sempre sync — compatibile con think() sync.
        Ritorna "" se nessun dato disponibile (es. credenziali Google assenti).
        """
        import signal as _signal

        start = time.monotonic()
        parts: list[str] = []

        # ── Livello 1: datetime (gratuito) ────────────────────────────────
        now = datetime.now()
        weekdays_it = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
        parts.append(
            f"Data e ora attuali: {weekdays_it[now.weekday()]} "
            f"{now.strftime('%d/%m/%Y %H:%M')}"
        )

        # ── Livello 1: calendar_today (cache 300s) ────────────────────────
        elapsed = time.monotonic() - start
        if elapsed < _GATHER_TIMEOUT:
            cal_data = self._get_cached("calendar_today", self._fetch_calendar_today)
            if cal_data:
                parts.append(cal_data)

        # ── Livello 2: email_unread (rule-based, cache 60s) ───────────────
        elapsed = time.monotonic() - start
        if elapsed < _GATHER_TIMEOUT:
            msg_lower = user_input.lower()
            if any(kw in msg_lower for kw in _EMAIL_KEYWORDS):
                email_data = self._get_cached("email_unread", self._fetch_email_unread)
                if email_data:
                    parts.append(email_data)

        if not parts:
            return ""

        body = "\n".join(parts)
        return (
            f"[DATI VERIFICATI — PRIORITÀ MASSIMA]\n"
            f"{body}\n"
            f"[/DATI VERIFICATI]\n"
            f"Usa questi dati come fonte primaria. Se contraddicono la memoria, "
            f"i dati verificati hanno la precedenza."
        )

    def invalidate(self, key: str) -> None:
        """Invalida una chiave cache specifica (es. dopo calendar_create)."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Svuota l'intera cache."""
        self._cache.clear()

    # ── Cache helper ─────────────────────────────────────────────────────────

    def _get_cached(self, key: str, fetcher) -> str:
        """Ritorna valore dalla cache se valido, altrimenti chiama fetcher."""
        cached = self._cache.get(key)
        ttl = _TTL.get(key, 300)
        if cached:
            data, ts = cached
            if time.time() - ts < ttl:
                return data
        try:
            data = fetcher()
            if data:
                self._cache[key] = (data, time.time())
            return data or ""
        except Exception as e:
            log.debug("PreAction fetch '%s' fallito: %s", key, e)
            return ""

    # ── Fetcher: calendario ───────────────────────────────────────────────────

    def _fetch_calendar_today(self) -> str:
        """Legge gli eventi di oggi e domani dal calendario Google."""
        try:
            from modules.google_cal import GoogleCalendar
            cal = GoogleCalendar()
            events_text = cal.list_events(days=2, max_results=10)
            if not events_text or "Nessun evento" in events_text:
                return "Calendario: nessun evento nelle prossime 48 ore."
            return f"Calendario (prossime 48 ore):\n{events_text}"
        except Exception as e:
            log.debug("PreAction calendar fetch fallito: %s", e)
            return ""

    # ── Fetcher: email non lette ──────────────────────────────────────────────

    def _fetch_email_unread(self) -> str:
        """Legge gli ultimi 3 messaggi non letti da Gmail (solo mittente + oggetto)."""
        try:
            from modules.google_mail import GoogleMail
            mail = GoogleMail()
            result = mail.list_emails(max_results=3, query="is:unread")
            if not result or "Nessuna email" in result or "Errore" in result:
                return "Gmail: nessuna email non letta."
            return f"Email non lette:\n{result}"
        except Exception as e:
            log.debug("PreAction email fetch fallito: %s", e)
            return ""
