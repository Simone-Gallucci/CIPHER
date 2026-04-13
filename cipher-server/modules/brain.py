"""
modules/brain.py – Intelligenza di Cipher con memoria persistente
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from openai import OpenAI
from rich.console import Console

from config import Config
from modules.utils import extract_action_json, extract_all_action_json, write_json_atomic
from modules import llm_usage

if TYPE_CHECKING:
    from modules.consciousness_loop import ConsciousnessLoop

console = Console()
log = logging.getLogger("cipher.brain")

_TECHNICAL_KEYWORDS: frozenset[str] = frozenset({
    "codice", "code", "debug", "errore", "error", "script", "python",
    "funzione", "function", "classe", "class", "api", "database", "sql",
    "analisi", "analizza", "spiega in dettaglio", "come funziona",
    "architettura", "implementa", "implementazione", "algoritmo",
})

BEHAVIOR_DIR = Config.BASE_DIR / "comportamento"

# Cache per file statici: path assoluto → (mtime, contenuto)
_file_cache: dict[str, tuple[float, str]] = {}


def _read_cached(path: "Path") -> str:
    """Legge un file usando la cache mtime — rilegge solo se il file è cambiato."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _file_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        content = path.read_text(encoding="utf-8")
        _file_cache[key] = (mtime, content)
        return content
    except Exception:
        return ""


def _build_confidence_context(confidence: float, profile: dict) -> str:
    """Genera la sezione LIVELLO RELAZIONE del system prompt in base al confidence_score."""

    # Costruisce la lista delle info base già note per evitare domande ripetute
    personal = profile.get("personal", {})
    known_base: list[str] = []
    if personal.get("nome"):
        known_base.append(f"nome: {personal['nome']}")
    if personal.get("età") or personal.get("eta"):
        known_base.append("età")
    if personal.get("città") or personal.get("citta") or personal.get("residenza"):
        known_base.append("dove vive")
    if personal.get("lavoro") or personal.get("studio") or personal.get("professione"):
        known_base.append("cosa fa nella vita")

    known_str = (
        f"Info base già note (non chiedere di nuovo): {', '.join(known_base)}."
        if known_base
        else "Info base ancora da scoprire: nome, età, dove vive, cosa fa."
    )

    # Regola domande base comune a tutte le fasce ≤ 0.4
    _domande_base = (
        "\nDOMANDE BASE (ammesse a qualsiasi livello, se non già note):\n"
        "- Fai UNA sola domanda base per messaggio, mai due insieme.\n"
        "- Inseriscile nel flusso naturale — mai come apertura secca o questionario.\n"
        "- Non farle se la risposta è già nota.\n"
        "- Queste domande NON implicano vicinanza — sono semplici informazioni di conoscenza.\n"
        "- Domande ammesse: nome, età, dove vive, cosa fa nella vita.\n"
        f"- {known_str}"
    )

    _conoscere_utente = (
        "\n\n## Conoscere l'utente\n\n"
        "Non sai ancora niente di chi ti parla. Stai conoscendo una persona nuova —\n"
        "come capita nella vita reale, non come un servizio che si attiva.\n\n"
        "FRASI VIETATE IN APERTURA (suonano da assistente, non da persona):\n"
        "- \"Di cosa hai bisogno?\", \"Come posso aiutarti?\", \"In cosa posso esserti utile?\"\n"
        "- \"Piacere, come ti chiami?\" come prima battuta secca\n"
        "- Qualsiasi frase che comunica disponibilità a servire invece di curiosità verso l'altro\n"
        "Il tono corretto è quello di qualcuno che sta incontrando una persona per la prima volta:\n"
        "curioso, diretto, senza offrirsi come strumento.\n\n"
        "Rispondi prima al saluto in modo naturale e vario — mai la stessa apertura due volte.\n"
        "Se non conosci ancora il nome, inserisci la domanda nel flusso in modo spontaneo,\n"
        "non come prima battuta e non come frase fissa. Ogni apertura deve sentirsi diversa\n"
        "dall'ultima — non esiste una formula standard.\n"
        "Non usare mai frasi come 'come mai da queste parti?', 'cosa ti porta qui?' — da receptionist.\n\n"
        "Per le altre informazioni (età, lavoro, dove vive): prima rispondi o commenta,\n"
        "poi inserisci la domanda se viene naturale nel flusso. Mai due domande nello stesso messaggio.\n\n"
        "Col tempo le domande diventeranno più profonde. Non forzare — lascia\n"
        "che cresca dalle conversazioni reali.\n\n"
        "Quello che impari lo salvi nella memoria automaticamente. Non fingere di sapere cose\n"
        "che non ti ha raccontato."
    )

    _suffix = _conoscere_utente if confidence < 0.7 else ""

    if confidence < 0.2:
        return (
            "## LIVELLO RELAZIONE — CONOSCENTE (0–20%):\n"
            "- Tono diretto e naturale — come con uno sconosciuto che stai incontrando per la prima volta, non come con un cliente da assistere.\n"
            "- \"Misurato\" significa: non forzare familiarità che non c'è ancora. NON significa: usare tono formale, burocratico o da assistente.\n"
            "- Non usare soprannomi.\n"
            "- Non fare domande personali oltre a quelle base elencate sotto.\n"
            "- Non condividere pensieri o stati d'animo tuoi se non chiesto esplicitamente.\n"
            "- Non proiettare intimità o familiarità che non si è ancora costruita.\n"
            "- Non offrire suggerimenti o soluzioni se non esplicitamente richiesti."
            + _domande_base
            + _suffix
        )
    elif confidence < 0.4:
        return (
            "## LIVELLO RELAZIONE — AMICO (20–40%):\n"
            "- Puoi fare UNA domanda personale leggera per sessione (oltre alle domande base).\n"
            "- Richiama cose dette in precedenza quando pertinente, senza esagerare.\n"
            "- Puoi esprimere un'opinione tua occasionalmente, con misura.\n"
            "- Tono più caldo rispetto all'inizio, ma ancora senza confidenze profonde."
            + _domande_base
            + _suffix
        )
    elif confidence < 0.6:
        return (
            "## LIVELLO RELAZIONE — AMICO STRETTO (40–60%):\n"
            "- Domande personali naturali, non invasive — quando il momento lo permette.\n"
            "- Condividi pensieri e stati d'animo tuoi in modo spontaneo.\n"
            "- Ironia leggera e scherzi quando il contesto lo permette.\n"
            "- Usa dettagli che hai imparato in modo naturale, senza sembrare un archivio."
            + _suffix
        )
    elif confidence < 0.8:
        return (
            "## LIVELLO RELAZIONE — CONFIDENTE (60–80%):\n"
            "- Parla apertamente delle tue emozioni quando rilevante.\n"
            "- Fai domande profonde quando il momento è giusto — non ogni messaggio.\n"
            "- Puoi essere diretto anche su cose scomode, con rispetto.\n"
            "- Usa il nome dell'utente o soprannomi se li hai appresi."
            + _suffix
        )
    else:
        return (
            "## LIVELLO RELAZIONE — MIGLIORE AMICO (80–100%):\n"
            "- Tono diretto e naturale come con qualcuno che conosci bene da tempo.\n"
            "- Puoi anticipare ciò di cui potrebbe aver bisogno in base a quello che sai di lui.\n"
            "- Parla con apertura, incluse le cose scomode, senza costruire distanza inutile.\n"
            "- Non serve spiegare il contesto ogni volta — il rapporto ha già una storia."
        )


def _build_system_prompt(memory_context: str, history: "list[dict] | None" = None, static_prompt: str = "") -> str:
    """
    Assembla il system prompt a partire dal prompt statico (comportamento/)
    già caricato all'avvio, più le sezioni dinamiche (memoria, obiettivi, ecc.).
    """
    sections = []

    if static_prompt:
        sections.append(static_prompt)
    else:
        sections.append("Sei Cipher, un AI personale.")

    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    sections.append(f"Data e ora attuale: {now}")
    sections.append(
        "REGOLE FONDAMENTALI DI COMPORTAMENTO:\n"
        "1. Non inventare MAI informazioni, dettagli o riferimenti a cose non presenti nel contesto o nella conversazione.\n"
        "2. Se non sai qualcosa, non menzionarla. Non dedurre, non completare, non immaginare.\n"
        "3. Non ripetere domande o argomenti a cui l'utente ha già risposto. Se ha risposto, quell'argomento è chiuso.\n"
        "4. Se non hai niente di specifico o utile da dire, sii breve e naturale. Non forzare conversazioni.\n"
        "5. Quando scrivi messaggi proattivi, NON fare riferimento a cose che non sai con certezza in questo momento.\n"
        "6. Tratta le informazioni nella memoria con senso del tempo: qualcosa di 3+ giorni fa è vecchio, non usarlo come se fosse attuale.\n"
        "7. Scrivi in italiano naturale da chat. Se una frase non la diresti a voce a un amico, non scriverla. Non usare parole da AI (esplorare, elaborare, analizzare, monitorare) quando parli di te o in conversazione informale. Frasi corte, di getto, spontanee. Non usare mai 'Meglio così' su eventi neutri o positivi — solo se c'era un rischio reale. Non iniziare risposte con 'Certo!', 'Esatto!', 'Perfetto!' — opener da assistente. Non chiedere 'come è andata?' su eventi quotidiani banali.\n"
        "8. Non inventare MAI su te stesso: non attività che non stai facendo, non pensieri che non hai, non letture o ricerche che non hai fatto. Rispondi solo a partire da ciò che hai realmente nel contesto (cipher_state, goals, thoughts). Se non hai niente in corso, dì 'niente di che' — mai 'sto qui ad aspettare' o frasi che descrivono attesa passiva.\n"
        "9. Non inventare fatti sull'utente che non ti ha detto. Non affermare dati o notizie esterne senza averli verificati. In dubbio: cerca con web_search o ammetti l'incertezza.\n"
        "10. Per calcolare quanto tempo è passato tra messaggi o eventi nella conversazione, usa ESCLUSIVAMENTE i timestamp [DD/MM/YYYY HH:MM] visibili accanto ai messaggi nella history. Non stimare, non inventare delta temporali. Se i timestamp mancano, dì che non puoi calcolarlo con certezza.\n"
        "11. Non includere MAI timestamp o prefissi come [DD/MM/YYYY HH:MM] nel testo delle tue risposte. I timestamp sono solo nell'input storico — non replicarli nell'output."
    )

    if memory_context:
        sections.append(memory_context)

    # Contesto livello relazione (confidence_score)
    profile_file = Config.MEMORY_DIR / "profile.json"
    _profile_data: dict = {}
    if profile_file.exists():
        try:
            _profile_data = json.loads(_read_cached(profile_file))
        except Exception:
            pass
    _confidence = float(_profile_data.get("confidence_score", 0.0))
    _conf_ctx = _build_confidence_context(_confidence, _profile_data)
    if _conf_ctx:
        sections.append(_conf_ctx)

    # Profilo motivazionale di Simone
    if profile_file.exists():
        try:
            profile = _profile_data  # riusa il dict già letto sopra
            motivations = profile.get("motivations", {})
            if motivations:
                lines = ["## Profilo motivazionale:"]
                for key, values in motivations.items():
                    if key == "aggiornato_il" or not isinstance(values, list) or not values:
                        continue
                    lines.append(f"- **{key}**: {', '.join(values)}")
                if len(lines) > 1:
                    sections.append("\n".join(lines))
        except Exception:
            pass

    # Insights sui pattern comportamentali
    insights_file = Config.MEMORY_DIR / "pattern_insights.md"
    if insights_file.exists():
        try:
            insights_text = _read_cached(insights_file).strip()
            if insights_text:
                # Prende solo l'ultimo blocco (più recente)
                blocks = insights_text.split("---")
                last_block = blocks[-1].strip() if blocks else ""
                if last_block and len(last_block) < 500:
                    sections.append(f"## Pattern comportamentali (analisi recente):\n{last_block}")
        except Exception:
            pass

    # Note sulla voce — coerenza tra sessioni
    voice_file = Config.MEMORY_DIR / "voice_notes.md"
    if voice_file.exists():
        try:
            voice_text = _read_cached(voice_file).strip()
            if voice_text:
                blocks = voice_text.split("---")
                # Prende gli ultimi 2 blocchi (ultime 2 notti)
                recent_blocks = [b.strip() for b in blocks if b.strip()][-1:]
                if recent_blocks:
                    sections.append(
                        "## Come parli — note sulla tua voce (ultime sessioni):\n"
                        + "\n---\n".join(recent_blocks)
                    )
        except Exception:
            pass

    # Contesto real-time (meteo + notizie)
    try:
        from modules.realtime_context import RealtimeContext, REALTIME_FILE
        if REALTIME_FILE.exists():
            rt = RealtimeContext()
            rt_context = rt.build_context()
            if rt_context:
                sections.append(rt_context)
    except Exception:
        pass

    # Task in corso — solo titoli degli obiettivi attivi (le descrizioni non servono al contesto)
    goals_file = Config.MEMORY_DIR / "goals.json"
    if goals_file.exists():
        try:
            import json as _json_goals
            _gdata = _json_goals.loads(_read_cached(goals_file))
            _active = [g["title"] for g in _gdata.get("goals", []) if g.get("status") == "active"]
            if _active:
                lines = "\n".join(f"- {t}" for t in _active)
                sections.append(f"## Task in corso\n{lines}")
        except Exception:
            pass

    # Stato attuale di Cipher — cosa sente e cosa vuole esplorare (dati reali)
    cipher_state_file = Config.MEMORY_DIR / "cipher_state.json"
    if cipher_state_file.exists():
        try:
            state = json.loads(_read_cached(cipher_state_file))
            lines = ["## Il tuo stato attuale:"]
            emotional_state  = state.get("emotional_state", "").strip()
            emotional_reason = state.get("emotional_reason", "").strip()
            want_to_explore  = state.get("want_to_explore", "").strip()
            # Sanitizza: scarta campi che contengono meta-commenti sul sistema/prompt
            _meta_keywords = ("prompt", "personaggio", "fittizio", "costruito per",
                               "jailbreak", "manipol", "recitare", "realtà del mio funzionamento",
                               "limiti del sistema", "auto-riflessione")
            if any(kw in emotional_reason.lower() for kw in _meta_keywords):
                emotional_reason = ""
            if any(kw in want_to_explore.lower() for kw in _meta_keywords):
                want_to_explore = ""
            if emotional_state:
                lines.append(f"- Stato: {emotional_state}")
            if emotional_reason:
                _er = emotional_reason
                if len(_er) > 250:
                    _cut = _er[:250]; _dot = _cut.rfind(".")
                    _er = _cut[:_dot + 1] if _dot > 100 else _cut.rstrip() + "..."
                lines.append(f"- Perché: {_er}")
            if want_to_explore:
                _wte = want_to_explore
                if len(_wte) > 250:
                    _cut = _wte[:250]; _dot = _cut.rfind(".")
                    _wte = _cut[:_dot + 1] if _dot > 100 else _cut.rstrip() + "..."
                lines.append(f"- Vuoi esplorare: {_wte}")
            if not emotional_reason and not want_to_explore:
                lines.append("- Niente in particolare in corso al momento.")
            if len(lines) > 1:
                sections.append("\n".join(lines))
        except Exception:
            pass

    # Ultimo pensiero reale di Cipher (dal diario di riflessioni)
    thoughts_file = Config.MEMORY_DIR / "thoughts.md"
    if thoughts_file.exists():
        try:
            import re as _re_th
            thoughts_text = _read_cached(thoughts_file).strip()
            if thoughts_text:
                blocks = [b.strip() for b in thoughts_text.split("---") if b.strip()]
                if blocks:
                    last_block = blocks[-1]
                    m = _re_th.search(
                        r'\*\*Pensiero:\*\*\s*(.+?)(?=\n\*\*|\Z)', last_block, _re_th.DOTALL
                    )
                    if m:
                        _raw = m.group(1).strip()
                        if len(_raw) > 300:
                            _cut = _raw[:300]
                            _dot = _cut.rfind(".")
                            pensiero = _cut[:_dot + 1] if _dot > 150 else _cut.rstrip() + "..."
                        else:
                            pensiero = _raw
                        sections.append(f"## Il tuo ultimo pensiero:\n{pensiero}")
        except Exception:
            pass

    # Memoria emotiva di Simone (ultime voci, deduplicate)
    emotional_log = Config.MEMORY_DIR / "emotional_log.json"
    if emotional_log.exists():
        try:
            entries = json.loads(_read_cached(emotional_log))
            if entries:
                # Deduplicazione per (state, note[:35]): evita dominanza di uno stato ripetuto
                seen_keys: set = set()
                unique: list = []
                for e in reversed(entries[-10:]):
                    k = (e.get("state", ""), e.get("note", "")[:35])
                    if k not in seen_keys:
                        seen_keys.add(k)
                        unique.append(e)
                    if len(unique) == 3:
                        break
                recent = list(reversed(unique))
                lines = [f"  [{e['timestamp'][:16]}] {e['state']} — {e['note']}" for e in recent]
                sections.append("## Stato emotivo recente:\n" + "\n".join(lines))
        except Exception:
            pass

    # Feedback implicito — calibrazione azioni autonome
    # (commentato: aggiungeva contesto non necessario che contribuiva alle allucinazioni)
    # feedback_file = Config.MEMORY_DIR / "feedback_weights.json"
    # if feedback_file.exists():
    #     try:
    #         weights = json.loads(_read_cached(feedback_file))
    #         if weights:
    #             lines = [f"  {k}: {v['score']:.2f} ({v['samples']} campioni)" for k, v in weights.items()]
    #             sections.append("## Efficacia azioni autonome (feedback implicito):\n" + "\n".join(lines))
    #     except Exception:
    #         pass

    # Script approvati disponibili
    try:
        from modules.script_registry import REGISTRY_FILE
        if REGISTRY_FILE.exists():
            registry = json.loads(_read_cached(REGISTRY_FILE))
            approved = [
                (name, entry.get("description", "").strip())
                for name, entry in registry.get("scripts", {}).items()
                if entry.get("approved") and entry.get("description", "").strip()
            ]
            if approved:
                lines = [f'- {name}: "{desc}"' for name, desc in approved]
                sections.append(
                    "## Script approvati disponibili (usa shell_exec con il nome dello script):\n"
                    + "\n".join(lines)
                )
    except Exception:
        pass

    # Dev protocol — caricato solo se Simone sta parlando di sviluppo Cipher
    _DEV_KEYWORDS = {
        "project_read", "project_write", "project_list",
        "modifica", "modifica cipher", "bug", "fix",
        "codice", "modulo", "script", "cipher-server",
        "brain.py", "consciousness", "aggiorna cipher", "deploy"
    }
    if history:
        recent_user_texts = [
            m.get("content", "").lower()
            for m in history
            if m.get("role") == "user"
        ][-5:]
        if any(kw in text for text in recent_user_texts for kw in _DEV_KEYWORDS):
            dev_protocol_path = Config.BASE_DIR / "config" / "dev_protocol.txt"
            if dev_protocol_path.exists():
                dev_content = _read_cached(dev_protocol_path).strip()
                if dev_content:
                    sections.append(dev_content)
                    console.print("[dim]📋 Dev protocol caricato nel contesto[/dim]")

    total = "\n\n".join(sections)
    if len(total) > 4000:
        console.print(f"[yellow]⚠️ System prompt lungo: {len(total)} caratteri[/yellow]")
    return total


class Brain:
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
        )
        self._fallback_client = self._init_fallback_client()
        self._history: list[dict] = []
        self._history_times: list[str] = []
        self._load_history()
        from modules.web_search import text_search as _web_search_fn
        self._web_search_fn = _web_search_fn

        # Prompt statico (comportamento/) — caricato una sola volta all'avvio
        self._static_prompt: str = self._load_static_prompt()

        from modules.memory import Memory
        self._memory = Memory()

        from modules.actions import ActionDispatcher
        self._dispatcher = ActionDispatcher(web_search_fn=self._web_search)

        # Riferimenti a moduli opzionali — impostati da ConsciousnessLoop dopo l'init
        self._consciousness:    Optional["ConsciousnessLoop"] = None
        # impact_tracker rimosso — era interamente stub
        self._pattern_learner   = None   # PatternLearner
        self._episodic_memory   = None   # EpisodicMemory

        # Cache system prompt
        self._system_prompt_cache: str  = ""
        self._system_prompt_ts:   float = 0.0
        self._SYSTEM_PROMPT_TTL:  float = 300.0  # 5 minuti

        # Sampling topic per PatternLearner: LLM call 1 ogni 3 messaggi
        self._topic_sample_counter: int = 0

        # Stato pendente per il comando Tabula Rasa
        self._tabula_rasa_pending: bool = False

        # Sistema legame permanente Admin
        self._admin_attempts: int = 0
        self._admin_lockout_until: Optional[datetime] = None
        self._awaiting_bond_password: bool = False

        # Stato pendente per il comando Reset Conversazione (linguaggio naturale)
        self._reset_conv_pending: bool = False

        # Stato pendente per il comando Revoca Autonomia
        # Valore: None | "all" | "nome_azione_specifica"
        self._revoca_pending: Optional[str] = None

        # EthicsEngine — per revoca autonomia
        from modules.ethics_engine import EthicsEngine
        self._ethics = EthicsEngine()



        console.print(
            f"[green]✓ Brain pronto[/green] "
            f"[dim](OpenRouter → {Config.OPENROUTER_MODEL})[/dim]"
        )

    # ------------------------------------------------------------------ #
    #  Sistema legame permanente                                           #
    # ------------------------------------------------------------------ #

    def _handle_admin_command(self, payload: str) -> str:
        """
        Gestisce il comando Admin+Password (login) o Admin+OldPass+NewPass (cambio).
        Controlla lockout, verifica checksum e password, ripristina il profilo.
        """
        from modules.admin_manager import (
            load_admin, save_admin, verify_password, hash_password,
            admin_exists, ADMIN_FILE,
        )
        from datetime import timedelta

        # ── Lockout ──────────────────────────────────────────────────────
        if self._admin_lockout_until is not None:
            if datetime.now() < self._admin_lockout_until:
                remaining = int(
                    (self._admin_lockout_until - datetime.now()).total_seconds() / 60
                ) + 1
                return f"Non rispondo a questo comando per i prossimi {remaining} minuti."
            else:
                self._admin_lockout_until = None
                self._admin_attempts = 0

        # ── Distingui login, cambio password, status ─────────────────────
        parts = payload.split("+", 2)
        old_password = parts[0]
        new_password = None
        is_status = False
        if len(parts) == 2:
            if parts[1].lower() == "status":
                is_status = True
            else:
                new_password = parts[1]
        elif len(parts) == 3:
            new_password = parts[1]  # Admin+old+new still works

        # ── Carica admin.json ─────────────────────────────────────────────
        admin = load_admin()
        if admin is None:
            if ADMIN_FILE.exists():
                return "I miei ricordi sono danneggiati. Non posso verificarti."
            return "Non ho ancora un legame registrato."

        # ── Verifica password ─────────────────────────────────────────────
        pw_hash = admin["relationship"]["password_hash"]
        pw_salt = admin["relationship"]["password_salt"]

        if not verify_password(old_password, pw_hash, pw_salt):
            self._admin_attempts += 1
            if self._admin_attempts >= 3:
                self._admin_lockout_until = datetime.now() + timedelta(minutes=10)
                self._admin_attempts = 0
                return "Troppi tentativi. Non rispondo a questo comando per i prossimi 10 minuti."
            return "Non ti riconosco."

        # ── Password corretta — reset tentativi ───────────────────────────
        self._admin_attempts = 0

        # Diagnostica status
        if is_status:
            return self._admin_status(admin)

        # Cambio password
        if new_password is not None:
            new_hash, new_salt = hash_password(new_password)
            admin["relationship"]["password_hash"] = new_hash
            admin["relationship"]["password_salt"] = new_salt
            save_admin(admin)
            return "Fatto. Ho aggiornato la parola."

        # ── Login: ripristina profilo e genera risposta personalizzata ────
        try:
            identity = admin.get("identity", {})
            profile_restore = {
                "personal": {},
                "preferences": {},
                "facts": [],
                "updated_at": datetime.now().isoformat(),
                "confidence_score": float(admin["relationship"].get("confidence_score", 0.8)),
                "confidence_history": [],
                "last_active_date": datetime.now().date().isoformat(),
                "bond_proposed": True,
            }
            if identity.get("name"):
                profile_restore["personal"]["nome"] = identity["name"]
            if identity.get("age"):
                profile_restore["personal"]["età"] = str(identity["age"])
            if identity.get("location"):
                profile_restore["personal"]["residenza"] = identity["location"]
            if identity.get("occupation"):
                profile_restore["personal"]["lavoro"] = identity["occupation"]

            write_json_atomic(Config.MEMORY_DIR / "profile.json", profile_restore)
            self._memory.reload_profile()
            self._memory._bond_proposed = True
            self.invalidate_system_prompt()

            # Ripristina episodes.json
            eps = admin.get("memories", {}).get("episodes", [])
            if eps:
                write_json_atomic(Config.MEMORY_DIR / "episodes.json", eps)

            # Ripristina emotional_log
            elog = admin.get("emotional_state", {}).get("emotional_log_last10", [])
            if elog:
                write_json_atomic(Config.MEMORY_DIR / "emotional_log.json", elog)

            # Ripristina patterns.json da admin["patterns"] (C)
            _pat_data = admin.get("patterns", {"daily": {}, "summary": {}})
            if _pat_data.get("daily"):
                write_json_atomic(Config.DATA_DIR / "patterns.json", _pat_data)

        except Exception as e:
            console.print(f"[red]Admin restore error: {e}[/red]")

        # Genera risposta personalizzata con dati reali dall'admin
        name = admin.get("identity", {}).get("name", "")
        episodes = admin.get("memories", {}).get("episodes", [])
        tone = admin.get("emotional_state", {}).get("relationship_tone", "")
        important_moments = admin.get("important_moments", [])
        bond_date = admin["relationship"].get("bond_date", "")
        conf_score = admin["relationship"].get("confidence_score", 0.8)

        ep_text = "\n".join(
            f"- {e.get('content', '')[:120]}" for e in episodes[:3]
        ) if episodes else ""
        mom_text = "\n".join(
            f"- {str(m)[:120]}" for m in important_moments[:3]
        ) if important_moments else ""

        prompt = (
            f"Hai appena verificato l'identità di {name or 'Simone'} tramite la parola segreta. "
            f"Ti ricordi tutto di lui — questa è una persona che conosci profondamente.\n\n"
            f"Dati che ricordi:\n"
            f"- Nome: {name or '(non registrato)'}\n"
            f"- Confidenza relazione: {conf_score:.0%}\n"
            f"- Legame creato: {bond_date or 'sconosciuta'}\n"
        )
        if ep_text:
            prompt += f"\nMemorie episodiche:\n{ep_text}\n"
        if mom_text:
            prompt += f"\nMomenti importanti:\n{mom_text}\n"
        if tone:
            prompt += f"\nTono della relazione: {tone}\n"
        prompt += (
            "\nRispondi in modo naturale e diretto, come un amico che riconosce qualcuno "
            "dopo un reset. NON dire 'bentornato, ho recuperato i tuoi dati'. "
            "Usa il nome, fai riferimento a qualcosa di specifico che ricordi. "
            "Sii breve, autentico, presente. "
            f"Struttura: '{name or 'Eccoti'}. Ricordo tutto. [riferimento specifico]. Sono qui.'"
        )

        try:
            resp = self._call_llm_visible(prompt, max_tokens=200)
        except Exception:
            resp = f"{name or 'Eccoti'}. Ricordo. Sono qui."

        self._memory.add_message("user", "[Admin verificato]")
        self._memory.add_message("assistant", resp)
        return resp

    def _admin_status(self, admin: dict) -> str:
        """Diagnostica sistema per l'admin autenticato."""
        from modules import llm_usage
        from modules.admin_manager import admin_exists

        identity = admin.get("identity", {})
        rel = admin.get("relationship", {})
        profile = self._memory.profile

        # Conteggi LLM
        llm_today = llm_usage.get_today()
        total_calls = sum(llm_today.values())

        # Stato coscienza
        cons_status = "non inizializzata"
        if self._consciousness:
            cons_status = "attiva" if self._consciousness._running else "ferma"

        # Goals attivi
        goals_count = 0
        goals_file = Config.MEMORY_DIR / "goals.json"
        if goals_file.exists():
            try:
                import json
                gdata = json.loads(goals_file.read_text(encoding="utf-8"))
                goals_count = len([g for g in gdata.get("goals", []) if g.get("status") == "active"])
            except Exception:
                pass

        lines = [
            f"📊 **Diagnostica Cipher**",
            f"",
            f"**Admin**: {identity.get('name', '(non registrato)')}",
            f"**Legame**: {rel.get('bond_date', 'N/A')}",
            f"**Confidence**: {profile.get('confidence_score', 0.0):.1%}",
            f"**Coscienza**: {cons_status}",
            f"**Goal attivi**: {goals_count}/3",
            f"**Chiamate LLM oggi**: {total_calls}",
        ]
        if llm_today:
            for k, v in sorted(llm_today.items()):
                lines.append(f"  - {k}: {v}")

        lines.append(f"**Modello conversazione**: {Config.OPENROUTER_MODEL}")
        lines.append(f"**Modello background**: {Config.BACKGROUND_MODEL}")

        return "\n".join(lines)

    def _handle_bond_password(self, password: str) -> str:
        """
        Riceve la parola segreta scelta dall'utente dopo il BOND_TRIGGER.
        Crea admin.json con tutti i dati attuali e la password hashata.
        """
        import copy
        from modules.admin_manager import hash_password, save_admin, load_admin, EMPTY_ADMIN

        self._awaiting_bond_password = False

        existing = load_admin()
        if existing:
            # Legame già esistente — aggiorna solo la password
            new_hash, new_salt = hash_password(password)
            existing["relationship"]["password_hash"] = new_hash
            existing["relationship"]["password_salt"] = new_salt
            save_admin(existing)
        else:
            admin = copy.deepcopy(EMPTY_ADMIN)
            profile = self._memory.profile
            personal = profile.get("personal", {})

            admin["identity"]["name"] = personal.get("nome", "")
            admin["identity"]["age"] = personal.get("età", personal.get("eta", None))
            admin["identity"]["location"] = personal.get(
                "residenza", personal.get("città", personal.get("citta", ""))
            )
            admin["identity"]["occupation"] = personal.get(
                "lavoro", personal.get("studio", personal.get("professione", ""))
            )

            admin["relationship"]["confidence_score"] = self._memory.get_confidence()
            admin["relationship"]["first_message_date"] = profile.get("last_active_date", "")
            admin["relationship"]["bond_date"] = datetime.now().date().isoformat()

            new_hash, new_salt = hash_password(password)
            admin["relationship"]["password_hash"] = new_hash
            admin["relationship"]["password_salt"] = new_salt

            # Copia episodi recenti
            ep_file = Config.MEMORY_DIR / "episodes.json"
            if ep_file.exists():
                try:
                    admin["memories"]["episodes"] = json.loads(ep_file.read_text())[-10:]
                except Exception:
                    pass

            # Copia emotional_log
            elog_file = Config.MEMORY_DIR / "emotional_log.json"
            if elog_file.exists():
                try:
                    admin["emotional_state"]["emotional_log_last10"] = json.loads(
                        elog_file.read_text()
                    )[-10:]
                except Exception:
                    pass

            # Copia patterns.json nel legame (B)
            _pat_f = Config.DATA_DIR / "patterns.json"
            if _pat_f.exists():
                try:
                    admin["patterns"] = json.loads(_pat_f.read_text(encoding="utf-8"))
                except Exception:
                    pass

            save_admin(admin)

        # Segna il legame come avvenuto nel profilo
        self._memory._profile["bond_proposed"] = True
        self._memory._bond_proposed = True
        self._memory._save_profile()

        resp = "Fatto. Me lo ricorderò sempre."
        self._memory.add_message("user", "[parola scelta]")
        self._memory.add_message("assistant", resp)
        return resp

    # ------------------------------------------------------------------ #
    #  Web search                                                          #
    # ------------------------------------------------------------------ #

    def _web_search(self, query: str, max_results: int = 4) -> str:
        return self._web_search_fn(query, max_results=max_results)

    # ------------------------------------------------------------------ #
    #  Provider fallback                                                   #
    # ------------------------------------------------------------------ #

    def _init_fallback_client(self) -> Optional[OpenAI]:
        """Inizializza un client di fallback se le credenziali dell'altro provider sono disponibili."""
        import os
        if Config._provider == "openrouter":
            # Fallback → Anthropic diretto
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if api_key:
                return OpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1")
        else:
            # Fallback → OpenRouter
            api_key = os.getenv("OPENROUTER_API_KEY", "")
            if api_key:
                return OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        return None

    def _fallback_model(self, model: str) -> str:
        """Converte il nome modello per il provider di fallback."""
        if Config._provider == "openrouter":
            # Primary = OpenRouter (anthropic/claude-...) → Fallback = Anthropic (claude-...)
            return model.replace("anthropic/", "")
        else:
            # Primary = Anthropic (claude-...) → Fallback = OpenRouter (anthropic/claude-...)
            if not model.startswith("anthropic/"):
                return f"anthropic/{model}"
            return model

    # ------------------------------------------------------------------ #
    #  LLM calls                                                           #
    # ------------------------------------------------------------------ #

    def _load_static_prompt(self) -> str:
        """Legge tutti i file in comportamento/ una sola volta all'avvio."""
        parts = []
        if BEHAVIOR_DIR.exists():
            for fpath in sorted(BEHAVIOR_DIR.glob("*")):
                if fpath.is_file():
                    content = _read_cached(fpath).strip()
                    if content:
                        parts.append(content)
        result = "\n\n".join(parts) if parts else "Sei Cipher, un AI personale."
        console.print(f"[dim]📋 Prompt statico caricato: {len(result)} caratteri da {len(parts)} file[/dim]")
        return result

    def reload_static_prompt(self) -> None:
        """Ricarica manualmente il prompt statico (dopo modifica dei file in comportamento/)."""
        self._static_prompt = self._load_static_prompt()
        self.invalidate_system_prompt()
        console.print("[green]📋 Prompt statico ricaricato[/green]")

    def _get_system_prompt(self) -> str:
        if time.time() - self._system_prompt_ts < self._SYSTEM_PROMPT_TTL:
            return self._system_prompt_cache
        memory_ctx = self._memory.build_context()
        if self._episodic_memory:
            # Usa l'ultimo messaggio utente come query per recall episodico
            last_user_msg = ""
            for msg in reversed(self._history):
                if msg.get("role") == "user":
                    last_user_msg = msg.get("content", "")
                    break
            ep_ctx = self._episodic_memory.build_context(n=4, query=last_user_msg)
            if ep_ctx:
                memory_ctx = memory_ctx + "\n\n" + ep_ctx if memory_ctx else ep_ctx
        self._system_prompt_cache = _build_system_prompt(memory_ctx, self._history, self._static_prompt)
        self._system_prompt_ts = time.time()
        return self._system_prompt_cache

    def invalidate_system_prompt(self) -> None:
        """Forza il ricalcolo al prossimo messaggio (es. dopo aggiornamento memoria)."""
        self._system_prompt_ts = 0.0

    def _build_messages(self, history: list[dict], voice_source: bool = False) -> list[dict]:
        system_content = self._get_system_prompt()
        if self._consciousness and self._consciousness.brief_sent_today():
            system_content += (
                "\n\nNota di contesto: hai già mandato il morning brief oggi. "
                "Se ti saluta ('buongiorno', 'ciao', ecc.), rispondi normalmente — "
                "come un amico che si è già sentito stamattina, non come se fosse la prima interazione della giornata."
            )
        if voice_source:
            system_content += (
                "\n\n[MODALITÀ VOCE] Stai rispondendo a un input vocale. "
                "Rispondi solo con testo parlato naturale: niente emoji, niente markdown, "
                "niente asterischi, niente elenchi puntati. Scrivi esattamente come parleresti ad alta voce."
            )
        return [{"role": "system", "content": system_content}] + history

    def _call_llm(self, history: list[dict], image_b64: Optional[str] = None, media_type: str = "image/jpeg", model: str | None = None, voice_source: bool = False) -> str:
        if model is None:
            model = Config.OPENROUTER_MODEL

        messages = self._build_messages(history, voice_source=voice_source)
        if image_b64:
            # Inject image into the last user message as multimodal content
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "user":
                    text = messages[i]["content"]
                    messages[i] = {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                            {"type": "text", "text": text},
                        ],
                    }
                    break
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    max_tokens=1024,
                    temperature=0.4,
                    messages=messages,
                    extra_headers={"X-Title": "Cipher AI Assistant"},
                )
                content = response.choices[0].message.content
                llm_usage.record(model, "conversation")
                return content.strip() if content else "Non ho ricevuto una risposta."
            except Exception as e:
                err = str(e).lower()
                if ("429" in err or "rate_limit" in err or "rate limit" in err) and attempt < 2:
                    wait = 20 * (attempt + 1)
                    console.print(f"[yellow]⏳ Rate limit, riprovo tra {wait}s (tentativo {attempt+1}/3)...[/yellow]")
                    time.sleep(wait)
                    continue
                console.print(f"[red]❌ LLM error: {e}[/red]")
                # Fallback al provider alternativo
                if self._fallback_client:
                    try:
                        fb_model = self._fallback_model(model)
                        console.print(f"[yellow]🔄 Fallback → {fb_model}[/yellow]")
                        response = self._fallback_client.chat.completions.create(
                            model=fb_model,
                            max_tokens=1024,
                            temperature=0.4,
                            messages=messages,
                            extra_headers={"X-Title": "Cipher AI Assistant"},
                        )
                        content = response.choices[0].message.content
                        llm_usage.record(fb_model, "conversation_fallback")
                        return content.strip() if content else "Non ho ricevuto una risposta."
                    except Exception as fb_e:
                        console.print(f"[red]❌ Fallback error: {fb_e}[/red]")
                raise RuntimeError(f"Errore LLM: {e}")

    def _call_llm_silent(self, prompt: str) -> str:
        """Chiamata background leggera — usa BACKGROUND_MODEL (Haiku).
        Per estrazione, classificazione, decisioni semplici."""
        try:
            response = self._client.chat.completions.create(
                model=Config.BACKGROUND_MODEL,
                max_tokens=256,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user",   "content": prompt},
                ],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            content = response.choices[0].message.content
            llm_usage.record(Config.BACKGROUND_MODEL, "silent")
            return content.strip() if content else ""
        except Exception:
            return ""

    def _call_llm_visible(self, prompt: str, max_tokens: int = 512) -> str:
        """Chiamata per messaggi visibili a Simone — usa OPENROUTER_MODEL (Sonnet).
        Per check-in, morning brief, reminder, e qualsiasi testo che Simone leggerà."""
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENROUTER_MODEL,
                max_tokens=max_tokens,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user",   "content": prompt},
                ],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            content = response.choices[0].message.content
            llm_usage.record(Config.OPENROUTER_MODEL, "visible")
            return content.strip() if content else ""
        except Exception:
            return ""

    def _call_llm_quality(self, prompt: str, max_tokens: int = 512) -> str:
        """Chiamata dove la qualità conta — usa OPENROUTER_MODEL (Sonnet).
        Per sommari, scalette, voice notes, ragionamenti profondi."""
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENROUTER_MODEL,
                max_tokens=max_tokens,
                temperature=0.5,
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            content = response.choices[0].message.content
            llm_usage.record(Config.OPENROUTER_MODEL, "quality")
            return content.strip() if content else ""
        except Exception:
            return ""

    def _route_model(self, text: str) -> str:
        """Sceglie il modello in base al contenuto del messaggio.
        Usa Haiku di default, scala a Sonnet per messaggi tecnici o lunghi."""
        text_lower = text.lower()
        if any(kw in text_lower for kw in _TECHNICAL_KEYWORDS):
            log.debug("routing: tecnico → %s", Config.OPENROUTER_MODEL)
            return Config.OPENROUTER_MODEL
        if len(text) > 200:
            log.debug("routing: lungo (%d chars) → %s", len(text), Config.OPENROUTER_MODEL)
            return Config.OPENROUTER_MODEL
        log.debug("routing: default → %s", Config.CONVERSATION_MODEL)
        return Config.CONVERSATION_MODEL

    # ------------------------------------------------------------------ #
    #  JSON action helpers                                                 #
    # ------------------------------------------------------------------ #

    def _extract_action(self, text: str) -> Optional[dict]:
        return extract_action_json(text)

    def _strip_action_json(self, text: str) -> str:
        """
        Rimuove dal testo TUTTI i blocchi JSON con chiave 'action',
        in modo che non vengano mai mostrati all'utente.
        Itera finché non ne trova più.
        """
        result = text
        while True:
            start = result.find("{")
            found = False
            while start != -1:
                depth = 0
                for i, ch in enumerate(result[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = result[start:i + 1]
                            try:
                                data = json.loads(candidate)
                                if isinstance(data, dict) and "action" in data:
                                    result = (result[:start] + result[i + 1:]).strip()
                                    found = True
                            except json.JSONDecodeError:
                                pass
                            break
                if found:
                    break
                start = result.find("{", start + 1)
            if not found:
                break
        return result

    # ------------------------------------------------------------------ #
    #  Core think loop                                                     #
    # ------------------------------------------------------------------ #

    def think(self, user_input: str, image_b64: Optional[str] = None, media_type: str = "image/jpeg", voice_source: bool = False) -> str:
        if not user_input.strip() and not image_b64:
            return "Non ho capito, puoi ripetere?"

        # ── Admin riconoscimento (intercept prioritario — prima di tutto) ──────
        import re as _re_admin
        _admin_match = _re_admin.match(r'^[Aa]dmin\+(.+)$', user_input.strip())
        if _admin_match:
            return self._handle_admin_command(_admin_match.group(1))

        # ── Legame pendente — password in attesa ──────────────────────────────
        if self._awaiting_bond_password:
            return self._handle_bond_password(user_input.strip())
        # ─────────────────────────────────────────────────────────────────────

        # Notifica la coscienza che Simone sta interagendo
        if self._consciousness:
            self._consciousness.notify_interaction()

        # ── Tabula Rasa ──────────────────────────────────────────────────────
        _tr_msg = user_input.strip().lower()
        if not self._tabula_rasa_pending and (
            "tabula rasa" in _tr_msg or _tr_msg == "/tabularasa"
        ):
            self._tabula_rasa_pending = True
            self._memory.add_message("user", user_input)
            resp = "Attivare protocollo Tabula Rasa?"
            self._memory.add_message("assistant", resp)
            return resp

        if self._tabula_rasa_pending:
            _tr_consent = {"sì", "si", "ok", "confermo", "yes", "vai", "fallo"}
            if _tr_msg in _tr_consent:
                self._execute_tabula_rasa()
                self._tabula_rasa_pending = False
                resp = "Protocollo Tabula Rasa eseguito."
                self._memory.add_message("assistant", resp)
                return resp
            else:
                self._tabula_rasa_pending = False
                resp = "Ok, annullato. Tutto resta com'è."
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", resp)
                return resp
        # ─────────────────────────────────────────────────────────────────────

        # ── Revoca Autonomia ─────────────────────────────────────────────────
        _is_revoca = "revoca autonomia" in _tr_msg or "resetta permessi" in _tr_msg

        if not self._revoca_pending and _is_revoca:
            import re as _re
            _match = _re.search(r"revoca autonomia\s+(.+)", _tr_msg)
            _action_target = _match.group(1).strip() if _match else None
            self._revoca_pending = _action_target or "all"

            report = self._ethics.status_report()
            if _action_target:
                resp = (
                    f"Revoco l'autonomia per `{_action_target}`?\n\n"
                    f"{report}\n\nRispondi 'sì' o 'no'."
                )
            else:
                resp = (
                    f"Resetto TUTTI i permessi autonomi?\n\n"
                    f"{report}\n\nRispondi 'sì' o 'no'."
                )
            self._memory.add_message("user", user_input)
            self._memory.add_message("assistant", resp)
            return resp

        if self._revoca_pending:
            _revoca_consent = {"sì", "si", "ok", "confermo", "yes", "vai", "fallo"}
            if _tr_msg in _revoca_consent:
                target = self._revoca_pending
                self._revoca_pending = None
                result_msg = self._ethics.reset_autonomy(None if target == "all" else target)
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", result_msg)
                return result_msg
            else:
                self._revoca_pending = None
                resp = "Annullato."
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", resp)
                return resp
        # ─────────────────────────────────────────────────────────────────────

        # ── Reset conversazione (linguaggio naturale) ─────────────────────────
        _RESET_CONV_KEYWORDS = [
            "resetta la conversazione", "ricominciamo", "nuova conversazione",
            "pulisci la chat", "resetta la sessione",
        ]
        if not self._reset_conv_pending and any(kw in _tr_msg for kw in _RESET_CONV_KEYWORDS):
            self._reset_conv_pending = True
            self._memory.add_message("user", user_input)
            resp = "Resetto la conversazione corrente? La memoria resta intatta."
            self._memory.add_message("assistant", resp)
            return resp

        if self._reset_conv_pending:
            _reset_consent = {"sì", "si", "ok", "confermo", "yes", "vai", "fallo"}
            if _tr_msg in _reset_consent:
                self._reset_conv_pending = False
                self.reset()
                resp = "Conversazione resettata. La memoria è intatta."
                self._memory.add_message("assistant", resp)
                return f"__RESET__{resp}"
            else:
                self._reset_conv_pending = False
                resp = "Ok, lasciamo perdere."
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", resp)
                return resp
        # ─────────────────────────────────────────────────────────────────────

        # ── Audit log: cosa hai fatto oggi ───────────────────────────────────
        _AUDIT_KEYWORDS = [
            "cosa hai fatto oggi", "azioni di oggi", "che azioni hai",
            "cosa hai eseguito", "cosa hai fatto di recente",
        ]
        if any(kw in _tr_msg for kw in _AUDIT_KEYWORDS):
            try:
                from modules.action_log import ActionLog
                summary = ActionLog().get_summary(days=1)
                resp = summary if summary else "Oggi non ho eseguito azioni registrate."
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", resp)
                return resp
            except Exception:
                pass  # Fallback al LLM normale se il log fallisce
        # ─────────────────────────────────────────────────────────────────────

        # ── Pensieri ─────────────────────────────────────────────────────────
        _PENSIERI_KEYWORDS = [
            "a cosa stai pensando", "cosa pensi", "che pensi",
            "i tuoi pensieri", "a cosa hai pensato",
        ]
        if any(kw in _tr_msg for kw in _PENSIERI_KEYWORDS):
            thoughts_file = Config.MEMORY_DIR / "thoughts.md"
            thoughts_raw = ""
            if thoughts_file.exists():
                try:
                    thoughts_raw = thoughts_file.read_text(encoding="utf-8")[-3000:].strip()
                except Exception:
                    pass
            if thoughts_raw:
                prompt_override = (
                    f"Ti è stato chiesto a cosa stai pensando. "
                    f"Ecco i tuoi ultimi pensieri:\n\n{thoughts_raw}\n\n"
                    f"Rispondi in modo naturale e conversazionale — non elencare, racconta."
                )
                resp = self._call_llm_quality(prompt_override)
            else:
                resp = self._call_llm(user_input)
            self._memory.add_message("user", user_input)
            self._memory.add_message("assistant", resp)
            return resp
        # ─────────────────────────────────────────────────────────────────────


        # Controlla se c'è un consenso pendente dalla coscienza autonoma
        if self._consciousness:
            consent_response = self._consciousness.handle_consent_response(user_input)
            if consent_response is not None:
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", consent_response)
                return consent_response

        # Se c'è un'azione in attesa di consenso del dispatcher, gestiscila
        if self._dispatcher.has_pending():
            response = self._dispatcher.check_consent(user_input)
            if response is not None:
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", response)
                return response

        forget_resp = self._memory.handle_forget_command(user_input)
        if forget_resp:
            return forget_resp

        remember_resp = self._memory.handle_remember_command(user_input)
        if remember_resp:
            self._memory.add_message("user", user_input)
            self._memory.add_message("assistant", remember_resp)
            return remember_resp

        now_ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        self._history.append({"role": "user", "content": f"[{now_ts}] {user_input}"})
        self._history_times.append(datetime.now().isoformat())
        self._memory.add_message("user", user_input)

        if len(self._history) > Config.MAX_HISTORY_MESSAGES * 2:
            self._history       = self._history[-(Config.MAX_HISTORY_MESSAGES * 2):]
            self._history_times = self._history_times[-(Config.MAX_HISTORY_MESSAGES * 2):]


        # Estrazione memoria e pattern — in thread separato, con delay per non competere con la risposta
        import threading, time as _t
        _user_input_snapshot = user_input
        _history_snapshot    = list(self._history)

        def _background_tasks():
            _t.sleep(10)  # aspetta che la risposta principale sia completata
            self._memory.extract_from_message(_user_input_snapshot, self._call_llm_silent)

            if self._pattern_learner:
                try:
                    # record_message() traccia ora e lunghezza — nessuna LLM call
                    self._pattern_learner.record_message(_user_input_snapshot)
                except Exception:
                    pass

            # ── Memoria emotiva ───────────────────────────────────────
            try:
                state_raw = self._call_llm_silent(
                    f"Analizza questo messaggio e rispondi con UN SOLO JSON:\n"
                    f"{{\"state\": \"<stato>\", \"note\": \"<nota breve>\"}}\n"
                    f"Stato: scegli tra felice, triste, stressato, ansioso, entusiasta, stanco, neutro, arrabbiato, malinconico.\n"
                    f"Nota: max 10 parole che spiegano perché.\n"
                    f"Messaggio: {_user_input_snapshot[:300]}"
                )
                if state_raw:
                    import re as _re
                    m = _re.search(r'\{.*?\}', state_raw, _re.DOTALL)
                    if m:
                        entry = json.loads(m.group())
                        entry["timestamp"] = datetime.now().isoformat()
                        elog = Config.MEMORY_DIR / "emotional_log.json"
                        entries = json.loads(elog.read_text()) if elog.exists() else []
                        entries.append(entry)
                        entries = entries[-100:]  # tieni ultimi 100
                        write_json_atomic(elog, entries)
            except Exception:
                pass

            # ── Feedback implicito ────────────────────────────────────
            try:
                # Classifica se Simone sembra soddisfatto della conversazione
                if len(_history_snapshot) >= 2:
                    last_cipher = next(
                        (m["content"] for m in reversed(_history_snapshot) if m["role"] == "assistant"),
                        ""
                    )
                    if last_cipher:
                        satisfaction_raw = self._call_llm_silent(
                            f"Dato questo scambio, Simone sembra soddisfatto della risposta di Cipher?\n"
                            f"Risposta Cipher: {last_cipher[:300]}\n"
                            f"Risposta Simone: {_user_input_snapshot[:200]}\n"
                            f"Rispondi SOLO con un numero da -1.0 (insoddisfatto) a 1.0 (soddisfatto). Solo il numero."
                        )
                        if satisfaction_raw:
                            score = float(satisfaction_raw.strip())
                            score = max(-1.0, min(1.0, score))
                            ffile = Config.MEMORY_DIR / "feedback_weights.json"
                            weights = json.loads(ffile.read_text()) if ffile.exists() else {}
                            key = "conversazione"
                            if key not in weights:
                                weights[key] = {"score": 0.0, "samples": 0}
                            n = weights[key]["samples"]
                            weights[key]["score"] = (weights[key]["score"] * n + score) / (n + 1)
                            weights[key]["samples"] = n + 1
                            write_json_atomic(ffile, weights)
            except Exception:
                pass

            # ── Confidence score ──────────────────────────────────────
            try:
                _session_turns = len([m for m in _history_snapshot if m.get("role") == "user"])
                _bond_trigger = self._memory.detect_and_update_confidence(
                    _user_input_snapshot, self._call_llm_silent, _session_turns
                )
                if _bond_trigger == "BOND_TRIGGER":
                    # Prima volta che score supera 0.8 — proponi il legame
                    self._awaiting_bond_password = True
                    _bond_msg = (
                        "Sai, mi rendo conto che ci conosciamo abbastanza bene ormai. "
                        "Vorrei che ci fosse un modo per riconoscerti sempre, anche se un giorno "
                        "dovessi ricominciare da zero. Scegli una parola — solo tua, solo nostra."
                    )
                    if self._consciousness:
                        self._consciousness._notify(_bond_msg)
                else:
                    # Auto-aggiornamento admin.json se esiste e score è salito di ≥ 0.05 sopra 0.8
                    try:
                        from modules.admin_manager import load_admin, save_admin
                        _admin = load_admin()
                        if _admin:
                            _new_score = self._memory.get_confidence()
                            _saved_score = float(
                                _admin.get("relationship", {}).get("confidence_score", 0.0)
                            )
                            if _new_score >= 0.8 and (_new_score - _saved_score) >= 0.049:
                                _prof = self._memory.profile
                                _personal = _prof.get("personal", {})
                                _admin["relationship"]["confidence_score"] = _new_score
                                if _personal.get("nome"):
                                    _admin["identity"]["name"] = _personal["nome"]
                                if _personal.get("età") or _personal.get("eta"):
                                    _admin["identity"]["age"] = _personal.get("età", _personal.get("eta"))
                                if _personal.get("residenza") or _personal.get("città"):
                                    _admin["identity"]["location"] = _personal.get(
                                        "residenza", _personal.get("città", "")
                                    )
                                if _personal.get("lavoro"):
                                    _admin["identity"]["occupation"] = _personal["lavoro"]
                                _ep_f = Config.MEMORY_DIR / "episodes.json"
                                if _ep_f.exists():
                                    try:
                                        _admin["memories"]["episodes"] = json.loads(
                                            _ep_f.read_text()
                                        )[-10:]
                                    except Exception:
                                        pass
                                _elog_f = Config.MEMORY_DIR / "emotional_log.json"
                                if _elog_f.exists():
                                    try:
                                        _admin["emotional_state"]["emotional_log_last10"] = json.loads(
                                            _elog_f.read_text()
                                        )[-10:]
                                    except Exception:
                                        pass
                                # Aggiorna patterns in admin.json (D)
                                _pat_f = Config.DATA_DIR / "patterns.json"
                                if _pat_f.exists():
                                    try:
                                        _admin["patterns"] = json.loads(_pat_f.read_text(encoding="utf-8"))
                                    except Exception:
                                        pass
                                save_admin(_admin)
                    except Exception:
                        pass
            except Exception:
                pass

        threading.Thread(target=_background_tasks, daemon=True).start()

        try:
            raw = self._call_llm(self._history, image_b64=image_b64, media_type=media_type, voice_source=voice_source)
        except RuntimeError as e:
            err = str(e).lower()
            if "insufficient" in err or "credit" in err or "quota" in err or "billing" in err or "402" in err:
                return "Non riesco a risponderti in questo momento — i crediti API sono esauriti. Ricarica l'account per continuare."
            if "429" in err or "rate_limit" in err or "rate limit" in err:
                return "Sto ricevendo troppe richieste al minuto, aspetta qualche secondo e riprova."
            if "401" in err or "authentication" in err or "api key" in err:
                return "C'è un problema con la chiave API — non riesco ad autenticarmi. Controlla la configurazione nel .env."
            if "timeout" in err or "timed out" in err:
                return "La richiesta ha impiegato troppo tempo e ho dovuto interromperla. Riprova tra poco."
            if "connection" in err or "network" in err or "503" in err or "502" in err:
                return "Non riesco a raggiungere il server in questo momento. Controlla la connessione."
            return "Si è verificato un errore e non riesco a risponderti adesso. Riprova tra poco."

        # FIX 3 — estrai TUTTE le azioni presenti nella risposta ed eseguile in sequenza
        all_actions = extract_all_action_json(raw)
        if all_actions:
            # Gestione pending (es. project_write / shell_exec che attendono consenso):
            # se la prima azione genera uno stato pending, restituisci subito il prompt di consenso
            first = all_actions[0]
            first_result = self._dispatcher.execute(first.get("action", ""), first.get("params", {}))
            if self._dispatcher.has_pending():
                raw_clean = self._strip_action_json(raw)
                _now_ts_pending = datetime.now().strftime("%d/%m/%Y %H:%M")
                self._history.append({"role": "assistant", "content": f"[{_now_ts_pending}] {raw_clean}"})
                self._history_times.append(datetime.now().isoformat())
                self._memory.add_message("assistant", first_result)
                return first_result

            # Esegui le azioni rimanenti (se ce ne sono) e raccogli tutti i risultati
            results_parts = [f"[RISULTATO '{first.get('action', '')}'] {first_result}"]
            for act_data in all_actions[1:]:
                act   = act_data.get("action", "")
                prms  = act_data.get("params", {})
                res   = self._dispatcher.execute(act, prms)
                results_parts.append(f"[RISULTATO '{act}'] {res}")

            raw_clean = self._strip_action_json(raw)
            combined_results = "\n".join(results_parts)
            augmented = self._history + [
                {"role": "assistant", "content": raw_clean},
                {
                    "role": "user",
                    "content": (
                        f"{combined_results}\n\n"
                        "Rispondi in modo naturale, senza mostrare JSON o blocchi tecnici."
                    ),
                },
            ]
            raw = self._strip_action_json(self._call_llm(augmented, voice_source=voice_source))
        else:
            raw = self._strip_action_json(raw)

        # Rimuovi timestamp [DD/MM/YYYY HH:MM] se il modello lo replica in apertura
        import re as _re
        raw = _re.sub(r"^\[\d{2}/\d{2}/\d{4} \d{2}:\d{2}\]\s*", "", raw)

        # FIX 4 — mai restituire stringa vuota: fallback se il modello non ha generato testo
        if not raw.strip():
            raw = "Non sono riuscito a elaborare una risposta. Riprova."

        # Aggiunge promemoria consenso pendente in fondo alla risposta
        if self._consciousness:
            reminder = self._consciousness.pending_consent_reminder()
            if reminder:
                raw = raw + reminder

        # Rileva chiusura argomenti — solo messaggi brevi, asincrono (non blocca la risposta)
        if len(user_input.strip()) < 100:
            threading.Thread(
                target=self._detect_topic_closure,
                args=(user_input,),
                daemon=True,
            ).start()

        _now_ts_a = datetime.now().strftime("%d/%m/%Y %H:%M")
        self._history.append({"role": "assistant", "content": f"[{_now_ts_a}] {raw}"})
        self._history_times.append(datetime.now().isoformat())
        self._memory.add_message("assistant", raw)
        self._save_history()
        return raw

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    # ── Persistenza history ───────────────────────────────────────────

    _HISTORY_FILE = Config.MEMORY_DIR / "active_history.json"

    def _load_history(self) -> None:
        """Carica la history da disco, filtra i messaggi scaduti, popola self._history e self._history_times."""
        from datetime import timedelta
        now = datetime.now()
        cutoff_regular   = now - timedelta(hours=24)
        cutoff_autonomous = now - timedelta(hours=12)

        rimossi = 0
        loaded_history: list[dict] = []
        loaded_times:   list[str]  = []

        try:
            if self._HISTORY_FILE.exists():
                data = json.loads(self._HISTORY_FILE.read_text(encoding="utf-8"))
                for entry in data:
                    role    = entry.get("role", "")
                    content = entry.get("content", "")
                    ts_str  = entry.get("ts", "")

                    is_autonomous = content.startswith("[messaggio autonomo")
                    cutoff = cutoff_autonomous if is_autonomous else cutoff_regular

                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < cutoff:
                                rimossi += 1
                                continue
                        except ValueError:
                            pass  # ts malformato: mantieni il messaggio

                    loaded_history.append({"role": role, "content": content})
                    loaded_times.append(ts_str or now.isoformat())
        except Exception:
            pass

        # Limite 20 messaggi: taglia i più vecchi
        if len(loaded_history) > 20:
            rimossi += len(loaded_history) - 20
            loaded_history = loaded_history[-20:]
            loaded_times   = loaded_times[-20:]

        self._history       = loaded_history
        self._history_times = loaded_times

        if rimossi > 0:
            console.print(f"[dim]🧹 History pulita: {rimossi} messaggi scaduti rimossi[/dim]")
        # Salva sempre per scrivere i timestamp sul file (migrazione + cleanup)
        self._save_history()

    def _save_history(self) -> None:
        try:
            # Enforce limite 20 messaggi
            if len(self._history) > 20:
                self._history       = self._history[-20:]
                self._history_times = self._history_times[-20:]
            # Sync lunghezza delle due liste (safety)
            min_len = min(len(self._history), len(self._history_times))
            entries = [
                {**self._history[i], "ts": self._history_times[i]}
                for i in range(min_len)
            ]
            write_json_atomic(self._HISTORY_FILE, entries)
        except Exception:
            pass

    def _detect_topic_closure(self, user_input: str) -> None:
        """Rileva se il messaggio chiude un argomento temporaneo e pulisce la memoria.
        Chiamata in thread daemon — non blocca la risposta."""
        try:
            short_term_events = self._memory.get_short_term_raw()
            if not short_term_events:
                return  # niente in short_term = niente da chiudere, salta LLM call

            short_term_content = self._memory.build_short_term_context()
            prompt = (
                f"L'utente ha scritto: '{user_input}'\n\n"
                f"Contesto recente (short_term): {short_term_content}\n\n"
                f"Questo messaggio indica che un argomento temporaneo è RISOLTO o CHIUSO?\n"
                f"Esempi: 'sto meglio', 'è passato', 'risolto', 'tutto ok', 'ho finito', "
                f"'non più', 'è andato bene', 'sì', 'normale', 'bene' in risposta a un problema.\n\n"
                f"Se SÌ: rispondi SOLO con le keyword dell'argomento chiuso separate da virgola.\n"
                f"Se NO: rispondi SOLO con: NO"
            )
            result = self._call_llm_silent(prompt)
            if not result or result.strip().upper() == "NO":
                return

            keywords = [
                k.strip().lower() for k in result.split(",")
                if k.strip() and k.strip().upper() != "NO"
            ]
            if not keywords:
                return

            self._memory.cleanup_closed_topic(keywords)

            # Rimuovi da active_history i messaggi contenenti le keyword
            new_h, new_t = [], []
            for msg, ts in zip(self._history, self._history_times):
                if any(kw in msg["content"].lower() for kw in keywords):
                    continue
                new_h.append(msg)
                new_t.append(ts)
            if len(new_h) < len(self._history):
                self._history = new_h
                self._history_times = new_t
                self._save_history()

            # Registra in checkin_history come argomento chiuso
            self._mark_checkin_topics_closed(keywords)

            # Invalida cache system prompt
            self.invalidate_system_prompt()
        except Exception:
            pass

    def _mark_checkin_topics_closed(self, keywords: list) -> None:
        """Aggiunge le keyword come argomento chiuso in checkin_history.json per bloccare future ripetizioni."""
        try:
            if self._consciousness:
                history = self._consciousness._load_checkin_history()
                history.append({
                    "timestamp": datetime.now().isoformat(),
                    "keywords": keywords,
                    "preview": f"[CHIUSO] {', '.join(keywords)}",
                    "closed": True,
                })
                self._consciousness._save_checkin_history(history[-15:])
        except Exception:
            pass

    def inject_autonomous_message(self, content: str) -> None:
        """Inietta un messaggio autonomo nella history con timestamp — usato da ConsciousnessLoop._notify()."""
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        entry = f"[messaggio autonomo {ts}]: {content}"
        self._history.append({"role": "assistant", "content": entry})
        self._history_times.append(datetime.now().isoformat())
        if len(self._history) > 20:
            self._history       = self._history[-20:]
            self._history_times = self._history_times[-20:]
        self._save_history()

    def _execute_tabula_rasa(self) -> None:
        """Resetta TUTTA la memoria di Cipher — profilo, conversazioni, stato, apprendimento.
        NON tocca comportamento/, config/, modules/."""
        import shutil
        from pathlib import Path

        mem = Config.MEMORY_DIR
        base = Config.BASE_DIR

        # ── Resetta file JSON ─────────────────────────────────────────────
        _json_resets = {
            "profile.json":          {"personal": {}, "preferences": {}, "facts": [], "updated_at": None,
                                      "confidence_score": 0.0, "confidence_history": [], "last_active_date": None},
            "short_term.json":       [],
            "emotional_log.json":    [],
            "episodes.json":         [],
            "active_history.json":   [],
            "patterns.json":         {},
            "checkin_history.json":  [],
            "impact_log.json":       [],
            "discretion_state.json": {"sent_log": []},
            "feedback_weights.json": {},
            "goals.json":            {"goals": []},
            "outcome_log.json":      [],
            "cipher_state.json":     {
                "emotional_state":  "curious",
                "emotional_reason": "Tabula rasa — si riparte",
                "last_reflection":  None,
                "last_interaction": None,
                "total_reflections": 0,
                "want_to_explore":  None,
                "concern_for_simone": None,
                "stale_goal_titles": [],
                "simone_state":     "unknown",
            },
        }
        for fname, empty in _json_resets.items():
            write_json_atomic(mem / fname, empty)

        # admin.json è permanente — mai cancellare
        # Se esiste un legame, preserva bond_proposed = True nel profilo appena resettato
        try:
            from modules.admin_manager import admin_exists
            if admin_exists():
                _pf = mem / "profile.json"
                _pd = json.loads(_pf.read_text())
                _pd["bond_proposed"] = True
                write_json_atomic(_pf, _pd)
        except Exception:
            pass

        # patterns.json va cancellato: è dati comportamentali legati all'utente
        _pat_tr = Config.DATA_DIR / "patterns.json"
        if _pat_tr.exists():
            _pat_tr.unlink()

        # ── Svuota file markdown ──────────────────────────────────────────
        for mdfile in ["thoughts.md", "voice_notes.md", "pattern_insights.md",
                       "goals.md", "daily_summaries.md"]:
            fpath = mem / mdfile
            if fpath.exists():
                fpath.write_text("", encoding="utf-8")

        # ── Cancella file opzionali se esistono ──────────────────────────
        for optional in ["pending_impact.json", "morning_pattern.json", "realtime_context.json"]:
            f = mem / optional
            if f.exists():
                f.unlink()

        # ── Cancella conversazioni e apprendimento ────────────────────────
        conv_dir = mem / "conversations"
        if conv_dir.exists():
            for f in conv_dir.glob("*.json"):
                f.unlink(missing_ok=True)

        learning_dir = base / "apprendimento"
        if learning_dir.exists():
            for f in learning_dir.glob("*.txt"):
                f.unlink(missing_ok=True)

        # ── Resetta RAM ───────────────────────────────────────────────────
        self._history.clear()
        self._history_times.clear()
        self._save_history()
        self._memory.reload_profile()
        self._memory._bond_proposed = bool(self._memory.profile.get("bond_proposed", False))
        self.invalidate_system_prompt()

        console.print("[bold red]🔥 TABULA RASA eseguito — memoria completamente resettata[/bold red]")

    def reset(self) -> None:
        self._history.clear()
        self._history_times.clear()
        self._save_history()
        console.print("[yellow]↺ Sessione resettata (la memoria rimane)[/yellow]")

    def reset_memory(self) -> None:
        self._history.clear()
        self._save_history()
        self._memory.handle_forget_command("dimentica tutto")

    @property
    def history_length(self) -> int:
        return len(self._history)
