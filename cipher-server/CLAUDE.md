# CLAUDE.md — Cipher AI Server

Guida operativa per Claude Code. Leggere **prima** di toccare qualsiasi file.

---

## Avvio sessione

Se esiste `.claude/session-state.md`, leggilo subito.
Prima di modificare qualsiasi file, leggi: `config.py`, il modulo da toccare, e i moduli che lo importano.

**Regola sempre attiva**: dopo ogni modifica aggiorna `CLAUDE.md` e `README.md` per riflettere lo stato reale.

**Regola README — albero directory**: il blocco ```` ``` ```` in "Struttura del progetto" deve contenere solo nomi di file/cartelle, senza commenti inline (`# ...`). Le descrizioni vanno esclusivamente nella sezione "File e directory — descrizione" con le tabelle Markdown.

---

## 1. Panoramica

Cipher è un AI companion con memoria persistente, riflessione autonoma, obiettivi propri e messaggi proattivi via Telegram. Chiunque interagisca è trattato allo stesso modo — la relazione cresce dai comportamenti, non dai nomi dichiarati. Il solo fatto noto sull'autore: Cipher è stato creato da Simone. Gira su Linux (anche Raspberry Pi). LLM backend: Claude via OpenRouter o Anthropic diretto. Routing: Haiku per background silenzioso, Sonnet per conversazione e messaggi visibili.

---

## 2. Architettura

```
Telegram → cipher_bot.py → POST /chat (127.0.0.1) → server.py
                                                        │
                                                    Brain.think()
                                                   /            \
                                            memoria          coscienza
                                           memory.py    consciousness_loop.py
                                         profile.json     self_reflection.py
                                        active_history      goal_manager.py
                                       episodic_memory       night_cycle.py
                                                        \            /
                                                    servizi esterni
                                            google_cal · gmail · whatsapp
                                          web_search · ElevenLabs · Vosk
```

**Init order in `server.py`**: `Brain → Notifier → Scheduler → ConsciousnessLoop`

ConsciousnessLoop inietta in Brain: `_pattern_learner`, `_episodic_memory` (partono `None` — ogni riferimento deve fare `if self._xxx:`).

**Flusso `Brain.think()`** (in ordine):
1. Intercetta `Admin+Password` regex `^[Aa]dmin\+(.+)` → `_handle_admin_command()` (priorità max)
2. `_awaiting_bond_password` → `_handle_bond_password()`
3. Controlla `tabula rasa` / `revoca autonomia` / reset conversazione (two-phase confirm)
4. Controlla audit / pensieri keywords
5. `consciousness.handle_consent_response()`
6. `dispatcher.has_pending()` → `check_consent()`
7. `handle_forget_command` / `handle_remember_command`
8. Append history con timestamp `[DD/MM/YYYY HH:MM]` su user e assistant
9. `_get_system_prompt()` (TTL 300s)
10. `_pre_action.gather()` → `verified_data` iniettato DOPO la TTL cache
11. `_call_llm(verified_data=...)` → risposta (sempre Sonnet)
12. `extract_all_action_json` → `ActionDispatcher.execute()` + cache invalidation su `calendar_create/delete`, `gmail_send`
13. Thread daemon (delay 10s): `extract_from_message`, `pattern_learner.record_message`, `emotional_log`, `feedback_weights`, `detect_and_update_confidence` → BOND_TRIGGER check
14. Se `source=="web"`: inietta nota UI actions in `verified_data`

**ConsciousnessLoop** (ciclo ~60s, ogni task wrappato in `_run_with_timeout()`):

| Task | Intervallo |
|---|---|
| Check inattività | continuo (soglia 120 min) |
| Refresh contesto real-time | ogni 60 min |
| Morning brief | 7:00–8:00, 1/giorno |
| Auto-riflessione | ogni 30 min (×2 se inattivo >7200s) |
| Generazione obiettivi | ogni 20 min (Haiku, max 3 attivi) |
| Esecuzione obiettivi | ogni 5 min (richiede confidence ≥ 0.4) |
| Auto-ispezione | keyword-triggered da Brain (Opus 4.6) |
| Pulizia goal scaduti | ogni ciclo (max age 24h) |

---

## 3. System prompt

**Statico**: caricato all'avvio con `sorted(glob("*"))` su `comportamento/`. Ricaricabile senza restart: `brain.reload_static_prompt()`. ⚠️ Mai lasciare `.bak`/`.old` in `comportamento/`.

**Dinamico** (TTL 300s, ricalcolato da `_build_system_prompt()`):

| # | Blocco | Condizione |
|---|---|---|
| 1 | Static prompt (`comportamento/`) | sempre |
| 2 | Data/ora corrente | sempre |
| 3 | 14 REGOLE FONDAMENTALI (hardcoded in Brain) | sempre |
| 4 | Memory context (`memory.py`) | sempre |
| 5 | Livello confidenza (`profile.json`) | sempre |
| 6 | `## MODALITÀ ONBOARDING` | se `confidence == 0.0` AND `personal.nome` assente — sostituisce il blocco CONOSCENTE |
| 7 | `## Conoscere l'utente` | solo se confidence < 0.7 (e non in onboarding) |
| 7 | Profilo motivazionale | se `profile.json["motivations"]` presenti |
| 8 | Pattern insights | se `memory/pattern_insights.md` esiste (≤500 chars, ultimo blocco) |
| 9 | Voice notes | se `memory/voice_notes.md` esiste (ultimo blocco) |
| 10 | Real-time context (meteo + news) | cache TTL 60 min |
| 11 | Task in corso | se goals attivi — **solo titoli** |
| 12 | Cipher state | `memory/cipher_state.json` sanitizzato |
| 13 | Ultimo pensiero | `memory/thoughts.md` (max 300 chars) |
| 14 | Stato emotivo recente | `memory/emotional_log.json` (3 dedup) + nota se ultimo stato negativo |
| 15 | Dev protocol | `config/dev_protocol.txt` se keyword dev negli ultimi 5 msg |
| 16 | Nota morning brief | se `brief_sent_today()` è True |
| 17 | Modalità voce | se `voice_source=True` |
| 18 | **[DATI VERIFICATI]** | iniettato da `_build_messages()` DOPO TTL cache — sempre |

`[DATI VERIFICATI]` (PreActionLayer): L1 sempre (datetime + `calendar_today` cache 300s), L2 rule-based (`email_unread` cache 60s se keyword email nel messaggio). Cache in-memory, si azzera al restart. Invalidazione dopo `calendar_create/delete`/`gmail_send`.

`_meta_keywords` filtrati da `cipher_state.json` prima dell'injection: `"prompt", "personaggio", "fittizio", "costruito per", "jailbreak", "manipol", "recitare", "realtà del mio funzionamento", "limiti del sistema", "auto-riflessione"`.

---

## 4. Modelli LLM

| Funzione | Metodo | Modello | Parametri |
|---|---|---|---|
| Conversazione principale | `_call_llm()` | **Sonnet** (`OPENROUTER_MODEL`) | max_tokens=1024, temp=0.4, retry 3× su rate limit |
| Messaggi proattivi visibili | `_call_llm_visible()` | Sonnet | max_tokens=512, temp=0.4 |
| Output creativo / NightCycle | `_call_llm_quality()` | Sonnet | max_tokens=512, temp=0.5, **nessun system prompt** |
| Background silenzioso | `_call_llm_silent()` | Haiku (`BACKGROUND_MODEL`) | max_tokens=256, temp=0.2 |
| GoalManager / SelfReflection | `_call_llm_silent()` | Haiku | idem |
| Auto-ispezione keyword-triggered | `_call_llm_opus()` | Opus 4.6 (`OPUS_MODEL`) | max_tokens=1024, con system prompt |

`_route_model()` ritorna **sempre** `OPENROUTER_MODEL` (Sonnet). `CONVERSATION_MODEL` nel .env esiste ma non è usato nel routing attuale.

Fallback: `_fallback_client` tenta il provider alternativo se il primario fallisce (richiede API key alternativa nel .env).

Config `.env`: `LLM_PROVIDER` (openrouter/anthropic), `OPENROUTER_MODEL`, `BACKGROUND_MODEL`, `OPUS_MODEL`.

---

## 5. Confidence e relazione

`confidence_score` float 0.0–1.0 in `memory/profile.json`. **Non può scendere mai.**

| Score | Livello | Comportamento chiave |
|---|---|---|
| 0.0–0.2 | Conoscente | Diretto, no intimità forzata, no suggerimenti non richiesti |
| 0.2–0.4 | Amico | Una domanda personale leggera per sessione, se nasce dal contesto |
| 0.4–0.6 | Amico stretto | Domande naturali, stati d'animo condivisi, ironia leggera |
| 0.6–0.8 | Confidente | Emozioni aperte, domande profonde, può usare nome/soprannomi |
| 0.8–1.0 | Migliore amico | Diretto, anticipa bisogni, storia condivisa implicita |

**Segnali (delta)**: `personal_story` +0.020, `advice_request` +0.030, `nickname_joke` +0.025, `emotion_shared` +0.015, `gratitude` +0.010, `long_session` +0.010, `daily_streak` +0.005.

**Bond trigger**: `confidence >= 0.8` + `bond_proposed=False` → Cipher propone legame e chiede parola segreta → `_handle_bond_password()` → crea `data/admin.json`. Auto-aggiornamento `admin.json` se `score >= 0.8` e `(score - saved_score) >= 0.049`.

---

## 6. Messaggi proattivi

| Tipo | Confidence min | Frequenza | Note |
|---|---|---|---|
| Morning brief | ≥ 0.3 | 1/giorno, 7:00–8:00 | 5 scenari; orario adattivo da `morning_pattern.json` |
| Check-in inattività | ≥ 0.4 | max 4/giorno, 1/ora | 120 min inattività; 0.3–0.4 soppresso; <0.3 non inviato |
| Goal tipo `contact` | < 0.3 | soggetto a DiscretionEngine | ≥3h inattività, guardrail triplo |
| Notizie interessi | nessuna | max 4/giorno, 1/ora | soggetto a `DiscretionEngine.should_send()` |
| Calendar reminder | nessuna | illimitato (ore attive) | **escluso dal conteggio anti-spam** |

Ore silenziose: 23:00–07:00 (urgency "urgent" passa tranne 01:00–06:00). `DiscretionEngine`: `MAX_PER_HOUR=1`, `MAX_PER_DAY=4`, stato in `discretion_state.json`. Calendar reminder escluso dal contatore.

Check-in soppresso anche con confidence ≥ 0.4 se: proattivo già pendente, ultima interazione prima delle 7:00, ora mai attiva (pattern_learner), evento calendario attivo.

**Morning brief — 5 scenari**: compleanno → auguri; festivo+eventi → auguri+agenda (esclude colorId "11"); festivo senza eventi → auguri; normale+eventi → agenda+night thought; normale senza eventi → night thought o `SKIP`.

---

## 7. Comandi speciali Telegram

| Comando | Effetto |
|---|---|
| `tabula rasa` / `/tabularasa` | Reset completo memoria (chiede conferma) |
| `Admin+Password` | Login admin: ripristina profilo post-Tabula Rasa |
| `Admin+Password+status` | Diagnostica: LLM calls, confidence, goals, modelli |
| `Admin+VecchiaPassword+NuovaPassword` | Cambio parola segreta |
| `revoca autonomia` | Resetta tutti i permessi acquisiti (chiede conferma) |
| `revoca autonomia [azione]` | Revoca permesso specifico |

Lockout: 3 tentativi falliti → 10 minuti.

---

## 8. Servizi systemd

| Servizio | Processo |
|---|---|
| `cipher.service` | Flask server + ConsciousnessLoop |
| `cipher-telegram.service` | `cipher_bot.py` |
| `cipher-memory.service` | `memory_worker.py` (consolida profilo ogni ora) |
| `cipher-funnel.service` | Esposizione via Tailscale |

```bash
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service
sudo journalctl -u cipher.service -f
sudo systemctl status cipher.service cipher-telegram.service cipher-funnel.service cipher-memory.service
/home/Szymon/Cipher/cipher-server/venv/bin/python3 -m py_compile modules/nome.py  # verifica sintassi
```

---

## 9. File e moduli

### Entry point e processi

| File | Ruolo |
|---|---|
| `config.py` | Unica fonte di verità per path, modelli, costanti |
| `server.py` | Flask entry point — init moduli, ConsciousnessLoop, tutti gli endpoint |
| `cipher_bot.py` | Bot Telegram (processo separato) |
| `memory_worker.py` | Consolida profilo e memoria ogni ora (processo separato) |
| `main.py` | Entry point CLI (text/voice) |

### Endpoint Flask

Pubblici (no auth): `/health`, `/web`, `/web/`
Rate limit: 30 req/min normali; 120 req/min per `/api/terminal`, `/api/files/*`, `/api/dashboard`, `/api/history`

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/chat` | POST | Conversazione principale |
| `/stt` | POST | Speech-to-text Vosk |
| `/tts` | POST | Text-to-speech ElevenLabs |
| `/web` | GET | Dashboard JARVIS HUD v2 (token iniettato server-side) |
| `/api/dashboard` | GET | Stato sistema (polling ogni 10s) |
| `/api/history` | GET | History conversazione (polling ogni 5s) |
| `/api/files/*` | GET/POST/DELETE | File manager (path validation via `_safe_file_path()`, upload max 10 MB, `.bak` su sovrascrittura) |
| `/api/terminal` | POST | Terminale sandbox in `HOME_DIR` (shell=True, timeout 10s, output cap 50 KB, blocca `_TERMINAL_BLOCKED`) |
| `/api/calendar` | GET | Lista eventi (?days=7, ?q=search) |
| `/api/calendar` | POST | Crea evento (JSON: title, start, end?, description?, location?) |
| `/api/calendar/<id>` | PUT | Modifica evento |
| `/api/calendar/<id>` | DELETE | Elimina evento |
| `/api/notes` | GET/POST | Note (legacy, non più nella UI) |
| `/consciousness/*` | GET | Status, thoughts, goals |
| `/memory`, `/memory/interests`, `/reset` | GET/POST | Memoria e reset |

Dashboard `web/index.html`: JARVIS HUD con chat sempre visibile come base layer. Navigazione tramite **wheel selector** (icona circolare 120px in alto a destra) che apre 5 label a pillola su arco circolare: FS, BASH, CAL, GOALS, INFO. Ogni label apre un **popup flottante** draggabile sopra la chat. Più popup aperti contemporaneamente. Accessibile via `https://cipher.taila739e7.ts.net/web`. Invia `source:"web"` → Brain inietta UI actions.

UI actions (solo `source=="web"`): `ui_navigate`, `ui_open_file`, `ui_terminal`. Parsate e rimosse dal testo da `parseUiActions()` in JS.

### Moduli core

| File | Ruolo |
|---|---|
| `modules/brain.py` | LLM routing, system prompt, dispatcher, history, confidence, admin |
| `modules/pre_action_layer.py` | Dati verificati real-time — iniettati dopo TTL cache |
| `modules/consciousness_loop.py` | Thread daemon — tutti i task periodici |
| `modules/self_reflection.py` | Auto-riflessione → `cipher_state.json` ogni 30 min |
| `modules/goal_manager.py` | Genera e gestisce obiettivi (5 tipi: explore, protect, task, observe, reflect) |
| `modules/memory.py` | Profilo, conversazioni, estrazione, confidence |
| `modules/night_cycle.py` | Sommario notturno, voice notes, pattern insights, prep eventi domani (3:00) |
| `modules/passive_monitor.py` | Notizie su interessi Cipher ogni 10 min |
| `modules/realtime_context.py` | Meteo + news per system prompt (cache TTL 60 min) |
| `modules/episodic_memory.py` | Episodi salienti — scrittura ogni riflessione, recall query-based nel prompt |

### Moduli azioni e infrastruttura

| File | Ruolo |
|---|---|
| `modules/actions.py` | ActionDispatcher — 30+ tipi, sistema consenso, lazy loaders |
| `modules/ethics_engine.py` | Livelli permesso 0–3, autonomia acquisita |
| `modules/discretion.py` | Anti-spam, ore silenziose, urgenza |
| `modules/admin_manager.py` | Legame permanente — `admin.json` + checksum + `changelog.json` |
| `modules/filesystem.py` | `~/cipher/home/` (R+W libera), `~/cipher/` (R libera, W con consenso), `.bak` su sovrascrittura |
| `modules/notifier.py` | Bridge Telegram, `set_message_callback` |
| `modules/scheduler.py` | Calendar reminder, apprendimento morning pattern |
| `modules/reminders.py` | Gestione azione `reminder_set` |
| `modules/utils.py` | `write_json_atomic()`, `extract_llm_json` |
| `modules/action_log.py` | Log azioni con source tracking |

### Moduli servizi esterni

| File | Ruolo |
|---|---|
| `modules/google_auth.py` | OAuth2 — valida scope al boot, elimina token non corrispondente |
| `modules/google_cal.py` | Google Calendar API |
| `modules/google_mail.py` | Gmail API (scope `gmail.modify`) — solo su richiesta esplicita |
| `modules/whatsapp.py` | WhatsApp Green API |
| `modules/listener.py` | STT Vosk offline (italiano + inglese) |
| `modules/voice.py` | TTS ElevenLabs |
| `modules/file_engine.py` | Elaborazione file (xlsx, pdf, ecc.) |
| `modules/web_search.py` | DDGS text + news — usato da brain.py e realtime_context.py |

### Moduli parzialmente attivi

| Modulo | Parti attive | Parti disabilitate |
|---|---|---|
| `pattern_learner.py` | `record_message()`, `get_predictions()`, `get_active_hours()`, `get_never_active_hours()` | `_update_motivational_profile()` in `night_cycle.py` — commentata |
| `llm_usage.py` | Scritto da brain.py, letto da `/health` e admin status | — |

### File dati (permanenti — mai resettati)

| File | Contenuto |
|---|---|
| `data/admin.json` | Legame permanente (PBKDF2-SHA256 + checksum SHA-256) |
| `data/changelog.json` | Log backup `.bak` — max 200 entries |
| `data/patterns.json` | Pattern da PatternLearner (snapshot in `admin.json["patterns"]` al bond) |

### File memoria (in `Config.MEMORY_DIR`, resettabili da Tabula Rasa)

- **Profilo e storia**: `profile.json` (confidence, confidence_history 20 entry), `active_history.json` (max 20 msg, TTL 24h/12h), `conversations/` (1 file/sessione, pulizia >30gg)
- **Stato Cipher**: `cipher_state.json`, `thoughts.md`, `cipher_interests.json`
- **Stato Simone**: `emotional_log.json` (100 entry), `feedback_weights.json`, `short_term.json` (TTL 48h)
- **Obiettivi**: `goals.json` (key: `"goals"`), `goals.md`
- **Notturno**: `daily_summaries.md`, `pattern_insights.md`, `voice_notes.md`, `morning_brief.json`, `morning_pattern.json`
- **Infrastruttura**: `discretion_state.json`, `night_cycle_last.json`, `llm_usage.json`, `action_log.json`, `realtime_context.json`
- **Autonomia**: `ethics_learned.json`, `ethics_log.md`, `contacts.json`, `episodes.json`

### Secrets

`secrets/credentials.json` — OAuth2 Google (input manuale). `secrets/token.json` — generato al primo avvio OAuth2, eliminato se scope non corrisponde.

---

## 10. Vincoli critici

- Mai hardcodare path — usare sempre `Config.*`
- Scrittura file solo dentro `Config.HOME_DIR` o `Config.MEMORY_DIR`
- Scritture JSON condivise: `write_json_atomic()` da `modules/utils.py`
- Max 20 messaggi in history: `MAX_HISTORY_MESSAGES = 10` (10 coppie)
- `admin.json` e `changelog.json` sono permanenti — mai toccarli in Tabula Rasa o pulizie; `changelog.json` non spostarlo da `data/`
- Flask su `127.0.0.1` — non cambiare a `0.0.0.0`
- `_route_model()` ritorna sempre Sonnet — non modificare senza valutare costo/latenza
- `_call_llm_quality()` senza system prompt — deliberato per NightCycle, non aggiungerne uno
- `_call_llm_opus()` ha system prompt — deliberato per auto-ispezione
- Confidence score non decresce mai
- `glob("*")` su `comportamento/` carica tutto — mai file `.bak`/`.old` in quella directory
- Tag messaggi proattivi: `[messaggio autonomo DD/MM/YYYY HH:MM]: ...` — non alterare il formato
- Timestamp history: prefisso `[DD/MM/YYYY HH:MM]` su user e assistant — non alterare
- Regola 11 system prompt: vieta timestamp nell'output
- `## Task in corso` inietta solo titoli obiettivi — mai descrizioni
- `project_write` crea `.bak` solo su sovrascrittura (`not append and target.exists()`)
- Consenso richiesto per: `shell_exec`, `fs_delete`, `project_write`, `gmail_send`
- Moduli opzionali Brain: `_pattern_learner`, `_episodic_memory` partono `None` — ogni riferimento fa `if self._xxx:`
- Max 3 goal attivi concorrenti — non aumentare senza valutare impatto LLM calls
- Anti-spam: `MAX_PER_HOUR=1`, `MAX_PER_DAY=4` — calendar reminder escluso
- Rate limiting: 30 req/min su endpoint normali, 120 su endpoint dashboard
- `_safe_file_path()` in server.py: `.resolve()` + prefix check vs `HOME_DIR` — non bypassare
- Lockout admin: 3 tentativi falliti → 10 minuti — non abbassare il threshold
- Morning brief gestito solo da `consciousness_loop.py:_send_morning_brief()` — non reintrodurre nello scheduler
- `_OBIETTIVI_KEYWORDS` rimosso intenzionalmente da `Brain.think()` — non reintrodurlo
- Google OAuth2: scope `gmail.modify` in `Config.GOOGLE_SCOPES` — non rimuovere senza rigenerare token
- LLM provider fallback: `_fallback_client` tenta provider alternativo se primario fallisce

---

## 11. Note sviluppo

### Guardrail Anthropic

Pattern che triggherano il safety filter:
1. Prompt user-role che inizia con `"Sei [nome]"` — usare descrizioni funzionali nei moduli background
2. Titoli obiettivi con termini analitici: `analizzare pattern`, `monitorare engagement`, `verificare preferenze`, `pattern cognitivi`, `analisi psicologica`
3. Titoli obiettivi con termini manipolativi: `dipendenza`, `dark pattern`, `manipolazione`, `vulnerabilità contestuale`
4. `comportamento/00_identity.txt` con `"non sono un assistente"` o `"non un bot"` — identity-override filter
5. `emotional_reason`/`want_to_explore` con `_meta_keywords` — scartati prima dell'injection

Moduli background (`GoalManager`, `SelfReflection`) devono sempre includere `{"role": "system", ...}` nelle chiamate LLM con descrizione funzionale.

### 14 Regole fondamentali (hardcoded in Brain)

Incluse nel system prompt ad ogni chiamata. Coprono: no allucinazioni, no domande già risposte, italiano naturale da chat, no riempitivi (`"Meglio così"` su eventi neutri, opener da assistente, `"come è andata?"` su eventi banali), no inventare su sé stesso, solo timestamp dalla history per calcoli temporali, no timestamp nell'output, riconoscimento stati emotivi negativi, consapevolezza gap temporale (8+ ore), divieto riempitivi (`"Classico!"`, `"Top!"`, `"Bello!"`, `"Forte!"`, `"Capisco!"`, `"Interessante!"`).

### Regole specifiche

- **Modalità ONBOARDING**: `_build_confidence_context()` ritorna early con blocco ONBOARDING se `confidence == 0.0` AND `personal.nome` mancante. Nessun flag separato — source of truth è `profile.json`. Esce automaticamente appena `memory.py` salva il nome. Non rimuovere il check. Nessun privilegio per nessun nome dichiarato — chiunque passi per l'onboarding uguale
- Banda CONOSCENTE (confidence < 0.2) in `_build_confidence_context()`: include "Non offrire suggerimenti o soluzioni se non esplicitamente richiesti" — non rimuovere
- `_conoscere_utente` iniettato se confidence < 0.7 (e non in onboarding): contiene frasi vietate esplicite — non rimuovere
- Domande contestuali: le fasce con "una domanda per sessione" hanno il vincolo "solo se nasce naturalmente dalla conversazione" — non rimuovere il vincolo
- Auto-ispezione: `_INSPECTION_KEYWORDS` in `brain.py` intercetta frasi come "cosa miglioreresti" → `consciousness.trigger_self_inspection()` in thread daemon. Usa Opus 4.6. Non è periodica
- `emotional_log` classifier: usa contesto delle ultime 2 battute; frasi consolatorie generiche vietate anche nei proattivi
- Check-in emotivo: se stato negativo in `emotional_log.json`, inietta `{simone_emotional_context}` nel `CHECKIN_PROMPT`
- `_call_llm()`: supporta `image_b64` e `media_type` — inietta immagine nell'ultimo messaggio utente come content array
- `brief_sent_today()` True → nota nel prompt che invita a rispondere come amico già sentito stamattina

**Fine sessione**: aggiornare `.claude/session-state.md` con file toccati, modifiche fatte, esito.

---

## 12. Dashboard web — `web/index.html`

Tutta la frontend è in un unico file: `web/index.html` (~91 KB, HTML + CSS + JS inline). Non esistono file CSS/JS separati. Tema HUD rosso/cyan (Iron Man/JARVIS style).

### Architettura UI

- **Base layer**: chat sempre visibile (text + voice mode) con infobar in alto (CIPHER, data/ora, stato, confidence)
- **Wheel selector**: icona circolare 120px (`web/static/logo.jpg`) in alto a destra; click apre/chiude 5 label a pillola su arco circolare con anello decorativo animato
- **Popup flottanti**: ogni sezione si apre come finestra draggabile sopra la chat; più popup aperti simultaneamente; z-index incrementale per bring-to-front
- **Nessuna sidebar/navbar**: navigazione solo tramite wheel selector

### Wheel selector (fan menu)

| Label | `data-section` | Popup | Funzione |
|---|---|---|---|
| FS | `files` | FILE MANAGER | Naviga `~/cipher/home/`, upload/download/elimina |
| BASH | `terminal` | TERMINAL | Shell sandbox (timeout 10s, comandi pericolosi bloccati) |
| CAL | `calendar` | CALENDAR | Google Calendar CRUD — lista, crea, modifica, elimina eventi |
| GOALS | `goals` | OBJECTIVES | Obiettivi autonomi di Cipher (GoalManager) |
| INFO | `info` | SYSTEM INFO | Memoria, coscienza, stato emotivo, log azioni |

CSS: `.fan-menu` fixed top-right, `.fan-trigger` 120px con `logo.jpg`, `.fan-items` origin al centro trigger, `.fan-item` pill-shape (`border-radius: 20px`) posizionati su arco R=130px (0°→108°) con `translate(X,Y) translate(-50%,-50%)`. Anello decorativo `.fan-trigger::after` (260px, animato con `wheel-ring-in`).

### Popup system

`.hud-popup` con `.popup-bar` (drag header), `.popup-x` (close), `.view` (contenuto). `togglePopup(name)` toggle `.visible`. Drag via `popupDragStart()` su mousedown/touchstart: se il target è `.popup-x` esce immediatamente (guard `e.target.closest('.popup-x')`) — necessario su mobile per non bloccare il `click` sulla X con `e.preventDefault()`. `touchmove` handler chiama `e.preventDefault()` per bloccare lo scroll durante il drag. Z-index incrementale `_popupZ`.

### Media queries

| Breakpoint | Effetto chiave |
|---|---|
| ≤768px | Header compatto, info items nascosti |
| ≤680px | Chat input fisso sopra navbar; popup 90vw×60vh (no `!important` — drag inline style sovrascrive); trigger 90px, top:50px; wheel R=100px |
| ≤430px | Font 14px, touch target 44px, orb 80px, toast centrato sopra input |

### Vincoli UI

- Non spezzare in più file — tutto resta in `web/index.html`
- `#chat-input font-size: 16px` intenzionale — sotto 16px iOS Safari fa zoom su focus
- `.chat-input-row` è `position: fixed` su ≤680px — aggiornare `#chat-messages padding-bottom` se si cambia altezza input
- **Keyboard fix mobile**: `html` e `body` usano `height: 100%` (non `100vh`) + `interactive-widget=resizes-content` nel meta viewport (Android Chrome). Su iOS, il listener `visualViewport` (`resize`+`scroll`) gestisce il resize: sposta `.chat-input-row` con `transform: translateY(-kbH)` e riduce `#chat-messages` con `height` esplicita. `blur` chiama `resetKeyboard()` che azzera gli inline style. Il blocco JS è isolato in IIFE — non toccare senza capire l'intera interazione iOS/Android
- Toast su ≤430px a `bottom: 120px` — non abbassare senza verificare sovrapposizione con navbar+input
- Wheel item positions calcolate trigonometricamente — se si aggiungono/rimuovono item, ricalcolare angoli
- `_get_cal_service()` in server.py riusa il service di Brain se disponibile, altrimenti crea istanza GoogleCalendar
- Popup mobile: **mai** usare `!important` su `left`/`top` di `.hud-popup` — impedirebbe il drag via inline style. Il centramento su mobile è gestito in JS da `togglePopup()` (`left:12px; top:12.5vh`) quando `window.innerWidth <= 768`
- `touchmove` handler del popup drag deve avere `e.preventDefault()` — senza, il browser scrolla invece di trascinare
- Popup mobile CSS (`@media max-width:768px`): `width:calc(100vw-24px)`, `max-height:75vh`, `height:auto` con `!important`; `.hud-popup > .view` ha `overflow-y:auto; -webkit-overflow-scrolling:touch; min-height:0` per scroll interno mentre la `.popup-bar` rimane fissa. `.popup-x` 44×44px. `.emotion-grid`, `.alog-entry` collassano a 1 colonna. `.fi-actions` sempre visibili su touch
