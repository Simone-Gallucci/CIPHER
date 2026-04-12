"""
modules/utils.py – Utility condivise per Cipher

Funzioni riutilizzabili usate da più moduli:
- Parsing JSON da risposte LLM
- Scrittura JSON atomica (thread-safe e process-safe)
"""

import json
import threading
from pathlib import Path
from typing import Optional

# Lock globale per proteggere la finestra write → rename (thread-safe).
# rename() è atomico su Linux sullo stesso filesystem → process-safe.
_write_lock = threading.Lock()


def extract_action_json(text: str) -> Optional[dict]:
    """
    Estrae il primo oggetto JSON valido con chiave 'action' dal testo.

    Usa depth-tracking carattere per carattere per gestire correttamente
    JSON annidati (es. params: {"key": {"nested": ...}}).
    Ritorna None se non trova nessun oggetto JSON con chiave 'action'.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict) and "action" in data:
                            return data
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def extract_llm_json(text: str) -> Optional[dict | list]:
    """
    Estrae un oggetto JSON da una risposta LLM che può contenere
    blocchi markdown (```json ... ```) o testo libero prima/dopo.

    Strategia:
    1. Se c'è un blocco markdown, lo estrae e lo usa come candidato.
    2. Trova il primo { o [ con depth-tracking per gestire strutture annidate.
    3. Ritorna il dict/list parsato, o None se non trova JSON valido.
    """
    # 1. Strip blocco markdown se presente
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:          # parti dispari = dentro i backtick
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned.startswith("{") or cleaned.startswith("["):
                text = cleaned
                break

    # 2. Trova il primo JSON completo con depth-tracking
    open_idx = -1
    open_ch  = ""
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            open_idx = i
            open_ch  = ch
            break

    if open_idx == -1:
        return None

    close_ch = "}" if open_ch == "{" else "]"
    depth = 0

    for i, ch in enumerate(text[open_idx:], open_idx):
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                candidate = text[open_idx:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None

    return None


def write_json_atomic(path: Path, data, **dump_kwargs) -> None:
    """
    Scrive dati JSON in modo atomico su disco.

    - Thread-safe: lock durante write + rename.
    - Process-safe: Path.rename() è atomico su Linux (stesso filesystem),
      quindi anche memory_worker.py e server.py non si pestano i piedi.

    Sostituisce il pattern: path.write_text(json.dumps(data, ...))
    nei punti in cui più thread o processi scrivono lo stesso file.
    """
    dump_kwargs.setdefault("ensure_ascii", False)
    dump_kwargs.setdefault("indent", 2)
    content  = json.dumps(data, **dump_kwargs)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with _write_lock:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.rename(path)
