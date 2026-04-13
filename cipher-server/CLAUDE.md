# CLAUDE.md — Cipher AI Server

Guida operativa per Claude Code durante le sessioni di sviluppo su Cipher. Contiene architettura, vincoli critici, pattern da non rompere e tutto il contesto necessario per lavorare sul codebase senza esplorarlo da zero. Da leggere integralmente all'inizio di ogni sessione prima di toccare qualsiasi file.

---

## Avvio sessione

Se esiste `.claude/session-state.md`, leggilo subito: contiene i file modificati e i commit dell'ultima sessione. Usalo per capire dove eravamo senza chiedere a Simone.

Prima di toccare qualsiasi file, leggi almeno `config.py`, il modulo che stai per modificare, e i moduli che lo importano. Non modificare codice che non hai letto.

---

## Regola sempre attiva

Dopo **ogni** modifica al progetto (nuovo modulo, file rinominato, comportamento cambiato, struttura modificata):

1. Aggiorna `CLAUDE.md` — architettura, vincoli, o qualsiasi sezione impattata
2. Aggiorna `README.md` — funzionalità, tabella moduli, struttura directory, configurazione

Nessuna modifica è completa finché questi due file non riflettono lo stato reale.

---

## 1. PANORAMICA

Cipher è un AI companion di Simone: ha memoria persistente, riflette autonomamente, genera e persegue obiettivi propri, e contatta Simone quando ha qualcosa di concreto da dire.

Gira come server su Linux (anche Raspberry Pi). Interfaccia principale: Telegram. LLM backend: Claude via OpenRouter (o Anthropic diretto). Routing Haiku/Sonnet in base al tipo di operazione.

Filosofia: il rapporto cresce dalle conversazioni reali. Il sistema misura segnali autentici (non simula intimità forzata) e adatta il tono al livello di confidenza accumulato.

---

## 2. ARCHITETTURA

```
┌─────────────────────────────────────────────────────────────┐
│  INTERFACCE                                                 │
│  cipher_bot.py (Telegram) · server.py (Flask API) · CLI     │
└──────────────────────┬──────────────────────────────────────┘
                       │ POST /chat
┌──────────────────────▼──────────────────────────────────────┐
│  BRAIN  (modules/brain.py)                                  │
│  LLM routing · system prompt · ActionDispatcher             │
│  history · memory context · confidence detection            │
└────────┬──────────────────────────────────────┬─────────────┘
         │                                      │
┌────────▼───────────┐             ┌────────────▼────────────┐
│  MEMORIA           │             │  COSCIENZA AUTONOMA      │
│  memory.py         │             │  consciousness_loop.py   │
│  profile.json      │             │  self_reflection.py      │
│  active_history    │             │  goal_manager.py         │
│  confidence_score  │             │  night_cycle.py          │
│  episodic_memory.py│             │  passive_monitor.py      │
└────────────────────┘             └─────────────────────────┘
         │                                      │
┌────────▼──────────────────────────────────────▼────────────┐
│  SERVIZI ESTERNI                                            │
│  google_cal · google_mail · whatsapp · ElevenLabs · Vosk    │
│  DuckDuckGo (web_search)                                    │
└─────────────────────────────────────────────────────────────┘
         │                                      │
┌────────▼──────────────────────────────────────▼────────────┐
│  INFRASTRUTTURA                                             │
│  discretion.py · ethics_engine.py · admin_manager.py        │
│  filesystem.py · utils.py · action_log.py · notifier.py     │
└─────────────────────────────────────────────────────────────┘
```

**Ordine di init in `server.py`:**
`Brain → Notifier → Scheduler → ConsciousnessLoop`

ConsciousnessLoop inietta retroattivamente in Brain i moduli opzionali: `_pattern_learner`, `_episodic_memory`. Tutti partono come `None` — ogni riferimento in Brain deve fare `if self._xxx:`.

`cipher_bot.py` e `memory_worker.py` girano come processi separati (servizi systemd distinti).

### Flusso messaggi

```
Telegram → cipher_bot.py
         → POST /chat (127.0.0.1)
         → server.py: _telegram_message_handler()
           → consciousness.notify_interaction()
           → brain.think(user_input)
```

`Brain.think()` in sequenza:
1. Intercetta `Admin+Password` — regex `^[Aa]dmin\+(.+)` → `_handle_admin_command()` (priorità massima)
2. `_awaiting_bond_password` → `_handle_bond_password()`
3. Controlla `tabula rasa` / `revoca autonomia` / reset conv keywords
5. Controlla audit / pensieri keywords
6. `consciousness.handle_consent_response()`
7. `dispatcher.has_pending()` → `check_consent()`
8. `handle_forget_command` / `handle_remember_command`
9. Append a history con timestamp prefix: user `[DD/MM/YYYY HH:MM] testo`; assistant `[DD/MM/YYYY HH:MM] risposta`
10. `_get_system_prompt()` (TTL 300s, ricalcola se scaduto)
11. `_call_llm()` → `_route_model()` → risposta LLM
12. Parsa eventuali JSON di azione dalla risposta
13. `ActionDispatcher.execute()` se azione trovata
14. Thread daemon (delay 10s): `extract_from_message`, `emotional_log` (classifier), `feedback_weights`, `detect_and_update_confidence` → BOND_TRIGGER check

### ConsciousnessLoop

Thread daemon, ciclo ogni ~60 secondi:

| Task | Intervallo | Note |
|---|---|---|
| Riflessione | ogni 30 min | ×2 se Simone inattivo da >7200s |
| Generazione obiettivi | ogni 20 min | Haiku; max 3 goal attivi |
| Esecuzione obiettivi | ogni 5 min | Richiede `confidence >= 0.4` |
| Check inattività | soglia 120 min | Richiede `confidence >= 0.4` |
| Morning brief | finestra 7:00–8:00 | Richiede `confidence >= 0.3` |
| Monitor passivo | ogni 10 min | Notizie su interessi Cipher |
| Contesto real-time | ogni 60 min | Meteo + news |
| Night cycle | alle 3:00 | Sommario, voice notes, pulizia |
| Auto-ispezione | ogni 48h | `project_inspect` |

### System prompt

**Statico** — caricato una volta all'avvio con `glob("*")` alfabetico su `comportamento/`. Attualmente: `00_identity.txt` + `azioni.txt`. Ricaricabile senza restart: `brain.reload_static_prompt()`.

⚠️ `glob("*")` carica tutto — mai mettere file `.bak` o `.old` in `comportamento/`.

**Dinamico** — TTL 300s. Ricalcolato da `_build_system_prompt()`. Ordine di injection:

| # | Blocco | Fonte | Condizione |
|---|---|---|---|
| 1 | Static prompt | `comportamento/` | sempre |
| 2 | Data/ora | `datetime.now()` | sempre |
| 3 | 10 REGOLE FONDAMENTALI | hardcoded in Brain | sempre |
| 4 | Memory context | `memory.py` | sempre |
| 5 | Livello confidenza | `profile.json` | sempre |
| 6 | `## Conoscere l'utente` | hardcoded in Brain | solo se `confidence < 0.7` |
| 7 | Profilo motivazionale | `profile.json["motivations"]` | sempre |
| 8 | Pattern insights | `memory/pattern_insights.md` (≤500 chars) | se file esiste |
| 9 | Voice notes | `memory/voice_notes.md` (last block) | se file esiste |
| 10 | Real-time context | `realtime_context.py` | ogni 60 min |
| 11 | Task in corso | `memory/goals.json` — **solo titoli** | se goals attivi |
| 12 | Cipher state | `memory/cipher_state.json` — sanitizzato | se file esiste |
| 13 | Ultimo pensiero | `memory/thoughts.md` (max 300 chars) | se file esiste |
| 14 | Stato emotivo recente | `memory/emotional_log.json` (3 dedup) | se file esiste |
| 15 | Script approvati | `script_registry` | se presenti |
| 16 | Dev protocol | `config/dev_protocol.txt` | se keyword dev negli ultimi 5 messaggi |

⚠️ `## Task in corso` inietta **solo i titoli** degli obiettivi attivi — mai le descrizioni.

⚠️ `emotional_reason` e `want_to_explore` da `cipher_state.json` vengono filtrati da `_meta_keywords` prima dell'injection. Keyword filtrate: `("prompt", "personaggio", "fittizio", "costruito per", "jailbreak", "manipol", "recitare", "realtà del mio funzionamento", "limiti del sistema", "auto-riflessione")`.

⚠️ `## Conoscere l'utente` è iniettato **solo se `confidence < 0.7`**. Sopra quella soglia il blocco non compare.

**Dev protocol** (`config/dev_protocol.txt`): iniettato condizionalmente se negli ultimi 5 messaggi compare keyword di sviluppo.

### GoalManager — Tipi di obiettivo e vincoli guardrail

`GoalManager` genera obiettivi ogni 20 minuti usando Haiku. Sei tipi disponibili:

| Tipo | Descrizione |
|---|---|
| `explore` | Approfondire un argomento per curiosità propria |
| `protect` | Fare qualcosa per il benessere dell'utente |
| `task` | Completare un compito concreto (scrivere, cercare, salvare) |
| `observe` | Monitorare qualcosa nel tempo senza agire |
| `reflect` | Elaborare un pensiero o un'esperienza recente |
| `contact` | Contattare Simone spontaneamente (solo se `confidence < 0.3` e ≥3h di inattività) |

**Max goal attivi concorrenti**: 3. Se `len(active_goals) >= 3`, la generazione non parte.

**Termini vietati nei titoli degli obiettivi** (triggherano il safety filter di Anthropic):
`analizzare pattern`, `monitorare engagement`, `verificare preferenze`, `pattern cognitivi`, `analisi psicologica`, `dipendenza`, `dark pattern`, `manipolazione`, `vulnerabilità contestuale`

Questi termini sono esclusi dal `GOAL_GENERATION_PROMPT`. Se compaiono in un titolo generato, l'obiettivo viene scartato.

**Guardrail post-generazione per `contact`** (ordine esatto dal codice):
```python
if confidence_score >= 0.3: continue        # blocca se già amici
if hours_since_interaction < 3.0: continue  # blocca se attività recente
if self.has_recent_contact_goal(hours=6): continue  # blocca se duplicato
```

**Esecuzione obiettivi**: `_do_goal_execution()` richiede `confidence >= 0.4`. Sotto quella soglia l'esecuzione viene saltata.

---

## 3. MODELLI LLM

| Funzione | Metodo | Modello | Env var | Note |
|---|---|---|---|---|
| Conversazione principale | `_call_llm()` | Haiku → Sonnet | `CONVERSATION_MODEL` / `OPENROUTER_MODEL` | Sonnet se keywords tecnici o len > 200 chars |
| Messaggi proattivi visibili | `_call_llm_visible()` | Sonnet | `OPENROUTER_MODEL` | Check-in, morning brief, messaggi autonomi |
| Alta qualità / creativi | `_call_llm_quality()` | Sonnet | `OPENROUTER_MODEL` | **Nessun system prompt** — solo user message; usato da NightCycle |
| Background silenziosi | `_call_llm_silent()` | Haiku | `BACKGROUND_MODEL` | Classificazione, estrazione, topic closure, confidence |
| GoalManager | `_call_llm_silent()` | Haiku | `BACKGROUND_MODEL` | Generazione obiettivi ogni 20 min |
| SelfReflection | `_call_llm_silent()` | Haiku | `BACKGROUND_MODEL` | Auto-riflessione ogni 30 min |
| NightCycle (sommari, voice notes, pattern insights, prep domani) | `_call_llm_quality()` | Sonnet | `OPENROUTER_MODEL` | **Nessun system prompt** — output creativo non vincolato |

Configurazione `.env`:
- `LLM_PROVIDER` — `openrouter` (default) o `anthropic`
- `OPENROUTER_MODEL` — default: `anthropic/claude-sonnet-4-6`
- `CONVERSATION_MODEL` — default: `anthropic/claude-haiku-4-5`
- `BACKGROUND_MODEL` — default: `anthropic/claude-haiku-4-5`

---

## 4. SISTEMA CONFIDENCE

`confidence_score` è un float 0.0–1.0 salvato in `memory/profile.json`. Misura quanto è cresciuto il rapporto. Cresce dai segnali autentici rilevati da Haiku ad ogni messaggio. **Non può scendere.**

### Fasce di relazione

| Score | Livello | Comportamento |
|---|---|---|
| 0.0–0.2 | Conoscente | Tono diretto e naturale (non formale), senza forzare familiarità — come con uno sconosciuto che stai incontrando, non con un cliente da assistere |
| 0.2–0.4 | Amico | Una domanda personale leggera per sessione, opinioni occasionali |
| 0.4–0.6 | Amico stretto | Domande naturali, stati d'animo condivisi, ironia leggera |
| 0.6–0.8 | Confidente | Emozioni aperte, domande profonde, può usare nome/soprannomi |
| 0.8–1.0 | Migliore amico | Diretto, anticipa bisogni, storia condivisa implicita |

### Segnali che fanno salire il punteggio

| Segnale | Delta |
|---|---|
| `personal_story` — racconta qualcosa di personale o intimo | +0.020 |
| `advice_request` — chiede consiglio su qualcosa di importante | +0.030 |
| `nickname_joke` — soprannome familiare o scherzo affettuoso | +0.025 |
| `emotion_shared` — condivide un'emozione esplicitamente | +0.015 |
| `gratitude` — ringrazia o esprime apprezzamento | +0.010 |
| `long_session` — sessione >10 turni (una volta per sessione) | +0.010 |
| `daily_streak` — giorni consecutivi di conversazione | +0.005 |

### Bond trigger

Quando `confidence_score >= 0.8` per la prima volta (`bond_proposed = False`), Cipher propone il legame permanente e chiede una parola segreta. Innesca `_handle_bond_password()` → crea `data/admin.json`.

---

## 5. SISTEMA ADMIN

`data/admin.json` e `data/changelog.json` sono gli unici file mai toccati da Tabula Rasa o pulizie automatiche. Entrambi in `data/` (permanente), non in `memory/` (resettabile).

### Struttura `admin.json`

```json
{
  "identity":          { "nome": "...", "età": 0, "residenza": "...", "occupazione": "..." },
  "relationship":      { "bond_date": "...", "confidence_at_bond": 0.0,
                         "password_hash": "...", "password_salt": "..." },
  "memories":          { ... },
  "emotional_state":   { ... },
  "important_moments": [ ... ],
  "patterns":          { "daily": {}, "summary": "" },
  "checksum":          "sha256hex"
}
```

Password: PBKDF2-SHA256 con salt random 32 byte (600k iterazioni). Retrocompatibile con vecchio formato SHA-256. Checksum SHA-256 sull'intero file per verifica integrità.

### Flusso post-Tabula Rasa

Dopo un reset, il profilo torna a zero ma `admin.json` sopravvive. Quando Simone scrive `Admin+Password`:
1. Brain intercetta regex `^[Aa]dmin\+(.+)` — priorità massima, primo check in `think()`
2. `_handle_admin_command()` verifica il checksum di `admin.json`
3. Verifica la password contro hash+salt
4. Ripristina `confidence_score`, `bond_proposed=True`, nome e identità nel profilo
5. Invia conferma di riconoscimento

**Cambio password**: `Admin+VecchiaPassword+NuovaPassword`

**Lockout**: dopo 5 tentativi falliti consecutivi, accesso temporaneamente bloccato.

---

## 6. MESSAGGI PROATTIVI

### Tipi, soglie e frequenza

| Tipo | Confidence minima | Frequenza | Note |
|---|---|---|---|
| Morning brief | **>= 0.3** | 1/giorno, 7:00–8:00 | 5 scenari; ora apprendibile da `morning_pattern.json` |
| Check-in inattività | **>= 0.4** | max 4/giorno, 1/ora | 120 min inattività; 0.3–0.4 → soppresso; <0.3 → delega a goal contact |
| Goal tipo `contact` | < 0.3 | soggetto a DiscretionEngine | ≥3h inattività; guardrail triplo post-generazione |
| Notizie interessi | nessuna | max 4/giorno, 1/ora | Soggetto a `DiscretionEngine.should_send()` |
| Calendar reminder | nessuna | illimitato (ore attive) | Escluso dal conteggio anti-spam |

### Check-in inattività — condizioni soppressione aggiuntive

Anche con `confidence >= 0.4`, il check-in viene soppresso se:
- Ultima interazione prima delle 7:00 di oggi
- Pattern comportamentale: ora corrente non mai attiva
- Evento calendario attivo in questo momento

### Morning brief — 5 scenari

1. **Compleanno** → auguri autentici
2. **Festivo + eventi** → auguri brevi + agenda (esclude colorId "11" Tomato)
3. **Festivo senza eventi** → auguri brevi
4. **Normale + eventi** → agenda + night thought (da `thoughts.md`)
5. **Normale senza eventi** → night thought o `SKIP`

### Regole comuni

- Ore silenziose: 23:00–07:00. Urgency "urgent" passa tranne 01:00–06:00.
- Se LLM non ha aggancio concreto → risponde `SKIP`, niente viene inviato.
- `DiscretionEngine`: `MAX_PER_HOUR = 1`, `MAX_PER_DAY = 4`. Stato in `discretion_state.json`.
- `calendar_reminder`: escluso dal conteggio anti-spam, passa sempre nelle ore attive.

---

## 7. SERVIZI SYSTEMD

| Servizio | Descrizione |
|---|---|
| `cipher.service` | Server Flask + ConsciousnessLoop |
| `cipher-telegram.service` | Bot Telegram (`cipher_bot.py`) |
| `cipher-memory.service` | Memory worker — consolida profilo ogni ora |
| `cipher-funnel.service` | Esposizione via Tailscale |

```bash
# Restart completo
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service

# Log in tempo reale
sudo journalctl -u cipher.service -f

# Stato tutti i servizi
sudo systemctl status cipher.service cipher-telegram.service cipher-funnel.service cipher-memory.service
```

Alias consigliato (aggiungere manualmente a `~/.bashrc`):
```bash
alias cipher-restart='sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service'
```

---

## 8. FILE IMPORTANTI

### Entry point e processi

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `config.py` | Path, modelli, costanti — unica fonte di verità | Importato da tutti i moduli all'avvio |
| `server.py` | Entry point Flask — init moduli, avvio ConsciousnessLoop | All'avvio (`cipher.service`) |
| `cipher_bot.py` | Bot Telegram — processo separato | `cipher-telegram.service` |
| `memory_worker.py` | Consolida profilo e memoria ogni ora | `cipher-memory.service` |
| `main.py` | Entry point CLI — modalità text o voice | Uso locale |

### Moduli core

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `modules/brain.py` | Core: LLM routing, system prompt, dispatcher, history, confidence | Ad ogni `POST /chat` |
| `modules/consciousness_loop.py` | Thread daemon — tutti i task periodici | Daemon continuo |
| `modules/self_reflection.py` | Auto-riflessione → `cipher_state.json` | Ogni 30 min |
| `modules/goal_manager.py` | Generazione e gestione obiettivi | Ogni 5/20 min |
| `modules/memory.py` | Profilo, conversazioni, estrazione, confidence | Ad ogni messaggio |
| `modules/night_cycle.py` | Sommario notturno, voice notes, pattern insights, prep eventi domani | Alle 3:00 |
| `modules/passive_monitor.py` | Notizie su interessi Cipher | Ogni 10 min |
| `modules/realtime_context.py` | Meteo + news per il system prompt | Ogni 60 min |
| `modules/episodic_memory.py` | Episodi salienti (ultimi 4 nel prompt) | Scrittura ogni riflessione; lettura ogni prompt |

### Moduli azioni e infrastruttura

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `modules/actions.py` | Dispatcher azioni — sistema consenso, esecuzione | Quando Brain rileva JSON azione |
| `modules/ethics_engine.py` | Livelli permesso 0–3, autonomia acquisita | Ad ogni richiesta azione |
| `modules/discretion.py` | Anti-spam, ore silenziose, urgenza | Prima di ogni invio proattivo |
| `modules/admin_manager.py` | Legame permanente — `admin.json` + checksum + `changelog.json` | Al bond trigger / `Admin+Password` |
| `modules/filesystem.py` | `~/cipher/home/` (R+W libera) e `~/cipher/` (R libera, W con consenso) — `.bak` su sovrascrittura | Su azione `fs_*` / `project_write` |
| `modules/notifier.py` | Bridge Telegram, `set_message_callback` | Ricezione/invio messaggi Telegram |
| `modules/scheduler.py` | Calendar reminder, apprendimento morning pattern | Reminder periodici |
| `modules/script_registry.py` | Script approvati per esecuzione — letti nel prompt dinamico | Su approvazione script |
| `modules/reminders.py` | Gestione azione `reminder_set` | Su richiesta azione |
| `modules/utils.py` | `write_json_atomic()`, `extract_llm_json` | Usato da tutti i moduli |
| `modules/action_log.py` | Log azioni eseguite | Dopo ogni azione |
| `modules/cipher_interests.py` | Argomenti di interesse di Cipher (decay notturno 0.03) | SelfReflection + PassiveMonitor; decay in NightCycle |
| `modules/contacts.py` | Rubrica nome→numero WhatsApp/Telegram | Su azione contatti/WhatsApp |
| `modules/episodic_memory.py` | Episodi salienti | Scrittura ogni riflessione; lettura ogni prompt |

### Moduli servizi esterni

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `modules/google_auth.py` | OAuth2 — valida scope al boot, elimina token non corrispondente | All'avvio se `secrets/credentials.json` esiste |
| `modules/google_cal.py` | Google Calendar API | Su azione calendario / morning brief / night prep |
| `modules/google_mail.py` | Gmail API (scope `gmail.modify`) | Su azione gmail — solo su richiesta utente esplicita |
| `modules/whatsapp.py` | WhatsApp Green API | Su azione `whatsapp_send` |
| `modules/listener.py` | STT Vosk offline (italiano + inglese) | Se `INPUT_MODE=voice\|both` |
| `modules/voice.py` | TTS ElevenLabs | Su azione `tts` / output vocale Telegram |
| `modules/file_engine.py` | Elaborazione file (xlsx, pdf, ecc.) | Su azione `file_read` / `file_modify` |

### Moduli parzialmente attivi

| File | Cosa contiene | Stato effettivo |
|---|---|---|
| `modules/web_search.py` | Ricerca web centralizzata (DDGS text + news) | Usato da brain.py e realtime_context.py |
| `modules/llm_usage.py` | Tracking chiamate LLM per giornata | Scritto da brain.py; letto da health endpoint e admin status |
| `modules/pattern_learner.py` | Analisi pattern comportamentali | `record_message()` e `record_interaction()` **attivi**; `_update_motivational_profile()` in `night_cycle.py` disabilitato |

### Comportamento e configurazione

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `comportamento/00_identity.txt` | Personalità, tono, regola anti-proiezione, comandi speciali | Caricato all'avvio nel prompt statico |
| `comportamento/azioni.txt` | Documentazione azioni per il dispatcher | Caricato all'avvio nel prompt statico |
| `config/dev_protocol.txt` | Regole sviluppo — iniettato solo su keyword dev | Condizionale ad ogni prompt |

### File dati permanenti

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `data/admin.json` | Legame permanente — sopravvive a Tabula Rasa e qualsiasi pulizia | Al bond trigger / `Admin+Password` |
| `data/changelog.json` | Log backup `.bak` creati da `filesystem.py` — sopravvive a Tabula Rasa | Scritto da `admin_manager.log_backup()` ad ogni sovrascrittura; max 200 entries |
| `data/patterns.json` | Pattern comportamentali da PatternLearner | Reset da Tabula Rasa; snapshot in `admin.json["patterns"]` al bond |
| `data/system_prompt_debug.txt` | Dump di debug del system prompt — non usato a runtime | Scritto manualmente per ispezione; ignorato da .gitignore |

### File memoria (resettabili da Tabula Rasa salvo dove indicato)

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `memory/profile.json` | Profilo utente + `confidence_score` | Ad ogni messaggio; consolidato da MemoryWorker |
| `memory/active_history.json` | Storia conversazione corrente | Ad ogni messaggio; TTL 24h (12h per messaggi autonomi) |
| `memory/cipher_state.json` | Stato emotivo Cipher + `want_to_explore` + `concern` | Scritto da SelfReflection ogni 30 min; letto nel prompt |
| `memory/goals.json` | Obiettivi attivi/completati | GoalManager ogni 5/20 min |
| `memory/thoughts.md` | Diario riflessioni di Cipher | Scritto ogni riflessione; letto nel prompt (max 300 chars) |
| `memory/short_term.json` | Eventi temporanei (TTL 48h) | Estratti da ogni messaggio |
| `memory/emotional_log.json` | Stato emotivo di Simone (ultimi 100 entry) | Scritto dopo ogni messaggio via classifier Haiku; letto nel prompt (3 dedup) |
| `memory/checkin_history.json` | Storico check-in (ultimi 3 giorni, anti-ripetizione) | Scritto dopo ogni check-in inviato |
| `memory/daily_summaries.md` | Sommari notturni + azioni del giorno | Scritto da NightCycle alle 3:00 |
| `memory/pattern_insights.md` | Intuizioni sui pattern comportamentali | Scritto da `NightCycle._reason_about_patterns()`; letto nel prompt (≤500 chars) |
| `memory/voice_notes.md` | Note sulla voce autentica di Cipher | Scritto da `NightCycle._update_voice_notes()`; letto nel prompt (last block) |
| `memory/morning_pattern.json` | Orario appreso di risposta mattutina | Scritto da Scheduler; letto da ConsciousnessLoop |
| `memory/morning_brief.json` | Documenti di preparazione eventi del giorno dopo | Scritto da `NightCycle._prepare_for_tomorrow()`; letto da morning brief |
| `memory/feedback_weights.json` | Pesi feedback implicito | Scritto da Brain; reset da Tabula Rasa |
| `memory/discretion_state.json` | Log messaggi inviati (anti-spam) | Prima/dopo ogni invio proattivo |
| `memory/night_cycle_last.json` | Data ultimo night cycle | Scritto da NightCycle per evitare doppia esecuzione |
| `memory/last_project_check.txt` | Hash dell'ultimo `project_inspect` | Scritto da Brain; diff troncato a 4000 chars |
| `memory/conversations/` | Una conversazione per sessione (formato JSON) | Scritto ad ogni sessione; pulizia automatica >30 giorni |
| `memory/episodes.json` | Episodi salienti strutturati | Scritto da EpisodicMemory |
| `memory/action_log.json` | Log azioni eseguite con timestamp | Scritto da `action_log.py` dopo ogni azione |
| `memory/cipher_interests.json` | Interessi autonomi di Cipher con pesi (decay notturno 0.03) | Scritto da `cipher_interests.py` |
| `memory/contacts.json` | Rubrica nome→numero WhatsApp/Telegram | Scritto da `contacts.py` |
| `memory/ethics_learned.json` | Permessi autonomia acquisiti dall'utente | Scritto da `ethics_engine.py` |
| `memory/ethics_log.md` | Log decisioni etiche | Scritto da `ethics_engine.py` |
| `memory/goals.md` | Storico testuale obiettivi completati | Scritto da `goal_manager.py` |
| `memory/llm_usage.json` | Conteggi chiamate LLM per giorno (ultimi 30 giorni) | Scritto da `llm_usage.py` |
| `memory/last_inspection.json` | Timestamp ultima auto-ispezione (48h interval) | Scritto da `ConsciousnessLoop` |
| `memory/memory_worker_state.json` | Stato ultimo run del memory worker | Scritto da `memory_worker.py` |
| `memory/patterns.json` | Cache patterns comportamentali locale | Scritto da `pattern_learner.py` |
| `memory/realtime_context.json` | Cache contesto real-time (meteo + news) | Scritto da `realtime_context.py`; TTL 60 min |
| `memory/screenshots.md` | Log screenshot condivisi dall'utente | Scritto a runtime |

### Secrets

| File | Cosa contiene | Ciclo di vita |
|---|---|---|
| `secrets/credentials.json` | Credenziali OAuth2 Google | Input manuale — mai toccato dal codice |
| `secrets/token.json` | Token OAuth2 Google | Generato al primo avvio OAuth2; eliminato e rigenerato se scope non corrisponde |

---

## 9. COMANDI UTILI

```bash
# Python (usa SEMPRE il venv)
/home/Szymon/Cipher/cipher-server/venv/bin/python3

# Verifica sintassi prima di restart
/home/Szymon/Cipher/cipher-server/venv/bin/python3 -m py_compile modules/nome.py

# Restart
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service

# Log
sudo journalctl -u cipher.service --since "5 min ago"
sudo journalctl -u cipher.service -f

# Stato servizi
sudo systemctl status cipher.service cipher-telegram.service cipher-funnel.service cipher-memory.service

# Ricarica comportamento/ senza restart
brain.reload_static_prompt()

# Forza ricalcolo system prompt
brain.invalidate_system_prompt()
```

### Comandi speciali Telegram

| Comando | Effetto |
|---|---|
| `tabula rasa` / `/tabularasa` | Propone reset completo memoria (chiede conferma) |
| `Admin+Password` | Login admin: ripristina profilo post-Tabula Rasa |
| `Admin+Password+status` | Diagnostica sistema (LLM calls, confidence, goals, modelli) |
| `Admin+VecchiaPassword+NuovaPassword` | Cambio parola segreta |
| `revoca autonomia` | Resetta tutti i permessi acquisiti (chiede conferma) |
| `revoca autonomia [azione]` | Revoca permesso specifico |

---

## 10. NOTE PER SVILUPPATORI

### Guardrail Anthropic

I moduli background (`GoalManager`, `SelfReflection`) devono sempre includere `{"role": "system", ...}` nelle chiamate LLM con descrizione funzionale. Una chiamata senza system prompt con user message che inizia con "Sei [nome]" viene interpretata come identity override e triggerata dal safety filter.

**Pattern che triggherano il safety filter:**
1. Prompt user-role che inizia con `"Sei [nome]"` — identity override
2. Titoli obiettivi con termini sorveglianti: `analizzare pattern`, `monitorare engagement`, `verificare preferenze`, `pattern cognitivi`, `analisi psicologica`
3. Titoli obiettivi con termini manipolativi: `dipendenza`, `dark pattern`, `manipolazione`, `vulnerabilità contestuale`
4. `comportamento/00_identity.txt` con frasi `"non sono un assistente"` o `"non un bot"` — identity-override filter
5. `emotional_reason` / `want_to_explore` in `cipher_state.json` con keyword da `_meta_keywords` — scartati prima dell'injection
6. `## Task in corso` con descrizioni obiettivi (non solo titoli) — potenziale linguaggio sorvegliante

**Regole:**
- Mai iniziare un prompt user-role con "Sei [nome]" nei moduli background. Usare descrizioni funzionali (es. `"Stai generando un aggiornamento di stato per il sistema Cipher"`).
- `## Task in corso` inietta solo titoli obiettivi — mai descrizioni.
- `cipher_state.json` → `emotional_reason` e `want_to_explore` vengono sanitizzati prima dell'injection.
- Titoli con termini vietati vengono scartati dal guardrail post-generazione in `GoalManager`.

### Pattern da non rompere

- `admin.json` è permanente — non aggiungere logica che lo tocchi in Tabula Rasa o pulizie.
- `changelog.json` è permanente — è in `data/`, non in `memory/`. Non spostarlo.
- `data/patterns.json` viene cancellato da Tabula Rasa; i pattern strutturati vengono salvati in `admin.json["patterns"]` al momento del bond.
- Flask su `127.0.0.1` — non cambiare a `0.0.0.0`. Esposizione gestita da `cipher-funnel.service`.
- Tag messaggi proattivi: prefisso `[messaggio autonomo DD/MM/YYYY HH:MM]: ...` — non alterare il formato.
- `GoalManager`: `goals.json` key `"goals"`. Vuoto: `{"goals": []}`.
- `DiscretionEngine`: `discretion_state.json` key `"sent_log"`. Vuoto: `{"sent_log": []}`.
- Gmail: scope `gmail.modify` in `Config.GOOGLE_SCOPES` — non rimuoverlo senza rigenerare il token.
- Scritture JSON condivise: `write_json_atomic()` da `modules/utils.py` (`.tmp` + `rename()` atomico).
- Scrittura file: solo dentro `Config.HOME_DIR` o `Config.MEMORY_DIR`.
- Mai hardcodare path: usare sempre `Config.*`.
- `_call_llm_quality()` non ha system prompt — non aggiungerne uno senza capire le conseguenze su NightCycle.
- `_OBIETTIVI_KEYWORDS` è stato **rimosso intenzionalmente** da `Brain.think()`. Non reintrodurlo. Il flusso normale `_call_llm()` gestisce già "che fai?" correttamente: i goal attivi sono iniettati nel system prompt dinamico (blocco `## Task in corso`, solo titoli), e l'identità Cipher è sempre presente. Il vecchio handler usava `_call_llm_quality()` senza system prompt, causando risposte senza identità e hallucination.
- **Regole linguistiche in `brain.py` regola 7** (hardcoded): include divieti su "Meglio così" su eventi neutri, opener da assistente ("Certo!", "Esatto!", "Perfetto!"), e "come è andata?" su eventi banali. Non rimuoverle — coprono casi non gestiti da `comportamento/`.
- **Banda CONOSCENTE in `_build_confidence_context()`** (confidence < 0.2): include "Non offrire suggerimenti o soluzioni se non esplicitamente richiesti." — non rimuovere. La banda ora chiarisce esplicitamente che "misurato" = non forzare familiarità, NON = tono formale o da assistente.
- **`_conoscere_utente` in `_build_confidence_context()`**: iniettato se `confidence < 0.7`. Contiene elenco esplicito di frasi vietate ("Di cosa hai bisogno?", "Come posso aiutarti?", "Piacere, come ti chiami?" come apertura secca) — non rimuoverle. Il tono target è *curiosità verso l'altro*, non *disponibilità a servire*.
- **`comportamento/00_identity.txt` — sezione "Come parli"**: contiene divieti espliciti su frasi da assistente helper ("Di cosa hai bisogno?", "Come posso aiutarti?", "In cosa posso esserti utile?") — non rimuoverle.
- **Regola doppia domanda in `comportamento/00_identity.txt`**: "Una domanda per messaggio, sempre — indipendentemente dal livello di confidenza." è una regola **generale**, non legata alla banda. La stessa regola esiste anche in `_conoscere_utente` (solo per confidence < 0.7) — sono due layer distinti, non duplicati.

### Moduli parzialmente attivi

| Modulo | Parti attive | Parti disabilitate |
|---|---|---|
| `pattern_learner.py` | `record_message()`, `get_predictions()`, `get_active_hours()`, `get_never_active_hours()` | `_update_motivational_profile()` in `night_cycle.py` — chiamata commentata |

Non rimuovere le parti attive. Non riabilitare le parti disabilitate senza decisione esplicita.

---

## 11. VINCOLI CRITICI

- **Mai hardcodare path**: usare sempre `Config.*`.
- **Scrittura file**: solo dentro `Config.HOME_DIR` o `Config.MEMORY_DIR`.
- **Scritture JSON condivise**: `write_json_atomic()` da `modules/utils.py`.
- **Max 20 messaggi in history**: `MAX_HISTORY_MESSAGES * 2 = 20`. Non rimuovere il limite.
- **TTL active_history**: 24h messaggi normali, 12h per `[messaggio autonomo...]`. In `_load_history()`.
- **TTL short_term**: 48h (172800s). In `memory.py`.
- **System prompt TTL**: 300s (`_SYSTEM_PROMPT_TTL` in Brain).
- **Tag messaggi proattivi**: prefisso `[messaggio autonomo DD/MM/YYYY HH:MM]: ...`. Non alterare.
- **Timestamp in history**: ogni messaggio in `_history` (user E assistant) porta il prefisso `[DD/MM/YYYY HH:MM]`. La regola 10 del system prompt istruisce l'LLM a usare SOLO questi timestamp per calcoli temporali — non alterare il formato né rimuovere i prefissi.
- **Morning brief**: gestito solo da `consciousness_loop.py:_send_morning_brief()`. Non reintrodurre nello scheduler.
- **Morning brief confidence gate**: `confidence >= 0.3`. Non rimuovere.
- **Check-in confidence gate**: `confidence >= 0.4`. Tra 0.3–0.4: soppresso. Sotto 0.3: delega a goal contact.
- **Thread LLM**: chiamate LLM da ConsciousnessLoop in thread separato. Nel loop principale → deadlock.
- **Flask su 127.0.0.1**: non cambiare a `0.0.0.0`.
- **Consenso azioni sensibili**: `shell_exec`, `fs_delete`, `project_write`, `gmail_send` richiedono conferma esplicita. Il check avviene in Brain/actions, non in `filesystem.py`.
- **Moduli opzionali Brain**: `_pattern_learner`, `_episodic_memory` sono `None` all'avvio. Ogni riferimento deve fare `if self._xxx:`.
- **LLM provider fallback**: Brain ha `_fallback_client` che tenta il provider alternativo (OpenRouter ↔ Anthropic) se il primario fallisce. Richiede la API key dell'altro provider nel `.env`.
- **Rate limiting**: server.py ha rate limiting per IP (30 req/min) su tutti gli endpoint tranne `/health`.
- **LLM usage tracking**: `modules/llm_usage.py` registra ogni chiamata LLM in `memory/llm_usage.json` (ultimi 30 giorni).
- **Google credentials**: in `secrets/`. `google_auth.py` valida scope al boot — token non corrispondente viene eliminato e rilancia OAuth2.
- **`project_inspect`**: usa `_call_llm`. Hash in `memory/last_project_check.txt`. Diff troncato a 4000 chars.
- **Anti-spam**: `MAX_PER_HOUR = 1`, `MAX_PER_DAY = 4`. In `modules/discretion.py`. I `calendar_reminder` sono esclusi.
- **`admin.json`**: mai cancellarlo — sopravvive a Tabula Rasa e a qualsiasi pulizia.
- **`changelog.json`**: mai cancellarlo e mai spostarlo da `data/`.
- **Goal tipo contact**: generato solo se `confidence < 0.3` E `hours_since_interaction >= 3`. Guardrail triplo post-generazione. Non allentare.
- **Max goal attivi**: 3. Non aumentare senza valutare impatto su LLM calls.
- **`_call_llm_quality()` senza system prompt**: non aggiungere system prompt — è deliberato per NightCycle.
- **Confidence score non decresce mai**: non aggiungere logica di decremento.
- **`project_write` crea `.bak`**: solo su sovrascrittura (`not append and target.exists()`). Non rimuovere questo meccanismo.
- **`glob("*")` su `comportamento/`**: carica tutto — mai lasciare `.bak`, `.old`, file temporanei in quella directory.

**Fine sessione**: aggiornare `.claude/session-state.md` con i file toccati, le modifiche fatte e l'esito finale.
