"""
memory_worker.py – Servizio autonomo di consolidamento memoria

Gira come processo separato (cipher-memory.service).
Monitora i file di conversazione scritti da Brain, rileva nuovi scambi
completi (user + assistant) e decide cosa salvare nella memoria persistente.
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

from openai import OpenAI

# Aggiungi cipher-server al path per importare config e moduli
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config
from modules.memory import Memory
from modules.episodic_memory import EpisodicMemory

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] memory: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("cipher.memory")

POLL_INTERVAL = 8          # secondi tra un controllo e l'altro
STATE_FILE    = Config.MEMORY_DIR / "memory_worker_state.json"
CONV_DIR      = Config.MEMORY_DIR / "conversations"

_PROMPT = """Sei Cipher. Analizza questo scambio con Simone.

Identifica SOLO le informazioni che vale la pena salvare a lungo termine —
cose utili da ricordare nelle sessioni future.

Salva:
- Dati personali di Simone (lavoro, città, progetti, relazioni, salute)
- Preferenze o abitudini espresse
- Decisioni importanti prese insieme
- Contesto su progetti o situazioni in corso
- Fatti tecnici o info rilevanti condivisi da Simone
- Momenti emotivamente significativi

NON salvare:
- Conversazioni banali o di routine
- Cose già ovvie o precedentemente note
- Semplici domande senza contenuto durevole

Scambio:
Simone: {user_msg}
Cipher: {assistant_msg}

Rispondi SOLO con JSON (lista vuota se non c'è niente da salvare):
{{"save": [
  {{"type": "personal|preference|fact|episode", "key": "campo (opzionale)", "value": "cosa salvare"}}
]}}
Solo JSON, nessuna spiegazione."""


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _call_llm(client: OpenAI, prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model=Config.OPENROUTER_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
            extra_headers={"X-Title": "Cipher MemoryWorker"},
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
    except Exception as e:
        log.error("Errore LLM: %s", e)
        return ""


def _process_exchange(
    user_msg: str,
    assistant_msg: str,
    client: OpenAI,
    memory: Memory,
    episodic: EpisodicMemory,
) -> int:
    result = _call_llm(
        client,
        _PROMPT.format(
            user_msg=user_msg[:600],
            assistant_msg=assistant_msg[:600],
        ),
    )
    if not result:
        return 0

    match = re.search(r'\{.*\}', result, re.DOTALL)
    if not match:
        return 0

    try:
        data = json.loads(match.group())
    except Exception:
        return 0

    saved = 0
    for item in data.get("save", []):
        item_type = item.get("type", "fact")
        key       = item.get("key", "")
        value     = item.get("value", "")
        if not value:
            continue

        if item_type == "episode":
            episodic.add_episode(
                content=value,
                episode_type="observation",
                tags=["memory_worker"],
            )
            saved += 1
        elif item_type in ("personal", "preference"):
            memory.update_profile(key, value, category=item_type)
            saved += 1
        else:
            memory.add_fact(value)
            saved += 1

    return saved


def _check_conversations(
    state: dict,
    client: OpenAI,
    memory: Memory,
    episodic: EpisodicMemory,
) -> dict:
    if not CONV_DIR.exists():
        return state

    conv_files = sorted(CONV_DIR.glob("*.json"))
    if not conv_files:
        return state

    # Lavora sul file più recente (sessione corrente)
    current_file = conv_files[-1]
    file_key     = current_file.name

    last_count = state.get(file_key, 0)

    try:
        data     = json.loads(current_file.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
    except Exception:
        return state

    # Cerca coppie user+assistant non ancora processate
    i = last_count
    processed = last_count
    while i < len(messages) - 1:
        msg_a = messages[i]
        msg_b = messages[i + 1]

        if msg_a.get("role") == "user" and msg_b.get("role") == "assistant":
            saved = _process_exchange(
                user_msg=msg_a.get("content", ""),
                assistant_msg=msg_b.get("content", ""),
                client=client,
                memory=memory,
                episodic=episodic,
            )
            if saved:
                log.info("%d elemento/i salvato/i da scambio #%d", saved, i // 2 + 1)
            processed = i + 2
            i += 2
        else:
            i += 1

    if processed > last_count:
        state[file_key] = processed
        _save_state(state)

    return state


def main() -> None:
    log.info("MemoryWorker avviato (poll ogni %ds)", POLL_INTERVAL)

    client   = OpenAI(
        api_key=Config.OPENROUTER_API_KEY,
        base_url=Config.OPENROUTER_BASE_URL,
    )
    memory   = Memory()
    episodic = EpisodicMemory()
    state    = _load_state()

    while True:
        try:
            state = _check_conversations(state, client, memory, episodic)
        except Exception as e:
            log.error("Errore nel loop: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
