"""
modules/memory.py – Memoria persistente di Cipher
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from config import Config

console = Console()

PROFILE_FILE     = Config.MEMORY_DIR / "profile.json"
CONV_DIR         = Config.MEMORY_DIR / "conversations"
LEARNING_DIR     = Config.BASE_DIR / "apprendimento"
BEHAVIOR_DIR     = Config.BASE_DIR / "comportamento"

EMPTY_PROFILE = {
    "personal": {},
    "preferences": {},
    "facts": [],
    "updated_at": None,
}


class Memory:
    def __init__(self) -> None:
        CONV_DIR.mkdir(parents=True, exist_ok=True)
        LEARNING_DIR.mkdir(parents=True, exist_ok=True)
        BEHAVIOR_DIR.mkdir(parents=True, exist_ok=True)
        self._profile       = self._load_profile()
        self._current_conv  = []
        self._session_file  = self._new_session_file()
        self._last_extract  = 0
        console.print(
            f"[green]✓ Memoria pronta[/green] "
            f"[dim]({self._count_conversations()} conversazioni salvate)[/dim]"
        )

    def _load_profile(self) -> dict:
        if PROFILE_FILE.exists():
            try:
                return json.loads(PROFILE_FILE.read_text())
            except Exception:
                pass
        return dict(EMPTY_PROFILE)

    def _save_profile(self) -> None:
        self._profile["updated_at"] = datetime.now().isoformat()
        PROFILE_FILE.write_text(json.dumps(self._profile, ensure_ascii=False, indent=2))

    def update_profile(self, key: str, value: str, category: str = "personal") -> None:
        if category not in self._profile:
            self._profile[category] = {}
        if category in ("personal", "preferences"):
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
        return CONV_DIR / f"{ts}.json"

    def _save_conversation(self) -> None:
        if not self._current_conv:
            return
        data = {"timestamp": datetime.now().isoformat(), "messages": self._current_conv}
        self._session_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def add_message(self, role: str, content: str) -> None:
        self._current_conv.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        self._save_conversation()

    def _count_conversations(self) -> int:
        return len(list(CONV_DIR.glob("*.json")))

    def _load_behavior_files(self) -> str:
        """Legge tutti i file nella cartella comportamento e li restituisce come direttive."""
        files = sorted(BEHAVIOR_DIR.glob("*"))
        if not files:
            return ""

        sections = []
        for fpath in files:
            if not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8").strip()
                if content:
                    sections.append(f"[{fpath.name}]\n{content}")
            except Exception:
                continue

        if not sections:
            return ""

        return "COMPORTAMENTO E STILE:\n" + "\n\n".join(sections)

    def _load_learning_files(self) -> str:
        """Legge tutti i file nella cartella apprendimento e li restituisce come contesto."""
        files = sorted(LEARNING_DIR.glob("*"))
        if not files:
            return ""

        sections = []
        for fpath in files:
            if not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8").strip()
                if content:
                    sections.append(f"[{fpath.name}]\n{content}")
            except Exception:
                continue

        if not sections:
            return ""

        return "CONOSCENZA APPRESA:\n" + "\n\n".join(sections)

    def build_context(self) -> str:
        parts = []

        # 1. Comportamento (massima priorità — influenza come risponde)
        behavior = self._load_behavior_files()
        if behavior:
            parts.append(behavior)

        # 2. Profilo utente
        profile_lines = []
        for k, v in self._profile.get("personal", {}).items():
            profile_lines.append(f"  - {k}: {v}")
        for k, v in self._profile.get("preferences", {}).items():
            profile_lines.append(f"  - preferenza {k}: {v}")
        for f in self._profile.get("facts", []):
            profile_lines.append(f"  - {f}")
        if profile_lines:
            parts.append("PROFILO UTENTE:\n" + "\n".join(profile_lines))

        # 3. Conoscenza appresa dai file
        learning = self._load_learning_files()
        if learning:
            parts.append(learning)

        # 4. Conversazioni passate — ultimi 50 messaggi completi
        past_files = [f for f in sorted(CONV_DIR.glob("*.json")) if f != self._session_file]
        if past_files:
            all_messages = []
            for fpath in reversed(past_files):
                try:
                    data = json.loads(fpath.read_text())
                    msgs = data.get("messages", [])
                    ts   = data.get("timestamp", "")[:10]
                    for m in reversed(msgs):
                        all_messages.append({
                            "role": m["role"],
                            "content": m["content"][:500],
                            "date": ts,
                        })
                        if len(all_messages) >= 50:
                            break
                except Exception:
                    continue
                if len(all_messages) >= 50:
                    break

            if all_messages:
                all_messages.reverse()
                lines = []
                for m in all_messages:
                    icon = "👤" if m["role"] == "user" else "🤖"
                    lines.append(f"  [{m['date']}] {icon} {m['content']}")
                parts.append("CONVERSAZIONI PASSATE:\n" + "\n".join(lines))

        if not parts:
            return ""
        return "\n\n━━━ MEMORIA ━━━\n" + "\n\n".join(parts) + "\n━━━━━━━━━━━━━━"

    def extract_from_message(self, text: str, call_llm_fn) -> None:
        self._last_extract += 1
        if self._last_extract % 3 != 0 or len(text.strip()) < 10:
            return
        prompt = (
            f'Analizza questo messaggio e decidi se contiene informazioni personali '
            f'importanti da ricordare (nome, età, lavoro, città, preferenze).\n\n'
            f'Messaggio: "{text}"\n\n'
            f'Se contiene info, rispondi SOLO con JSON:\n'
            f'{{"save": [{{"category": "personal|preferences|facts", "key": "campo", "value": "valore"}}]}}\n'
            f'Altrimenti: {{"save": []}}\nNessuna spiegazione.'
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
        except Exception:
            pass

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
                self._profile = dict(EMPTY_PROFILE)
                self._save_profile()
                for f in CONV_DIR.glob("*.json"):
                    f.unlink()
                console.print("[yellow]🗑 Memoria cancellata[/yellow]")
                return "Ho cancellato tutta la mia memoria. Ripartiamo da zero."
        return None

    @property
    def profile(self) -> dict:
        return self._profile

    def get_full_history(self) -> list[dict]:
        return self._current_conv
