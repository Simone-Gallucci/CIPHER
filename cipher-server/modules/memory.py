"""
modules/memory.py – Memoria persistente di Cipher

SECURITY-STEP4: tutti i path di memoria derivano da get_user_memory_dir(user_id).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from config import Config
from modules.auth import get_user_memory_dir, get_system_owner_id
from modules.utils import write_json_atomic

console = Console()

LEARNING_DIR     = Config.BASE_DIR / "apprendimento"
BEHAVIOR_DIR     = Config.BASE_DIR / "comportamento"

EMPTY_PROFILE = {
    "personal": {},
    "preferences": {},
    "facts": [],
    "updated_at": None,
    "confidence_score": 0.0,
    "confidence_history": [],   # ultimi 20 eventi: {signal, delta, ts}
    "last_active_date": None,   # per streak giorni consecutivi
    "bond_proposed": False,     # True dopo che il trigger legame è scattato
}


class Memory:
    def __init__(self, user_id: str = "") -> None:
        _uid = user_id or get_system_owner_id()
        self._mem_dir        = get_user_memory_dir(_uid)
        self._profile_file   = self._mem_dir / "profile.json"
        self._conv_dir       = self._mem_dir / "conversations"
        self._short_term_file = self._mem_dir / "short_term.json"
        self._conv_dir.mkdir(parents=True, exist_ok=True)
        LEARNING_DIR.mkdir(parents=True, exist_ok=True)
        BEHAVIOR_DIR.mkdir(parents=True, exist_ok=True)
        self._profile               = self._load_profile()
        self._current_conv          = []
        self._session_file          = self._new_session_file()
        self._last_extract          = 0
        self._long_session_credited = False  # reset a ogni avvio di sessione
        self._bond_proposed: bool   = bool(self._profile.get("bond_proposed", False))
        console.print(
            f"[green]✓ Memoria pronta[/green] "
            f"[dim]({self._count_conversations()} conversazioni salvate)[/dim]"
        )

    def _load_profile(self) -> dict:
        if self._profile_file.exists():
            try:
                return json.loads(self._profile_file.read_text())
            except Exception:
                pass
        return dict(EMPTY_PROFILE)

    def _save_profile(self) -> None:
        self._profile["updated_at"] = datetime.now().isoformat()
        write_json_atomic(self._profile_file, self._profile, permissions=0o600)

    def update_profile(self, key: str, value: str, category: str = "personal") -> None:
        if category not in self._profile:
            self._profile[category] = {}
        if category in ("personal", "preferences", "habits"):
            self._profile[category][key] = value
        elif category == "facts":
            fact = f"{key}: {value}" if key else value
            if fact not in self._profile["facts"]:
                self._profile["facts"].append(fact)
        self._save_profile()
        console.print(f"[green]💾 Ricordato:[/green] {key} = {value}")

    def add_fact(self, fact: str) -> None:
        if fact and fact not in self._profile["facts"]:
            self._profile["facts"].append(fact)
            self._save_profile()
            console.print(f"[green]💾 Fatto salvato:[/green] {fact}")

    def _new_session_file(self) -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return self._conv_dir / f"{ts}.json"

    def _save_conversation(self) -> None:
        if not self._current_conv:
            return
        data = {"timestamp": datetime.now().isoformat(), "messages": self._current_conv}
        write_json_atomic(self._session_file, data, permissions=0o600)

    def add_message(self, role: str, content: str) -> None:
        self._current_conv.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        self._save_conversation()

    def _count_conversations(self) -> int:
        return len(list(self._conv_dir.glob("*.json")))

    def build_context(self) -> str:
        parts = []

        # 1. Profilo utente
        profile_lines = []
        for k, v in self._profile.get("personal", {}).items():
            profile_lines.append(f"  - {k}: {v}")
        for k, v in self._profile.get("preferences", {}).items():
            profile_lines.append(f"  - preferenza {k}: {v}")
        for k, v in self._profile.get("habits", {}).items():
            profile_lines.append(f"  - abitudine {k}: {v}")
        for f in self._profile.get("facts", []):
            profile_lines.append(f"  - {f}")
        if profile_lines:
            parts.append("PROFILO UTENTE:\n" + "\n".join(profile_lines))

        # 3. Conversazioni passate — ultimi 20 messaggi completi
        # Legge keyword di argomenti chiusi per filtrare messaggi obsoleti
        _closed_keywords: list[str] = []
        try:
            _checkin_file = self._mem_dir / "checkin_history.json"
            if _checkin_file.exists():
                _checkin_data = json.loads(_checkin_file.read_text())
                for _entry in _checkin_data:
                    if _entry.get("closed"):
                        _closed_keywords.extend(
                            kw.lower() for kw in _entry.get("keywords", [])
                        )
        except Exception:
            pass

        past_files = [f for f in sorted(self._conv_dir.glob("*.json")) if f != self._session_file]
        if past_files:
            all_messages = []
            for fpath in reversed(past_files):
                try:
                    data = json.loads(fpath.read_text())
                    msgs = data.get("messages", [])
                    ts   = data.get("timestamp", "")[:10]

                    # Usa il summary se disponibile (generato da memory_worker)
                    summary = data.get("summary", "").strip()
                    if summary:
                        # Controlla che il summary non parli di argomenti chiusi
                        summary_lower = summary.lower()
                        if _closed_keywords and any(kw in summary_lower for kw in _closed_keywords):
                            continue
                        all_messages.append({
                            "role":    "summary",
                            "content": summary,
                            "date":    ts,
                        })
                        if len(all_messages) >= 20:
                            break
                        continue  # Salta i messaggi raw per questa sessione

                    # Fallback: messaggi raw (sessioni senza summary)
                    for m in reversed(msgs):
                        content_lower = m["content"].lower()
                        # Salta messaggi che parlano di argomenti già chiusi
                        if _closed_keywords and any(kw in content_lower for kw in _closed_keywords):
                            continue
                        all_messages.append({
                            "role": m["role"],
                            "content": m["content"][:500],
                            "date": ts,
                        })
                        if len(all_messages) >= 20:
                            break
                except Exception:
                    continue
                if len(all_messages) >= 20:
                    break

            if all_messages:
                all_messages.reverse()
                lines = []
                for m in all_messages:
                    if m["role"] == "summary":
                        lines.append(f"  [{m['date']}] 📝 {m['content']}")
                    else:
                        icon = "👤" if m["role"] == "user" else "🤖"
                        lines.append(f"  [{m['date']}] {icon} {m['content']}")
                parts.append("CONVERSAZIONI PASSATE:\n" + "\n".join(lines))

        # Short-term events (piani/eventi temporanei delle ultime 24h)
        st_ctx = self.build_short_term_context()
        if st_ctx:
            parts.insert(0, st_ctx)

        if not parts:
            return ""
        return "\n\n━━━ MEMORIA ━━━\n" + "\n\n".join(parts) + "\n━━━━━━━━━━━━━━"

    # ------------------------------------------------------------------ #
    #  Short-term context (eventi temporanei: "stasera esco a cena" ecc) #
    # ------------------------------------------------------------------ #

    def _load_short_term(self) -> list:
        if self._short_term_file.exists():
            try:
                data = json.loads(self._short_term_file.read_text())
                # Scarta eventi più vecchi di 24 ore
                now = datetime.now()
                fresh = []
                for e in data:
                    try:
                        ts = datetime.fromisoformat(e["timestamp"])
                        if (now - ts).total_seconds() < 172800:  # 48 ore
                            fresh.append(e)
                    except Exception:
                        pass
                return fresh
            except Exception:
                pass
        return []

    def _save_short_term(self, events: list) -> None:
        write_json_atomic(self._short_term_file, events, permissions=0o600)

    def add_short_term_event(self, description: str) -> None:
        events = self._load_short_term()
        events.append({"description": description, "timestamp": datetime.now().isoformat()})
        self._save_short_term(events)
        console.print(f"[cyan]📌 Evento temporaneo salvato:[/cyan] {description}")

    def cleanup_short_term(self) -> None:
        """Rimuove voci scadute (> 48 ore) da short_term.json."""
        events = self._load_short_term()
        self._save_short_term(events)  # _load_short_term già filtra per 48h

    def build_short_term_context(self) -> str:
        self.cleanup_short_term()
        events = self._load_short_term()
        if not events:
            return ""
        lines = [f"  [{e['timestamp'][11:16]}] {e['description']}" for e in events]
        return "EVENTI/PIANI RECENTI DI SIMONE (ultime 48h):\n" + "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Confidence score                                                   #
    # ------------------------------------------------------------------ #

    def get_confidence(self) -> float:
        return float(self._profile.get("confidence_score", 0.0))

    def _update_confidence(self, signal: str, delta: float) -> Optional[str]:
        """Incrementa confidence_score e registra l'evento in confidence_history.
        Ritorna 'BOND_TRIGGER' se lo score supera 0.8 per la prima volta, None altrimenti."""
        current = float(self._profile.get("confidence_score", 0.0))
        new_score = min(1.0, current + delta)
        self._profile["confidence_score"] = round(new_score, 4)

        history: list = self._profile.get("confidence_history", [])
        history.append({
            "signal": signal,
            "delta": delta,
            "score_after": self._profile["confidence_score"],
            "ts": datetime.now().isoformat(),
        })
        self._profile["confidence_history"] = history[-20:]  # tieni solo ultimi 20
        self._save_profile()

        if new_score >= 0.8 and not self._bond_proposed:
            self._bond_proposed = True
            self._profile["bond_proposed"] = True
            self._save_profile()
            return "BOND_TRIGGER"
        return None

    def detect_and_update_confidence(
        self, text: str, call_llm_fn, session_msg_count: int = 0
    ) -> Optional[str]:
        """Classifica i segnali nel messaggio dell'utente (via Haiku) e aggiorna il punteggio.
        Ritorna 'BOND_TRIGGER' se lo score supera 0.8 per la prima volta, None altrimenti."""
        import re as _re_conf

        # Inizializza i campi se il profilo è stato caricato da una versione precedente
        if "confidence_score" not in self._profile:
            self._profile["confidence_score"] = 0.0
        if "confidence_history" not in self._profile:
            self._profile["confidence_history"] = []

        # 1. Segnale diretto: sessione lunga (>10 turni utente nella sessione corrente)
        if session_msg_count > 10 and not self._long_session_credited:
            result = self._update_confidence("long_session", 0.010)
            self._long_session_credited = True  # una sola volta per sessione (reset all'avvio)
            if result == "BOND_TRIGGER":
                return "BOND_TRIGGER"

        # 2. Streak giorni consecutivi
        today_str = datetime.now().date().isoformat()
        last_date_str = self._profile.get("last_active_date")
        if last_date_str and last_date_str != today_str:
            from datetime import date, timedelta
            try:
                last_date = date.fromisoformat(last_date_str)
                if date.fromisoformat(today_str) - last_date == timedelta(days=1):
                    result = self._update_confidence("daily_streak", 0.005)
                    if result == "BOND_TRIGGER":
                        return "BOND_TRIGGER"
            except Exception:
                pass
        if last_date_str != today_str:
            self._profile["last_active_date"] = today_str
            self._save_profile()

        # 3. Classificazione segnali via Haiku
        if len(text.strip()) < 5:
            return None
        prompt = (
            "Analizza il messaggio e indica SOLO i segnali chiaramente presenti.\n"
            "Rispondi SOLO con JSON: {\"signals\": [...]}\n\n"
            "Segnali possibili (includi solo quelli chiaramente presenti):\n"
            "- emotion_shared: condivide un'emozione in modo esplicito\n"
            "- personal_story: racconta qualcosa di personale o intimo\n"
            "- nickname_joke: usa un soprannome familiare o scherza in modo affettuoso\n"
            "- advice_request: chiede consiglio su qualcosa di realmente importante per lui\n"
            "- gratitude: ringrazia o esprime apprezzamento esplicito\n\n"
            f"Messaggio: \"{text[:300]}\"\n"
            "Solo il JSON. Nessuna spiegazione."
        )
        _SIGNAL_DELTAS = {
            "emotion_shared":  0.015,
            "personal_story":  0.020,
            "nickname_joke":   0.025,
            "advice_request":  0.030,
            "gratitude":       0.010,
        }
        try:
            raw = call_llm_fn(prompt)
            match = _re_conf.search(r'\{.*\}', raw, _re_conf.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            for signal in data.get("signals", []):
                delta = _SIGNAL_DELTAS.get(signal)
                if delta:
                    result = self._update_confidence(signal, delta)
                    if result == "BOND_TRIGGER":
                        return "BOND_TRIGGER"
        except Exception as e:
            console.print(f"[dim]Confidence detect warning: {e}[/dim]")
        return None

    # ------------------------------------------------------------------ #

    def extract_from_message(self, text: str, call_llm_fn) -> None:
        self._last_extract += 1
        if self._last_extract % 3 != 0 or len(text.strip()) < 10:
            return
        now_str = datetime.now().strftime("%A %d/%m/%Y %H:%M")
        prompt = (
            f'Data e ora attuale: {now_str}\n\n'
            f'Analizza questo messaggio e decidi:\n'
            f'1. Se contiene informazioni personali permanenti da ricordare (nome, età, lavoro, città, preferenze, abitudini quotidiane, routine, orari abituali, attività ricorrenti).\n'
            f'2. Se contiene piani o eventi temporanei (es. "stasera esco a cena", "domani ho un appuntamento", "sono in palestra", "sto andando a dormire").\n\n'
            f'Messaggio: "{text}"\n\n'
            f'IMPORTANTE: estrai SOLO ciò che Simone ha affermato esplicitamente nel messaggio.\n'
            f'Non inferire, non dedurre, non assumere. Se il messaggio è vago o ambiguo, restituisci array vuoti.\n\n'
            f'Rispondi SOLO con JSON:\n'
            f'{{"save": [{{"category": "personal|preferences|facts|habits", "key": "campo", "value": "valore"}}], '
            f'"events": ["descrizione breve evento temporaneo in italiano"]}}\n'
            f'Usa category "habits" per abitudini ricorrenti.\n'
            f'Se non c\'è nulla di rilevante o il messaggio è vago: {{"save": [], "events": []}}\nNessuna spiegazione.'
        )
        try:
            result = call_llm_fn(prompt)
            match = re.search(r'\{.*\}', result, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            for item in data.get("save", []):
                if item.get("value"):
                    self.update_profile(item.get("key", ""), item["value"], item.get("category", "facts"))
            for event in data.get("events", []):
                if event and isinstance(event, str):
                    self.add_short_term_event(event)
        except Exception as e:
            console.print(f"[dim]Memory extract warning: {e}[/dim]")

    # ------------------------------------------------------------------ #
    #  Chiusura argomenti temporanei                                      #
    # ------------------------------------------------------------------ #

    _PROTECTED_FIELDS = {"nome", "residenza", "salute_cronica", "stage", "stage_orario", "sito_web"}

    def cleanup_closed_topic(self, keywords: list) -> int:
        """Rimuove da short_term e profile le voci legate a un argomento chiuso.
        Ritorna il numero totale di voci rimosse."""
        kw_lower = [k.lower() for k in keywords]
        removed = 0

        # 1. short_term.json
        events = self._load_short_term()
        before = len(events)
        events = [
            e for e in events
            if not any(kw in e.get("description", "").lower() for kw in kw_lower)
        ]
        removed += before - len(events)
        self._save_short_term(events)

        # 2. profile.json — facts
        facts_before = len(self._profile.get("facts", []))
        self._profile["facts"] = [
            f for f in self._profile.get("facts", [])
            if not any(kw in f.lower() for kw in kw_lower)
        ]
        removed += facts_before - len(self._profile["facts"])

        # 3. profile.json — personal (solo campi non protetti)
        personal = self._profile.get("personal", {})
        to_delete = [
            k for k, v in personal.items()
            if k not in self._PROTECTED_FIELDS
            and any(kw in str(v).lower() for kw in kw_lower)
        ]
        for k in to_delete:
            del personal[k]
            removed += 1

        if removed > 0:
            self._save_profile()

        console.print(
            f"[dim]🧹 Argomento chiuso: {keywords} — rimossi {removed} riferimenti dalla memoria[/dim]"
        )
        return removed

    def get_short_term_raw(self) -> list:
        """Ritorna le voci short_term grezze (per il rilevamento chiusura argomenti)."""
        return self._load_short_term()

    def handle_remember_command(self, text: str) -> Optional[str]:
        patterns = [
            r"ricorda(?:\s+che)?\s+(.+)",
            r"non dimenticare(?:\s+che)?\s+(.+)",
            r"tieni a mente(?:\s+che)?\s+(.+)",
            r"segna(?:\s+che)?\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                fact = match.group(1).strip()
                if fact:
                    self.add_fact(fact)
                    return f"Ho memorizzato: \"{fact}\""
        return None

    def handle_forget_command(self, text: str) -> Optional[str]:
        patterns = [
            r"dimentica tutto",
            r"cancella(?:\s+la)?\s+memoria",
            r"resetta(?:\s+la)?\s+memoria",
            r"elimina(?:\s+la)?\s+memoria",
        ]
        for pattern in patterns:
            if re.search(pattern, text.lower()):
                # admin.json è permanente — mai cancellare
                self._profile = dict(EMPTY_PROFILE)
                # Se admin.json esiste, preserva bond_proposed = True
                try:
                    from modules.admin_manager import admin_exists
                    if admin_exists():
                        self._profile["bond_proposed"] = True
                except Exception:
                    pass
                self._bond_proposed = bool(self._profile.get("bond_proposed", False))
                self._save_profile()
                for f in self._conv_dir.glob("*.json"):
                    f.unlink()
                # patterns.json va cancellato: è dati comportamentali legati all'utente
                _pat_f = Config.DATA_DIR / "patterns.json"
                if _pat_f.exists():
                    _pat_f.unlink()
                console.print("[yellow]🗑 Memoria cancellata[/yellow]")
                return "Ho cancellato tutta la mia memoria. Ripartiamo da zero."
        return None

    def reload_profile(self) -> None:
        """Ricarica il profilo da disco dopo un reset esterno (es. Tabula Rasa)."""
        self._profile = self._load_profile()

    @property
    def profile(self) -> dict:
        return self._profile

    def get_full_history(self) -> list[dict]:
        return self._current_conv
