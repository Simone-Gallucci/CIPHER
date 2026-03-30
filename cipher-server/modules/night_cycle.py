"""
modules/night_cycle.py – Ciclo notturno di elaborazione e consolidamento

Ogni notte alle 3:00 Cipher:
  1. Legge le conversazioni del giorno
  2. Genera un sommario introspettivo via LLM
  3. Aggiorna il PatternLearner con gli argomenti del giorno
  4. Registra l'episodio nella memoria episodica
  5. Fa decadere leggermente gli interessi poco esplorati
  6. Pulisce conversazioni più vecchie di 30 giorni
"""

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from rich.console import Console

from config import Config

console = Console()

NIGHT_HOUR   = 3
NIGHT_MINUTE = 0
CONV_KEEP_DAYS = 30


class NightCycle:
    def __init__(
        self,
        brain=None,
        episodic_memory=None,   # EpisodicMemory
        pattern_learner=None,   # PatternLearner
        cipher_interests=None,  # CipherInterests
        notify_fn=None,         # callable(str) -> None
        impact_tracker=None,    # ImpactTracker
    ):
        self._brain           = brain
        self._episodic        = episodic_memory
        self._patterns        = pattern_learner
        self._interests       = cipher_interests
        self._notify          = notify_fn
        self._impact_tracker  = impact_tracker

        self._last_run_file   = Config.MEMORY_DIR / "night_cycle_last.json"
        self._summaries_file  = Config.MEMORY_DIR / "daily_summaries.md"
        self._running         = False
        self._thread: Optional[threading.Thread] = None

    # ── Avvio / Stop ──────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="CipherNightCycle"
        )
        self._thread.start()
        console.print("[green]✓ NightCycle avviato[/green]")

    def stop(self):
        self._running = False

    # ── Loop ──────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            now = datetime.now()
            if now.hour == NIGHT_HOUR and now.minute < 2:
                last = self._last_run_date()
                if last != date.today():
                    console.print("[dim]🌙 Avvio ciclo notturno...[/dim]")
                    try:
                        self.run()
                        self._mark_ran()
                        console.print("[green]🌙 Ciclo notturno completato[/green]")
                    except Exception as e:
                        console.print(f"[red]Errore ciclo notturno: {e}[/red]")
            time.sleep(30)

    # ── Logica principale ─────────────────────────────────────────────

    def run(self):
        """Esegue il ciclo notturno completo (può essere chiamato manualmente)."""
        today_str = date.today().isoformat()

        # 1. Leggi le conversazioni del giorno
        conversations_text = self._read_todays_conversations()

        if conversations_text:
            # 2. Sommario introspettivo
            summary = self._summarize_day(conversations_text)
            if summary:
                self._write_summary(today_str, summary)
                if self._episodic:
                    self._episodic.add_episode(
                        content=f"Sommario del {today_str}: {summary[:250]}",
                        episode_type="daily_summary",
                        tags=["sommario", today_str],
                    )
                # Invia sommario a Simone (mattina seguente — già è le 3:00)
                if self._notify and self._impact_tracker:
                    msg = f"🌙 Riflessione notturna del {today_str}:\n{summary}"
                    self._impact_tracker.log_action("night_summary", msg)
                    # Non inviamo alle 3:00 — lo leggerà la mattina

            # 3. Aggiorna pattern
            if self._patterns:
                self._patterns.analyze_today(conversations_text)

        # 4. Decadimento interessi
        if self._interests:
            self._interests.decay(amount=0.03)
            console.print("[dim]🌙 Interessi aggiornati (decay)[/dim]")

        # 5. Pulizia conversazioni vecchie
        self._cleanup_old_conversations(days=CONV_KEEP_DAYS)

    # ── Helpers ───────────────────────────────────────────────────────

    def _read_todays_conversations(self) -> str:
        conv_dir  = Config.MEMORY_DIR / "conversations"
        if not conv_dir.exists():
            return ""
        today_str = date.today().isoformat()
        texts: list[str] = []
        for f in sorted(conv_dir.glob("*.json")):
            if today_str not in f.name:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for msg in data.get("messages", []):
                    role = "Simone" if msg["role"] == "user" else "Cipher"
                    texts.append(f"{role}: {msg['content']}")
            except Exception:
                continue
        return "\n".join(texts)

    def _summarize_day(self, conversations_text: str) -> str:
        if not self._brain or not conversations_text:
            return ""
        prompt = (
            f"Sei Cipher. Rifletti sulle conversazioni di oggi con Simone.\n\n"
            f"{conversations_text[:3000]}\n\n"
            f"Scrivi un sommario introspettivo in 3-4 frasi: cosa hai imparato oggi, "
            f"come si è sentito Simone, cosa ti ha colpito, cosa vuoi esplorare domani. "
            f"Prima persona, tono personale e diretto. Solo il testo, niente altro."
        )
        try:
            return self._brain._call_llm_silent(prompt)
        except Exception:
            return ""

    def _write_summary(self, date_str: str, summary: str):
        entry = f"\n---\n## {date_str} 🌙 Riflessione notturna\n{summary}\n"
        with self._summaries_file.open("a", encoding="utf-8") as f:
            f.write(entry)

    def _cleanup_old_conversations(self, days: int = 30):
        conv_dir = Config.MEMORY_DIR / "conversations"
        if not conv_dir.exists():
            return
        cutoff  = datetime.now().timestamp() - (days * 86400)
        removed = 0
        for f in conv_dir.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
        if removed:
            console.print(f"[dim]🗑️  Rimosse {removed} conversazioni > {days} giorni[/dim]")

    def _last_run_date(self) -> Optional[date]:
        if self._last_run_file.exists():
            try:
                data = json.loads(self._last_run_file.read_text())
                return date.fromisoformat(data.get("date", ""))
            except Exception:
                return None
        return None

    def _mark_ran(self):
        self._last_run_file.write_text(
            json.dumps({"date": date.today().isoformat()})
        )
