# Cipher

AI companion con memoria persistente, riflessione autonoma e messaggistica proattiva. Gira come server su Linux (Raspberry Pi o VPS), interfaccia principale Telegram.

Non è un chatbot generico: il rapporto cresce nel tempo. Il sistema misura segnali autentici nelle conversazioni e adatta il tono al livello di confidenza accumulato, dalla presentazione iniziale fino alla familiarità piena. Chiunque interagisca viene trattato allo stesso modo — la relazione si costruisce dai comportamenti reali, non da nomi dichiarati.

---

## Feature principali

- **Memoria persistente** — profilo utente, conversazioni, episodi salienti, stato emotivo, short-term events (TTL 48h)
- **Coscienza autonoma** — thread daemon con riflessione ogni 30 min, generazione obiettivi ogni 20 min, esecuzione ogni 5 min
- **Messaggi proattivi** — check-in inattività, morning brief adattivo, notizie su interessi condivisi
- **Sistema di confidenza** — 5 livelli di relazione (0.0–1.0), cresce dai segnali conversazionali reali, non può scendere
- **Legame permanente** — `admin.json` sopravvive a qualsiasi reset; parola segreta per il ripristino post-Tabula Rasa
- **Dispatcher azioni** — web search, calendario Google, Gmail, WhatsApp, filesystem, shell, file processing, export
- **Routing LLM** — Sonnet per conversazione e messaggi visibili; Haiku per task background silenziosi
- **Fallback LLM** — switch automatico OpenRouter ↔ Anthropic diretto se il provider primario fallisce
- **Multimodale** — supporto immagini nel `/chat` endpoint (base64)
- **Rate limiting** — 30 richieste/minuto per IP su tutti gli endpoint tranne `/health` e `/web`
- **Dashboard web** — JARVIS HUD (`/web`) con chat sempre visibile, wheel selector, popup flottanti draggabili (FS, BASH, CAL, GOALS, INFO); polling automatico
- **Tracciamento uso LLM** — conteggio chiamate per modello e tipo, storico 7 giorni in `memory/llm_usage.json`
- **Night cycle** — sommario notturno, voice notes, pattern insights, preparazione eventi domani (ogni notte alle 3:00)
- **Riconoscimento festività italiane** — compleanno, Pasqua, festività fisse nel morning brief
- **Voice I/O** — STT Vosk offline (italiano + inglese); TTS ElevenLabs su Telegram
- **Qualità conversazionale** — Cipher è consapevole del tempo passato tra le conversazioni: se sono passate 8+ ore, riconosce il gap e riprende il filo dalla conversazione precedente. Domande contestuali (mai buttate lì dal nulla). Risposte riempitive ("Classico!", "Top!", ecc.) vietate. Check-in proattivi tengono conto dell'ultimo stato emotivo rilevato.

---

## Stack tecnico

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.11+, Flask |
| LLM | Claude (Haiku / Sonnet) via OpenRouter o Anthropic diretto |
| Telegram | python-telegram-bot |
| Calendario | Google Calendar API (OAuth2) |
| Email | Gmail API (OAuth2, scope `gmail.modify`) |
| WhatsApp | Green API |
| STT | Vosk (offline, modelli italiano + inglese) |
| TTS | ElevenLabs API |
| Web search | DuckDuckGo (`ddgs`) |
| Web rendering | Playwright (per SPA e pagine JS-heavy) |
| File processing | pymupdf (fitz), openpyxl, pandas, python-docx, python-pptx, Pillow, ezdxf |
| Storage | File JSON (scrittura atomica via tmp+rename) |

---

## Struttura del progetto

```
cipher-server/
├── cipher_bot.py
├── server.py
├── main.py
├── memory_worker.py
├── config.py
├── run.sh
├── setup.sh
├── requirements.txt
│
├── comportamento/
│   ├── 00_identity.txt
│   └── azioni.txt
│
├── config/
│   └── dev_protocol.txt
│
├── data/
│   ├── admin.json
│   ├── changelog.json
│   └── patterns.json
│
├── home/
│   ├── allowed_scripts.json
│   └── scripts/
│
├── memory/
│   ├── profile.json
│   ├── active_history.json
│   ├── cipher_state.json
│   ├── goals.json
│   ├── goals.md
│   ├── thoughts.md
│   ├── short_term.json
│   ├── emotional_log.json
│   ├── checkin_history.json
│   ├── daily_summaries.md
│   ├── pattern_insights.md
│   ├── voice_notes.md
│   ├── morning_pattern.json
│   ├── morning_brief.json
│   ├── feedback_weights.json
│   ├── discretion_state.json
│   ├── night_cycle_last.json
│   ├── episodes.json
│   ├── cipher_interests.json
│   ├── contacts.json
│   ├── ethics_learned.json
│   ├── ethics_log.md
│   ├── llm_usage.json
│   ├── last_project_check.txt
│   ├── realtime_context.json
│   ├── memory_worker_state.json
│   ├── action_log.json
│   └── conversations/
│
├── models/
│   ├── vosk-model-it-0.22/
│   ├── vosk-model-en-us-0.22/
│   ├── vosk-model-small-it-0.22/
│   └── vosk-model-small-en-us-0.15/
│
├── modules/
│
├── secrets/
│   ├── credentials.json
│   └── token.json
│
├── uploads/
└── web/
    ├── index.html
    └── static/
        └── logo.jpg
```

### File e directory — descrizione

**Entry point e processi**

| File | Descrizione |
|---|---|
| `server.py` | Entry point Flask — init moduli, avvio ConsciousnessLoop (`cipher.service`) |
| `cipher_bot.py` | Bot Telegram — processo separato (`cipher-telegram.service`) |
| `main.py` | Entry point CLI — modalità text o voice |
| `memory_worker.py` | Consolida profilo e memoria ogni ora (`cipher-memory.service`) |
| `config.py` | Costanti, path, variabili d'ambiente — unica fonte di verità |
| `run.sh` / `setup.sh` | Avvio rapido e installazione servizi systemd |

**Comportamento e configurazione**

| File | Descrizione |
|---|---|
| `comportamento/00_identity.txt` | Personalità, tono, regole, comandi speciali, ascolto emotivo (incl. frasi vietate per stati negativi) — caricato nel prompt statico all'avvio |
| `comportamento/azioni.txt` | Documentazione azioni disponibili per il dispatcher |
| `config/dev_protocol.txt` | Regole sviluppo — iniettato nel prompt solo su keyword di sviluppo (ultimi 5 messaggi) |

**Dati permanenti** (`data/` — mai resettato da Tabula Rasa)

| File | Descrizione |
|---|---|
| `data/admin.json` | Legame permanente: identità, hash password (PBKDF2-SHA256), confidence al bond, episodi, patterns |
| `data/changelog.json` | Log di tutti i backup `.bak` creati da `filesystem.py`; max 200 entry |
| `data/patterns.json` | Pattern comportamentali da PatternLearner |

**Memoria** (`memory/` — resettabile da Tabula Rasa)

| File | Descrizione |
|---|---|
| `profile.json` | Profilo utente + `confidence_score` + `confidence_history` (ultimi 20 segnali) |
| `active_history.json` | History sessione corrente (max 20 messaggi, TTL 24h / 12h per messaggi autonomi); ogni messaggio porta timestamp `[DD/MM/YYYY HH:MM]` nel content |
| `cipher_state.json` | Stato emotivo Cipher + `want_to_explore` + `concern` — scritto da SelfReflection ogni 30 min |
| `goals.json` | Obiettivi attivi e completati (GoalManager) |
| `goals.md` | Storico testuale obiettivi completati |
| `thoughts.md` | Diario riflessioni di Cipher — letto nel prompt (max 300 chars, ultimo blocco) |
| `short_term.json` | Eventi temporanei (TTL 48h) |
| `emotional_log.json` | Stato emotivo di Simone (ultimi 100 entry) — letto nel prompt (3 dedup + nota follow-up se ultimo stato negativo); classifier usa contesto delle ultime 2 battute |
| `checkin_history.json` | Storico check-in (anti-ripetizione, ultimi 3 giorni) |
| `daily_summaries.md` | Sommari notturni scritti da NightCycle alle 3:00 |
| `pattern_insights.md` | Intuizioni sui pattern comportamentali (≤500 chars) — letto nel prompt |
| `voice_notes.md` | Note sulla voce autentica di Cipher — letto nel prompt (ultimo blocco) |
| `morning_pattern.json` | Orario appreso di risposta mattutina (media mobile, min 3 campioni) |
| `morning_brief.json` | Preparazione eventi domani — scritto da NightCycle, letto da morning brief |
| `feedback_weights.json` | Pesi feedback implicito conversazione |
| `discretion_state.json` | Log messaggi inviati per anti-spam (`MAX_PER_HOUR=1`, `MAX_PER_DAY=4`) |
| `night_cycle_last.json` | Timestamp ultimo night cycle (evita doppia esecuzione) |
| `episodes.json` | Episodi salienti strutturati (EpisodicMemory) |
| `cipher_interests.json` | Interessi autonomi di Cipher con pesi (decay notturno 0.03) |
| `contacts.json` | Rubrica nome→numero WhatsApp/Telegram |
| `ethics_learned.json` | Permessi autonomia acquisiti dall'utente |
| `ethics_log.md` | Log decisioni etiche |
| `llm_usage.json` | Conteggio chiamate LLM per modello e tipo (storico 7 giorni) |
| `last_project_check.txt` | Hash HEAD dell'ultimo `project_inspect` |
| `realtime_context.json` | Cache meteo + news (TTL 60 min) |
| `action_log.json` | Log azioni eseguite con timestamp e source |
| `memory_worker_state.json` | Stato ultimo run del memory worker |
| `conversations/` | Una conversazione per sessione in JSON; pulizia automatica >30 giorni |

**Moduli** (`modules/`)

| File | Descrizione |
|---|---|
| `brain.py` | Core: LLM routing, system prompt dinamico, dispatcher, history, confidence, admin |
| `pre_action_layer.py` | Dati verificati in tempo reale (calendario+email) — iniettati nel prompt bypassing la TTL cache |
| `consciousness_loop.py` | Thread daemon — riflessione, obiettivi, check-in, morning brief, night cycle |
| `memory.py` | Profilo, conversazioni, estrazione, confidence score, short-term events |
| `actions.py` | Dispatcher azioni — sistema consenso, 30+ tipi di azione, lazy loaders |
| `goal_manager.py` | Generazione e gestione obiettivi autonomi (5 tipi: explore, protect, task, observe, reflect; max 3 attivi) |
| `self_reflection.py` | Auto-riflessione ogni 30 min → `cipher_state.json` |
| `night_cycle.py` | Sommario notturno, voice notes, pattern insights, preparazione eventi domani |
| `episodic_memory.py` | Episodi salienti — scrittura ogni riflessione; recall query-based nel prompt |
| `ethics_engine.py` | Livelli permesso autonomia 0–3, permessi acquisiti |
| `discretion.py` | Anti-spam, ore silenziose (23:00–7:00), gestione urgenza |
| `admin_manager.py` | Legame permanente — `admin.json` + checksum SHA-256 + `changelog.json` |
| `filesystem.py` | Operazioni filesystem con backup `.bak` su sovrascrittura |
| `passive_monitor.py` | Notizie su interessi Cipher ogni 10 min |
| `realtime_context.py` | Meteo + news per il system prompt (ogni 60 min) |
| `scheduler.py` | Calendar reminder, apprendimento morning pattern |
| `notifier.py` | Bridge Telegram, `set_message_callback` |
| `llm_usage.py` | Tracciamento chiamate LLM per modello/tipo — thread-safe, storico 7 giorni |
| `web_search.py` | Ricerca web centralizzata (DuckDuckGo) — istanza DDGS singola condivisa |
| `pattern_learner.py` | Pattern comportamentali, predizioni orarie, ore mai attive |
| `cipher_interests.py` | Interessi autonomi di Cipher con decay notturno 0.03 |
| `google_auth.py` | OAuth2 Google — valida scope al boot, elimina token non corrispondente |
| `google_cal.py` | Google Calendar API |
| `google_mail.py` | Gmail API (scope `gmail.modify`) — solo su richiesta utente esplicita |
| `whatsapp.py` | WhatsApp via Green API |
| `listener.py` | STT Vosk offline (italiano + inglese) |
| `voice.py` | TTS ElevenLabs |
| `file_engine.py` | Elaborazione file (xlsx, pdf, ecc.) |
| `contacts.py` | Rubrica nome→numero WhatsApp/Telegram |
| `reminders.py` | Gestione azione `reminder_set` |
| `action_log.py` | Log azioni eseguite |
| `utils.py` | `write_json_atomic()`, `extract_llm_json`, utility comuni |

**Secrets** (`secrets/` — non versionato)

| File | Descrizione |
|---|---|
| `credentials.json` | Credenziali OAuth2 Google — input manuale, mai toccato dal codice |
| `token.json` | Token OAuth2 generato al primo avvio; eliminato e rigenerato se scope cambia |

---

## Installazione

```bash
# 1. Clona il repository
git clone <repo> cipher-server && cd cipher-server

# 2. Crea il virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Installa le dipendenze
pip install -r requirements.txt

# 4. Copia e configura il file .env
cp .env.example .env
nano .env

# 5. (Opzionale) Scarica modelli Vosk per input vocale
# italiano: vosk-model-it-0.22  — https://alphacephei.com/vosk/models
# inglese:  vosk-model-en-us-0.22
# Estrai entrambi in models/

# 6. Esegui setup servizi systemd
sudo bash setup.sh

# 7. Avvia
sudo systemctl start cipher.service cipher-telegram.service cipher-memory.service
```

---

## Configurazione

Variabili principali in `.env`:

```env
# LLM
LLM_PROVIDER=openrouter             # openrouter | anthropic
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-sonnet-4-6   # modello principale (Sonnet)
CONVERSATION_MODEL=anthropic/claude-haiku-4-5  # default Haiku (config, non usato nel routing attuale)
BACKGROUND_MODEL=anthropic/claude-haiku-4-5    # Haiku per task silenziosi
OPUS_MODEL=anthropic/claude-opus-4-6           # Opus per auto-ispezione keyword-triggered

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_ID=...             # chat_id autorizzato

# ElevenLabs TTS (opzionale)
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb

# WhatsApp via Green API (opzionale)
GREEN_API_INSTANCE_ID=...
GREEN_API_TOKEN=...

# Compleanno utente (per morning brief)
BIRTHDAY_DAY=0
BIRTHDAY_MONTH=0

# Modalità input
INPUT_MODE=text                     # text | voice | both

# Sicurezza API Flask (vuoto = auth disabilitata)
CIPHER_API_TOKEN=

# Feature flags
CONSCIOUSNESS_ENABLED=true
```

Google OAuth2: metti `credentials.json` in `secrets/`. Al primo avvio viene eseguita la flow OAuth2 e il token viene salvato in `secrets/token.json`.

---

## API Flask

| Endpoint | Metodo | Auth | Descrizione |
|---|---|---|---|
| `/health` | GET | No | Health check + diagnostica (modelli, confidence, LLM calls today) |
| `/chat` | POST | Sì | Messaggio testuale + immagine opzionale (base64) + `source` opzionale → risposta Cipher |
| `/memory` | GET | Sì | Profilo utente corrente |
| `/memory/interests` | GET | Sì | Interessi autonomi di Cipher |
| `/reset` | POST | Sì | Reset history conversazione (memoria intatta) |
| `/stt` | POST | Sì | Speech-to-text da audio PCM 16000 Hz |
| `/wake` | POST | Sì | Detect wake word → `{detected: true/false}` |
| `/tts` | POST | Sì | Text-to-speech via ElevenLabs |
| `/consciousness/status` | GET | Sì | Status coscienza + stato emotivo |
| `/consciousness/thoughts` | GET | Sì | Ultimi pensieri da `thoughts.md` |
| `/consciousness/goals` | GET | Sì | Obiettivi attivi |
| `/api/dashboard` | GET | Sì | Dati aggregati dashboard (profilo, obiettivi, calendario, emotional log, action log) |
| `/api/history` | GET | Sì | History conversazione corrente (JSON) |
| `/api/files` | GET | Sì | Lista file in `home/` (param: `path`) |
| `/api/files` | DELETE | Sì | Elimina file/cartella in `home/` (param: `path`) |
| `/api/files/read` | GET | Sì | Legge file in `home/` (param: `path`) |
| `/api/files/write` | POST | Sì | Scrive/aggiorna file in `home/` (body: `path`, `content`, `append`) |
| `/api/files/upload` | POST | Sì | Upload file in `home/` (max 10 MB, form: `file`, `path`) |
| `/api/files/download` | GET | Sì | Download file da `home/` (param: `path`) |
| `/api/files/mkdir` | POST | Sì | Crea cartella in `home/` (body: `path`) |
| `/api/terminal` | POST | Sì | Esegue comando shell in `home/` (body: `cmd`, timeout 10s, output max 50 KB) |
| `/api/calendar` | GET | Sì | Lista eventi Google Calendar (?days=7, ?q=search) |
| `/api/calendar` | POST | Sì | Crea evento (body: `title`, `start`, `end?`, `description?`, `location?`) |
| `/api/calendar/<id>` | PUT | Sì | Modifica evento |
| `/api/calendar/<id>` | DELETE | Sì | Elimina evento |
| `/api/notes` | GET | Sì | Legge `home/notes.md` (legacy) |
| `/api/notes` | POST | Sì | Salva `home/notes.md` (legacy) |

Rate limit: 30 richieste/minuto per IP (eccetto `/health` e `/web`). Auth via header `X-Cipher-Token`.

---

## Canali supportati

| Canale | Descrizione |
|---|---|
| Telegram | Interfaccia principale — messaggi testuali, vocali e immagini |
| Dashboard web | `GET /web` — JARVIS HUD con chat sempre visibile; wheel selector (5 label: FS, BASH, CAL, GOALS, INFO) apre popup flottanti draggabili |
| API REST | `POST /chat` — input programmatico |
| CLI | `main.py` — terminale locale, modalità text o voice |
| Voice in (Vosk) | STT offline, wake words: `cipher`, `jarvis`, `ehi`, `ci sei`, `ehi amico` |
| Voice out (ElevenLabs) | TTS per risposte vocali su Telegram e dashboard web |

---

## Primo avvio / Onboarding utente

Quando `profile.json` è vuoto (utente nuovo), Cipher entra automaticamente in **modalità ONBOARDING**. La condizione è `confidence_score == 0.0` AND `profile.personal.nome` assente — nessun flag separato, la source of truth è `profile.json`.

In questa modalità il system prompt contiene istruzioni specifiche:
- Non fare domande generiche di cortesia ("come va?", "come stai?") che presuppongono familiarità non ancora costruita
- Presentarsi brevemente (chi è, non cosa può fare), poi chiedere il nome in modo naturale
- Una domanda alla volta: nome → città → lavoro/studio → aspettative
- Tono curioso e diretto, non da assistente

Non appena `memory.py` salva il nome dell'utente in `profile.json`, la modalità onboarding si disattiva automaticamente al messaggio successivo. I messaggi proattivi (check-in, morning brief, notizie) rimangono bloccati durante l'onboarding perché richiedono tutti `confidence >= 0.3` o `>= 0.4`.

---

## Sistema di confidenza

`confidence_score` è un float 0.0–1.0 che misura quanto è cresciuto il rapporto. Cresce automaticamente dai segnali conversazionali rilevati via Haiku ad ogni messaggio. Non può scendere.

| Score | Livello | Comportamento di Cipher |
|---|---|---|
| 0.0–0.2 | Conoscente | Tono diretto e naturale — come con uno sconosciuto che si sta incontrando, non un cliente da assistere. No intimità forzata, no frasi da assistente, no suggerimenti non richiesti |
| 0.2–0.4 | Amico | Una domanda personale leggera per sessione, opinioni occasionali |
| 0.4–0.6 | Amico stretto | Pensieri e stati d'animo condivisi, ironia, riferimenti a cose passate |
| 0.6–0.8 | Confidente | Emozioni aperte, domande profonde, usa il nome o soprannomi |
| 0.8–1.0 | Migliore amico | Diretto, anticipa bisogni, storia condivisa implicita |

**Segnali che fanno salire il punteggio** (rilevati da Haiku ad ogni messaggio):

| Segnale | Delta |
|---|---|
| `personal_story` — racconta qualcosa di personale | +0.020 |
| `advice_request` — chiede consiglio su qualcosa di importante | +0.030 |
| `nickname_joke` — soprannome familiare o scherzo affettuoso | +0.025 |
| `emotion_shared` — condivide un'emozione esplicitamente | +0.015 |
| `gratitude` — ringrazia o esprime apprezzamento | +0.010 |
| `long_session` — sessione >10 turni (una volta per sessione) | +0.010 |
| `daily_streak` — giorni consecutivi di conversazione | +0.005 |

Regole linguistiche generali (tutte le fasce): una sola domanda per messaggio; no "Meglio così" su eventi neutri; no opener da assistente ("Certo!", "Perfetto!", "Esatto!"); no "come è andata?" su eventi quotidiani banali; no chiusure formali.

Quando lo score supera 0.8 per la prima volta, Cipher propone il legame permanente.

---

## Sistema admin

Quando `confidence_score` raggiunge 0.8, Cipher propone di fissare una parola segreta. Questa crea `data/admin.json` con identità admin, confidence al momento del bond, e password hashata (PBKDF2-SHA256, 600.000 iterazioni, salt random 32 byte). Il file è protetto da checksum SHA-256.

`admin.json` e `changelog.json` (in `data/`) sono gli unici file del sistema che non vengono mai toccati da Tabula Rasa o pulizie automatiche.

### Ripristino post-reset

Dopo un Tabula Rasa la memoria torna a zero, ma il legame sopravvive:

```
Admin+ParolaSegreta
```

Cipher verifica la password, ripristina il profilo (nome, età, residenza, lavoro, confidence, episodi, patterns) e riconosce Simone.

Per cambiare la parola segreta:

```
Admin+VecchiaParola+NuovaParola
```

Lockout: 3 tentativi falliti → blocco temporaneo 10 minuti.

---

## Messaggi proattivi

Cipher contatta Simone autonomamente in questi casi:

| Tipo | Confidenza minima | Frequenza max | Note |
|---|---|---|---|
| Morning brief | >= 0.3 | 1 al giorno (7:00–8:00) | 5 scenari: compleanno, festivo+eventi, festivo, normale+eventi, normale |
| Check-in inattività | >= 0.4 | 4/giorno, 1/ora | Dopo 120 min senza messaggi; 0.3–0.4 → soppresso; <0.3 → non inviato |
| Goal tipo `contact` | < 0.3 | soggetto a DiscretionEngine | Solo con ≥3h inattività; guardrail triplo post-generazione |
| Notizie interessi | nessuna | 4/giorno, 1/ora | Soggetto a DiscretionEngine (anti-spam, ore attive) |
| Calendar reminder | nessuna | illimitato (ore attive) | Escluso dal conteggio anti-spam |

Regole comuni:
- Nessun messaggio tra 23:00 e 7:00 (urgenza "urgent" passa tranne 01:00–06:00)
- Se il LLM non ha un aggancio concreto → risponde `SKIP`, niente viene inviato
- `calendar_reminder` escluso dal conteggio anti-spam
- Check-in soppresso anche con confidence >= 0.4 se: ultima interazione prima delle 7:00, ora mai attiva nei pattern, evento calendario attivo

---

## ConsciousnessLoop — task periodici

Thread daemon, ciclo ogni ~60 secondi:

| Task | Intervallo | Note |
|---|---|---|
| Check inattività | continuo | Soglia 120 min; guardie multiple |
| Refresh contesto real-time | ogni 60 min | Meteo + news |
| Morning brief | finestra 7:00–8:00 | 1/giorno; confidence >= 0.3 |
| Auto-riflessione | ogni 30 min | ×2 se Simone inattivo >2h |
| Generazione obiettivi | ogni 20 min | Haiku; max 3 goal attivi |
| Esecuzione obiettivi | ogni 5 min | Richiede confidence >= 0.4 |
| Auto-ispezione | keyword-triggered | Simone chiede idee → Brain chiama `trigger_self_inspection()` in thread; usa Opus 4.6 |
| Pulizia obiettivi scaduti | ogni ciclo | Max age 24h |

Ogni task è wrappato in `_run_with_timeout()` con timeout fisso — un task bloccato non ferma il loop.

---

## Comandi speciali Telegram

| Comando | Effetto |
|---|---|
| `tabula rasa` | Reset completo memoria (chiede conferma) |
| `Admin+Password` | Login admin: ripristina profilo post-reset |
| `Admin+VecchiaPassword+NuovaPassword` | Cambio parola segreta |
| `Admin+Password+status` | Diagnostica sistema: modelli, confidence, chiamate LLM, goals, stato servizi |
| `revoca autonomia` | Resetta tutti i permessi acquisiti (chiede conferma) |
| `revoca autonomia [azione]` | Revoca permesso specifico |

---

## Servizi systemd

```bash
# Restart completo
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service

# Log in tempo reale
sudo journalctl -u cipher.service -f

# Stato tutti i servizi
sudo systemctl status cipher.service cipher-telegram.service cipher-funnel.service cipher-memory.service
```

| Servizio | Descrizione |
|---|---|
| `cipher.service` | Server Flask + ConsciousnessLoop |
| `cipher-telegram.service` | Bot Telegram (`cipher_bot.py`) |
| `cipher-memory.service` | Memory worker — consolida profilo ogni ora |
| `cipher-funnel.service` | Esposizione via Tailscale |

---

## Dashboard web

La dashboard (`web/index.html`) è un file HTML monolitico (~91 KB) con CSS e JS inline, tema JARVIS HUD rosso/cyan.

**Architettura UI:**
- **Chat sempre visibile** come base layer (text + voice mode)
- **Wheel selector** in alto a destra: icona circolare 120px (`logo.jpg`), click apre 5 label a pillola su arco circolare con anello decorativo
- **Popup flottanti**: ogni sezione si apre come finestra draggabile sopra la chat; più popup aperti contemporaneamente

**Sezioni (wheel selector):**

| Label | Funzione |
|---|---|
| FS | File Manager — naviga `~/cipher/home/`, upload/download/elimina |
| BASH | Terminale sandbox (timeout 10s) |
| CAL | Google Calendar — lista, crea, modifica, elimina eventi |
| GOALS | Obiettivi autonomi di Cipher |
| INFO | Memoria, coscienza, stato emotivo, log azioni |

**Responsive:** breakpoint a 768px (header compatto + popup mobile), 680px (input fisso, wheel trigger 90px), 430px (font 14px, touch target 44px). Drag funziona anche su mobile (touch events con preventDefault per bloccare lo scroll).

**Popup su mobile (≤768px):** larghezza `calc(100vw-24px)`, altezza `auto` con `max-height: 75vh`, centrati automaticamente all'apertura da JS (`togglePopup`). Scroll interno nella `.view` con `-webkit-overflow-scrolling: touch`. Bottone ✕ 44×44px. Grids interne (`.emotion-grid`, `.alog-entry`) collassate a 1 colonna. Azioni file sempre visibili. `!important` non usato su `left`/`top` per preservare il drag.

**Tastiera virtuale mobile:** `html`/`body`/`#content` usano `height: 100%` (non `100vh`). Meta viewport include `interactive-widget=resizes-content` (Android Chrome). Su iOS, un listener `visualViewport` (`resize`+`scroll`) solleva `.chat-input-row` con `transform: translateY(-kbH)` e riduce `#chat-messages` a `vvp.height - header - tabs - input - 16px`. Il `blur` sull'input resetta entrambi gli inline style. Lo scroll automatico a fine chat si attiva su `focus` (delay 300ms per attendere apertura tastiera).
