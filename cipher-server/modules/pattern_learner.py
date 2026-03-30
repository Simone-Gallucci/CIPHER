"""
modules/pattern_learner.py – Apprendimento pattern comportamentali di Simone

Analizza le conversazioni per trovare ricorrenze: a che ora interagisce,
quali argomenti tratta certi giorni, comportamenti abituali.
Cipher usa questi pattern per anticipare i bisogni di Simone.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, date
from typing import Optional

from rich.console import Console

from config import Config

console = Console()

DAYS_IT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


class PatternLearner:
    def __init__(self, brain=None):
        self._brain = brain
        self._file  = Config.MEMORY_DIR / "patterns.json"
        self._data  = self._load()

    # ── Persistenza ───────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self):
        self._file.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Registrazione ─────────────────────────────────────────────────

    def record_interaction(self, hour: int, weekday: int, topic: str):
        """Registra un'interazione con ora, giorno della settimana e argomento."""
        key = f"{weekday}_{hour}"
        if key not in self._data:
            self._data[key] = {"count": 0, "topics": {}}
        self._data[key]["count"] += 1
        topics = self._data[key]["topics"]
        topics[topic] = topics.get(topic, 0) + 1
        self._save()

    def analyze_today(self, conversations_text: str):
        """
        Analizza le conversazioni del giorno tramite LLM per estrarre argomenti
        e aggiornare i pattern. Da chiamare nel ciclo notturno.
        """
        if not self._brain or not conversations_text.strip():
            return
        try:
            result = self._brain._call_llm_silent(
                f"Analizza questi scambi conversazionali:\n{conversations_text[:2500]}\n\n"
                f"Identifica al massimo 3 argomenti principali discussi. "
                f"Rispondi con una lista JSON di stringhe brevi (max 5 parole ciascuna), "
                f"esempio: [\"lavoro\", \"musica italiana\", \"piano vacanze\"]. "
                f"Solo JSON, niente altro."
            )
            match = re.search(r'\[.*?\]', result, re.DOTALL)
            if match:
                topics: list = json.loads(match.group())
                now = datetime.now()
                for topic in topics[:3]:
                    self.record_interaction(now.hour, now.weekday(), str(topic))
                console.print(f"[dim]📊 Pattern aggiornati: {topics}[/dim]")
        except Exception as e:
            console.print(f"[red]PatternLearner errore analyze_today: {e}[/red]")

    # ── Previsioni ────────────────────────────────────────────────────

    def get_predictions(self, lookahead_hours: int = 3) -> list[dict]:
        """
        Ritorna previsioni di argomenti probabili nelle prossime N ore,
        basandosi sui pattern storici.
        """
        now     = datetime.now()
        weekday = now.weekday()
        hour    = now.hour
        predictions = []

        for h_offset in range(lookahead_hours):
            h    = (hour + h_offset) % 24
            key  = f"{weekday}_{h}"
            data = self._data.get(key, {})
            if data.get("count", 0) < 3:
                continue
            topics = data.get("topics", {})
            if not topics:
                continue
            top_topic  = max(topics, key=topics.get)
            freq       = data["count"]
            confidence = min(freq / 10.0, 1.0)
            predictions.append({
                "hour":       h,
                "topic":      top_topic,
                "frequency":  freq,
                "confidence": round(confidence, 2),
            })

        return predictions

    def get_summary(self) -> str:
        """Ritorna un sommario leggibile dei pattern appresi."""
        if not self._data:
            return "Nessun pattern appreso ancora."

        by_day: dict[int, list] = defaultdict(list)
        for key, data in self._data.items():
            if data.get("count", 0) < 3:
                continue
            parts = key.split("_")
            if len(parts) != 2:
                continue
            try:
                day, hour = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            topics = data.get("topics", {})
            top    = max(topics, key=topics.get) if topics else "n/d"
            by_day[day].append(f"{hour:02d}:00 ({top}, {data['count']}x)")

        if not by_day:
            return "Pattern ancora insufficienti (servono ≥ 3 occorrenze per slot)."

        lines = ["Pattern comportamentali di Simone:"]
        for day in sorted(by_day.keys()):
            lines.append(f"  {DAYS_IT[day]}: {', '.join(sorted(by_day[day]))}")
        return "\n".join(lines)
