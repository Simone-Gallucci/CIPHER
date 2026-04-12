"""
modules/humanizer.py — Post-processing testo Cipher

Passa ogni risposta LLM destinata all'utente attraverso un secondo LLM
che rimuove i pattern tipici dell'AI (elenchi non richiesti, transizioni
meccaniche, formulazioni rigide) e la rende indistinguibile da testo umano.

Fallback silenzioso: se la chiamata LLM fallisce, restituisce il testo originale
invariato — non blocca mai il flusso principale.
"""

import logging
from openai import OpenAI
from config import Config

log = logging.getLogger("cipher.humanizer")

_SYSTEM_PROMPT = (
    "Riscrivi il testo che segue mantenendo esattamente significato, tono e lunghezza. "
    "Rimuovi elenchi puntati non esplicitamente richiesti, strutture rigide, "
    "transizioni meccaniche (es. 'inoltre', 'in conclusione', 'certamente') "
    "e formulazioni tipiche dell'AI. "
    "Il risultato deve sembrare scritto da una persona reale in una chat. "
    "Restituisci solo il testo riscritto, senza spiegazioni né commenti."
)


class Humanizer:
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
        )

    def process(self, text: str) -> str:
        """Post-processa il testo per renderlo più naturale.

        Fallback silenzioso: in caso di errore restituisce il testo originale.
        Non applicare su stringhe vuote, SKIP o payload JSON.
        """
        if not text or not text.strip():
            return text
        stripped = text.strip()
        if stripped.upper() == "SKIP" or stripped.startswith("{"):
            return text
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENROUTER_MODEL,
                # Headroom: il testo riscritto può essere leggermente più lungo
                max_tokens=min(len(text) * 2 + 200, 2048),
                temperature=0.4,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            result = response.choices[0].message.content
            return result.strip() if result else text
        except Exception as e:
            log.debug("Humanizer fallback (errore LLM): %s", e)
            return text
