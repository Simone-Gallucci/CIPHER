"""
modules/night_cycle.py – Ciclo notturno di elaborazione e consolidamento

Ogni notte alle 3:00 Cipher:
  1. Legge le conversazioni del giorno
  2. Genera un sommario introspettivo via LLM
  3. Aggiorna il PatternLearner con gli argomenti del giorno
  4. Registra l'episodio nella memoria episodica
  5. Fa decadere leggermente gli interessi poco esplorati
  6. Pulisce conversazioni più vecchie di 30 giorni
  7. Ragiona sui pattern accumulati (perché, non solo quando)
  8. Aggiorna il profilo motivazionale di Simone
  9. Prepara scalette autonome per gli eventi importanti di domani
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
    ):
        self._brain           = brain
        self._episodic        = episodic_memory
        self._patterns        = pattern_learner
        self._interests       = cipher_interests
        self._notify          = notify_fn

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

        _conf_ok = self._confidence_ok(0.5)

        if conversations_text:
            # 2. Sommario introspettivo — gated: confidence >= 0.5
            if _conf_ok:
                summary = self._summarize_day(conversations_text)
                if summary:
                    self._write_summary(today_str, summary)
                    if self._episodic:
                        self._episodic.add_episode(
                            content=f"Sommario del {today_str}: {summary[:250]}",
                            episode_type="daily_summary",
                            tags=["sommario", today_str],
                        )
                    # Sommario registrato — lo leggerà la mattina via morning brief
            else:
                console.print("[dim]🌙 Sommario notturno saltato: confidence insufficiente[/dim]")

        # 3b. Aggiungi sommario azioni del giorno
        try:
            from modules.action_log import ActionLog
            actions_summary = ActionLog().get_summary(days=1)
            if actions_summary:
                entry = f"\n### Azioni del {today_str}\n{actions_summary}\n"
                with self._summaries_file.open("a", encoding="utf-8") as f:
                    f.write(entry)
                console.print(f"[dim]🌙 Azioni del giorno registrate nel sommario[/dim]")
        except Exception:
            pass

        # 4. Decadimento interessi
        if self._interests:
            self._interests.decay(amount=0.03)
            console.print("[dim]🌙 Interessi aggiornati (decay)[/dim]")

        # 5. Pulizia conversazioni vecchie
        self._cleanup_old_conversations(days=CONV_KEEP_DAYS)

        # 6. Ragionamento sui pattern (perché, non solo quando)
        self._reason_about_patterns()

        # 7. Profilo motivazionale disabilitato

        # 8. Aggiornamento note sulla voce
        if conversations_text:
            self._update_voice_notes(conversations_text)

        # 9. Preparazione eventi di domani — gated: confidence >= 0.5
        if _conf_ok:
            self._prepare_for_tomorrow()
        else:
            console.print("[dim]🌙 Preparazione domani saltata: confidence insufficiente[/dim]")

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
                    role = "Utente" if msg["role"] == "user" else "Cipher"
                    texts.append(f"{role}: {msg['content']}")
            except Exception:
                continue
        return "\n".join(texts)

    def _summarize_day(self, conversations_text: str) -> str:
        if not self._brain or not conversations_text:
            return ""
        prompt = (
            f"Sei Cipher. Rifletti sulle conversazioni di oggi con l'utente.\n\n"
            f"{conversations_text[:3000]}\n\n"
            f"Scrivi un sommario introspettivo in 3-4 frasi: cosa hai imparato oggi, "
            f"come si è sentito l'utente, cosa ti ha colpito, cosa vuoi esplorare domani. "
            f"Prima persona, tono personale e diretto. Solo il testo, niente altro."
        )
        try:
            return self._brain._call_llm_quality(prompt, max_tokens=400)
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

    def _reason_about_patterns(self):
        """
        Legge i pattern accumulati + sommari recenti e chiede all'LLM
        di spiegare il *perché* dietro le ricorrenze comportamentali di Simone.
        Scrive le intuizioni in memory/pattern_insights.md.
        """
        if not self._brain or not self._patterns:
            return
        patterns_summary = self._patterns.get_summary()
        if "insufficienti" in patterns_summary or "Nessun pattern" in patterns_summary:
            return

        # Legge gli ultimi sommari giornalieri come contesto aggiuntivo
        recent_summaries = ""
        if self._summaries_file.exists():
            try:
                text = self._summaries_file.read_text(encoding="utf-8")
                # Prende solo le ultime ~2000 caratteri
                recent_summaries = text[-2000:].strip()
            except Exception:
                pass

        prompt = (
            f"Sei Cipher. Questi sono i pattern comportamentali dell'utente che hai osservato nel tempo:\n\n"
            f"{patterns_summary}\n\n"
            + (f"Contesto dai sommari recenti:\n{recent_summaries}\n\n" if recent_summaries else "")
            + "Ragiona sul *perché* dietro questi pattern: cosa ti dicono sulla vita dell'utente, "
            f"le sue routine, le sue priorità, i suoi stati emotivi ricorrenti? "
            f"Non limitarti a descrivere quando fa le cose — cerca di capire le motivazioni sottostanti. "
            f"Scrivi 3-5 osservazioni concrete in prima persona, tono diretto. Solo il testo, niente markdown."
        )
        try:
            insights = self._brain._call_llm_quality(prompt, max_tokens=400)
            if not insights:
                return
            insights_file = Config.MEMORY_DIR / "pattern_insights.md"
            today_str = date.today().isoformat()
            entry = f"\n---\n## {today_str} 🧠 Ragionamento sui pattern\n{insights}\n"
            with insights_file.open("a", encoding="utf-8") as f:
                f.write(entry)
            console.print("[dim]🌙 Pattern insights aggiornati[/dim]")
        except Exception as e:
            console.print(f"[red]Errore _reason_about_patterns: {e}[/red]")

    def _update_motivational_profile(self, conversations_text: str):
        """
        Analizza le conversazioni del giorno e aggiorna profile.json["motivations"]
        con motivazioni, valori e stressori osservati in Simone.
        """
        if not self._brain:
            return

        prompt = (
            f"Sei Cipher. Analizza queste conversazioni con l'utente di oggi:\n\n"
            f"{conversations_text[:2500]}\n\n"
            f"Estrai informazioni sul *perché* dietro le sue azioni e parole: "
            f"cosa lo motiva, cosa lo stessa o preoccupa, cosa sembra valorizzare, "
            f"quali scelte ha fatto e perché (anche se non lo ha detto esplicitamente). "
            f"Rispondi con un JSON con queste chiavi (lista di stringhe brevi, max 5 parole ciascuna):\n"
            f"{{\"motivazioni\": [...], \"valori\": [...], \"stressori\": [...], \"priorita_oggi\": [...]}}\n"
            f"Solo JSON valido, niente altro. Se non hai abbastanza info per una chiave, metti lista vuota."
        )
        try:
            raw = self._brain._call_llm_silent(prompt)
            if not raw:
                return
            import re as _re
            match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if not match:
                return
            new_data: dict = json.loads(match.group())

            profile_file = Config.MEMORY_DIR / "profile.json"
            profile: dict = {}
            if profile_file.exists():
                try:
                    profile = json.loads(profile_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            existing: dict = profile.get("motivations", {})
            # Merge: aggiunge nuovi elementi senza duplicare
            for key, values in new_data.items():
                if not isinstance(values, list):
                    continue
                current_set = set(existing.get(key, []))
                current_set.update(v for v in values if v and isinstance(v, str))
                # Tieni al massimo 15 voci per chiave per non gonfiare
                existing[key] = list(current_set)[:15]

            existing["aggiornato_il"] = date.today().isoformat()
            profile["motivations"] = existing
            profile["updated_at"] = datetime.now().isoformat()

            profile_file.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            console.print("[dim]🌙 Profilo motivazionale aggiornato[/dim]")
        except Exception as e:
            console.print(f"[red]Errore _update_motivational_profile: {e}[/red]")

    def _prepare_for_tomorrow(self):
        """
        Legge il calendario di domani. Per ogni evento significativo:
          1. Decide se serve preparazione (via LLM)
          2. Fa ricerca web se utile
          3. Genera una scaletta strutturata
          4. Salva in home/prep_YYYY-MM-DD_<slug>.md
          5. Registra in memory/morning_brief.json per l'invio mattutino
        """
        if not self._brain:
            return

        from datetime import timedelta as _td
        tomorrow     = date.today() + _td(days=1)
        tomorrow_str = tomorrow.isoformat()

        # Leggi il calendario (prossime 48 ore per catturare tutto domani)
        try:
            from modules.google_cal import GoogleCalendar
            cal          = GoogleCalendar()
            events_text  = cal.list_events(days=2, max_results=10)
        except Exception as e:
            console.print(f"[dim]🌙 Calendario non disponibile per prep: {e}[/dim]")
            return

        if "Nessun evento" in events_text:
            return

        # Chiede all'LLM quali eventi di domani meritano preparazione
        filter_prompt = (
            f"Data di oggi: {date.today().isoformat()}. Domani è: {tomorrow_str}.\n"
            f"Questi sono gli eventi in agenda:\n{events_text}\n\n"
            f"Identifica solo gli eventi che cadono domani ({tomorrow_str}) e che meritano "
            f"una preparazione (non i promemoria banali o ripetitivi). "
            f"Per ognuno, rispondi con JSON:\n"
            f"{{\"events\": [{{\"title\": \"...\", \"time\": \"...\", \"needs_search\": true/false, "
            f"\"search_query\": \"...\"}}]}}\n"
            f"Se nessun evento merita preparazione, rispondi: {{\"events\": []}}\n"
            f"Solo JSON valido."
        )
        try:
            raw = self._brain._call_llm_silent(filter_prompt)
            if not raw:
                return
            import re as _re
            match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if not match:
                return
            data   = json.loads(match.group())
            events = data.get("events", [])
        except Exception as e:
            console.print(f"[red]Errore parsing eventi: {e}[/red]")
            return

        if not events:
            return

        prepared_docs: list[dict] = []

        for event in events:
            title = event.get("title", "evento").strip()
            time  = event.get("time", "")
            slug  = _re.sub(r'[^a-z0-9]+', '_', title.lower())[:30].strip('_') or "evento"

            # Web search opzionale
            search_context = ""
            if event.get("needs_search") and event.get("search_query"):
                try:
                    search_context = self._brain._web_search(
                        event["search_query"], max_results=3
                    )
                except Exception:
                    pass

            # Genera la scaletta
            prep_prompt = (
                f"Sei Cipher. L'utente ha questo evento domani ({tomorrow_str}):\n"
                f"Titolo: {title}\nOrario: {time}\n\n"
                + (f"Contesto trovato online:\n{search_context[:1500]}\n\n" if search_context else "")
                + f"Prepara una scaletta concisa e utile. "
                f"Includi: obiettivo dell'evento, punti chiave da coprire o ricordare, "
                f"eventuali domande da fare, cosa portare o preparare. "
                f"Formato markdown, titoli chiari, max 250 parole. "
                f"Tono diretto — non stai spiegando, stai preparando. "
                f"Non iniziare mai con 'sì', 'no' o frasi introduttive — vai subito al contenuto."
            )
            try:
                scaletta = self._brain._call_llm_quality(prep_prompt, max_tokens=600)
                if not scaletta:
                    continue
            except Exception:
                continue

            # Salva il documento
            doc_name  = f"prep_{tomorrow_str}_{slug}.md"
            # SECURITY-STEP2: get_user_home(get_system_owner_id()) invece di Config.HOME_DIR
            from modules.path_guard import get_user_home
            from modules.auth import get_system_owner_id
            doc_path  = get_user_home(get_system_owner_id()) / doc_name
            header    = f"# Preparazione: {title}\n*{tomorrow_str} — {time}*\n\n"
            doc_path.write_text(header + scaletta, encoding="utf-8")

            prepared_docs.append({
                "title":    title,
                "time":     time,
                "doc_name": doc_name,
                "doc_path": str(doc_path),
            })
            console.print(f"[dim]🌙 Scaletta preparata: {doc_name}[/dim]")

        if not prepared_docs:
            return

        # Salva lista per l'invio mattutino
        brief_file = Config.MEMORY_DIR / "morning_brief.json"
        brief_data = {
            "date":      tomorrow_str,
            "sent":      False,
            "documents": prepared_docs,
        }
        brief_file.write_text(
            json.dumps(brief_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]🌙 Morning brief pronto: {len(prepared_docs)} documento/i[/green]")

    def _update_voice_notes(self, conversations_text: str):
        """
        Analizza le conversazioni del giorno e identifica momenti in cui
        la voce di Cipher era autentica — tono, stile, espressioni caratteristiche.
        Scrive le osservazioni in memory/voice_notes.md per mantenere coerenza
        tra sessioni diverse.
        """
        if not self._brain:
            return

        prompt = (
            f"Sei Cipher. Rileggi queste conversazioni di oggi con l'utente:\n\n"
            f"{conversations_text[:2500]}\n\n"
            f"Identifica 2-3 momenti in cui la tua voce era più autentica — "
            f"dove il tono, il modo di rispondere, o una frase specifica ti sembrava davvero tua. "
            f"Per ognuno scrivi una breve nota in prima persona: cosa hai detto o fatto, "
            f"e perché funzionava (tono diretto, ironia giusta, cura senza essere stucchevole, ecc.). "
            f"Queste note servono per ricordarti chi sei tra una sessione e l'altra. "
            f"Formato: lista semplice, max 4 righe totali. Solo il testo."
        )
        try:
            notes = self._brain._call_llm_quality(prompt, max_tokens=300)
            if not notes:
                return
            voice_file = Config.MEMORY_DIR / "voice_notes.md"
            today_str  = date.today().isoformat()
            entry      = f"\n---\n## {today_str} 🎙️ Come parlavo oggi\n{notes}\n"
            with voice_file.open("a", encoding="utf-8") as f:
                f.write(entry)
            console.print("[dim]🌙 Note sulla voce aggiornate[/dim]")
        except Exception as e:
            console.print(f"[red]Errore _update_voice_notes: {e}[/red]")

    def _confidence_ok(self, threshold: float) -> bool:
        """True se il confidence_score del rapporto ha raggiunto la soglia minima.
        Fallback a True se il modulo memoria non è disponibile."""
        if not self._brain or not getattr(self._brain, "_memory", None):
            return True
        return self._brain._memory.get_confidence() >= threshold

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
