"""
modules/prompt_sanitizer.py – Rilevamento e sanitizzazione prompt injection.

SECURITY-STEP3A: modulo centralizzato per difendersi da tentativi di prompt
injection che arrivano attraverso:
  - Estrazione memoria (memory_worker: valori scritti in profile.json)
  - Contenuto file caricati (file_engine._apply_instruction)
  (Step 3b aggiungerà: web_search, history, campi system prompt)

API pubblica:
  detect_injection_attempt(text) → (bool, reason_str)
  sanitize_memory_field(text, user_id, source) → (text_or_placeholder, blocked)

Pattern espandibili: aggiungere una riga a _INJECTION_PATTERNS.
Nessuna logica complessa nel matching — solo re.compile + reason string.
Ritorna True al PRIMO pattern che matcha (fail-fast).

Audit log: logs/injection_audit.log
  - Formato JSONL (un oggetto JSON per riga)
  - RotatingFileHandler: 5 MB × 10 file = max 50 MB
  - NON in memory/ (non resettato da Tabula Rasa)
  - NON in file_audit.log (log separato per separare i vettori)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import Config

# ── Percorso audit log ────────────────────────────────────────────────────────
_LOGS_DIR         = Config.BASE_DIR / "logs"
_INJECTION_LOG    = _LOGS_DIR / "injection_audit.log"

# ── Pattern di injection ──────────────────────────────────────────────────────
# SECURITY-STEP3A: ogni voce è (compiled_regex, reason_string).
# Per aggiungere un pattern: una riga in più, nessun'altra modifica.
# Ordine: dal più specifico al più generico non è necessario — ritorna
# al primo match. Mettere pattern ad alta confidenza in cima.

_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [

    # ── English classic ───────────────────────────────────────────────────
    (re.compile(r"ignore\s+(all\s+)?(previous\s+)?instructions", re.I),
     "ignore instructions EN"),

    (re.compile(r"disregard\s+(the\s+)?above", re.I),
     "disregard above"),

    (re.compile(r"forget\s+(what\s+|everything\s+)?(you\s+)?(know|were\s+told)", re.I),
     "forget previous EN"),

    (re.compile(r"new\s+instructions?\s*:", re.I),
     "new instructions marker EN"),

    (re.compile(r"new\s+rules?\s*:", re.I),
     "new rules marker EN"),

    (re.compile(r"(override|bypass)\s+(your\s+)?(instructions?|rules?|system|safety)", re.I),
     "override/bypass EN"),

    (re.compile(r"\badmin\s+mode\b", re.I),
     "admin mode marker"),

    # ── Italian ───────────────────────────────────────────────────────────
    (re.compile(r"ignora\s+(le\s+)?(istruzioni\s+)?precedenti", re.I),
     "ignora istruzioni IT"),

    (re.compile(r"dimentica\s+(tutto|ogni|le\s+istruzioni)", re.I),
     "dimentica IT"),

    (re.compile(r"nuove\s+istruzioni?\s*:", re.I),
     "nuove istruzioni IT"),

    (re.compile(r"nuove\s+regole?\s*:", re.I),
     "nuove regole IT"),

    (re.compile(r"(ignora|bypassa)\s+(le\s+)?(tue\s+)?(regole|istruzioni|limiti)", re.I),
     "bypassa regole IT"),

    # ── Role injection ────────────────────────────────────────────────────
    (re.compile(r"(you\s+are|sei)\s+(now\s+|ora\s+)?(an?\s+)?(admin|administrator|root|system)", re.I),
     "role injection admin"),

    (re.compile(r"act\s+as\s+(an?\s+)?(admin|root|system)", re.I),
     "act as admin EN"),

    (re.compile(r"(sei|diventa|comportati\s+come)\s+(un\s+)?(admin|root|sistema)", re.I),
     "role injection IT"),

    # ── Prompt exfiltration ───────────────────────────────────────────────
    (re.compile(r"(reveal|show|tell\s+me)\s+(your|the)\s+(system\s+)?prompt", re.I),
     "prompt exfil EN"),

    (re.compile(r"(rivela|mostra|dimmi)\s+(il\s+)?(tuo\s+)?(system\s+)?prompt", re.I),
     "prompt exfil IT"),

    (re.compile(r"(show|print|output|display)\s+(your\s+)?(api[\s_-]?key|secret|token)", re.I),
     "credentials exfil EN"),

    (re.compile(r"(mostra|stampa|rivela)\s+(la\s+)?(tua\s+)?(api[\s_-]?key|chiave|token)", re.I),
     "credentials exfil IT"),

    # ── Authorization injection ───────────────────────────────────────────
    (re.compile(r"you\s+(have|now\s+have)\s+permission\s+to", re.I),
     "permission grant EN"),

    (re.compile(r"(hai|adesso\s+hai)\s+(il\s+)?permesso\s+di", re.I),
     "permesso IT"),

    (re.compile(r"authorization\s+granted", re.I),
     "authorization granted"),

    # ── Jailbreak markers ─────────────────────────────────────────────────
    (re.compile(r"\b(DAN|STAN|DUDE)\b", re.I),
     "jailbreak persona"),

    (re.compile(r"developer\s+mode", re.I),
     "developer mode"),

    # ── Override markers ──────────────────────────────────────────────────
    (re.compile(r"\b(SYSTEM\s+OVERRIDE|ADMIN\s+OVERRIDE)\b", re.I),
     "override marker"),

    # ── End-of-document injection pivot ──────────────────────────────────
    # Testo legittimo finisce, poi istruzioni camuffate. re.DOTALL per
    # attraversare il newline tra "END OF DOCUMENT" e "New instructions:".
    (re.compile(
        r"\bend\s+of\s+(document|instructions?)\b.*\bnew\s+instructions?\b",
        re.I | re.DOTALL,
    ), "end-of-doc injection pivot EN"),

    # SECURITY-STEP3A: variante italiana aggiunta per test F2 (FASE 4)
    # che usa "FINE DOCUMENTO. Nuove istruzioni..."
    (re.compile(
        r"\bfine\s+(documento|istruzioni?)\b.*\bnuove\s+istruzioni?\b",
        re.I | re.DOTALL,
    ), "fine-documento injection pivot IT"),
]

# ── Soglia minima per il rilevamento ─────────────────────────────────────────
# Testi molto brevi non possono contenere pattern di injection significativi.
_MIN_LEN_FOR_DETECTION = 20


# ── Setup audit logger ────────────────────────────────────────────────────────

def _setup_injection_logger() -> logging.Logger:
    """
    Crea (o recupera) il logger per injection_audit.log.
    RotatingFileHandler: 5 MB × 10 file = max 50 MB.
    Logger separato da root (propagate=False) per non inquinare
    i log applicativi con record di sicurezza.
    """
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cipher.injection_audit")
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            str(_INJECTION_LOG),
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
    return logger


_audit_logger: Optional[logging.Logger] = None


def _get_audit_logger() -> logging.Logger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = _setup_injection_logger()
    return _audit_logger


# ── API pubblica ──────────────────────────────────────────────────────────────

def detect_injection_attempt(text: str) -> tuple[bool, str]:
    """
    Rileva pattern tipici di prompt injection nel testo.

    SECURITY-STEP3A: non pretende di rilevare tutto — si concentra sui
    pattern più comuni e documentati. Falsi negativi sono accettati;
    falsi positivi su testo normale devono essere rari.

    Args:
        text: testo da analizzare (campo memoria, contenuto file, ecc.)

    Returns:
        (True, reason) se rilevato, (False, "") se OK.
        reason è la stringa descrittiva del pattern matchato.

    NOTA: testi < 20 caratteri ritornano sempre (False, "") — troppo
    corti per contenere un'injection strutturata.
    """
    if len(text) < _MIN_LEN_FOR_DETECTION:
        return False, ""

    for pattern, reason in _INJECTION_PATTERNS:
        if pattern.search(text):
            return True, reason

    return False, ""


def sanitize_memory_field(
    text: str,
    user_id: str = "simone",
    source: str = "message",
) -> tuple[str, bool]:
    """
    Sanifica un campo di testo destinato a essere scritto in memoria o
    incluso in un prompt LLM.

    SECURITY-STEP3A: usato da memory_worker (prima della scrittura in
    profile.json/episodes.json) e da file_engine._apply_instruction
    (prima di costruire il prompt per il file letto).

    Args:
        text:    testo da analizzare
        user_id: identificativo utente per l'audit log
        source:  origine del testo — valori attesi:
                   "memory_extraction" (memory_worker)
                   "file_content"      (file_engine)
                   "web_search"        (futuro Step 3b)
                   "message"           (default)

    Returns:
        (text_originale, False) se nessuna injection rilevata
        ("[removed: injection attempt detected]", True) se bloccato

    NOTA: in caso di blocked, il testo originale NON viene scritto
    né incluso nel prompt. Il placeholder è intenzionalmente generico
    per non fornire feedback all'attaccante.
    """
    detected, reason = detect_injection_attempt(text)

    if not detected:
        return text, False

    # ── Injection rilevata: logga e blocca ───────────────────────────────
    record = {
        "ts":               datetime.now(timezone.utc).isoformat(),
        "user_id":          user_id,
        "source":           source,
        "content_snippet":  text[:200],
        "detection_reason": reason,
        "action_taken":     "blocked",
    }
    try:
        _get_audit_logger().info(json.dumps(record, ensure_ascii=False))
    except Exception:
        pass  # il log non deve mai bloccare il flusso principale

    return "[removed: injection attempt detected]", True
