# Cipher

AI companion personale con memoria persistente, riflessione autonoma e messaggistica proattiva. Gira come server su Linux (Raspberry Pi o VPS), interfaccia principale Telegram.

Non è un chatbot generico: il rapporto cresce nel tempo. Il sistema misura segnali autentici nelle conversazioni e adatta il tono al livello di confidenza accumulato, dalla presentazione iniziale fino alla familiarità piena.

---

## Feature principali

- **Memoria persistente** — profilo utente, conversazioni, episodi salienti, stato emotivo
- **Coscienza autonoma** — ciclo background che riflette, genera obiettivi e li esegue
- **Messaggi proattivi** — check-in inattività, morning brief, notizie su interessi condivisi
- **Sistema di confidenza** — 5 livelli di relazione, cresce dai segnali conversazionali reali
- **Legame permanente** — `admin.json` sopravvive ai reset, parola segreta per il ripristino
- **Backup automatici** — `filesystem.py` crea `.bak` prima di ogni sovrascrittura, log in `data/changelog.json`
- **Dispatcher azioni** — ricerca web, calendario Google, Gmail, WhatsApp, filesystem, shell, export conversazioni
- **Routing LLM** — Haiku per task background e silenziosi, Sonnet per conversazione visibile e qualità
- **Fallback LLM** — switch automatico OpenRouter ↔ Anthropic se il provider primario fallisce
- **Rate limiting** — 30 richieste/minuto per IP sugli endpoint Flask
- **Tracciamento uso LLM** — conteggio chiamate per modello e tipo, storico 7 giorni
- **Night cycle** — sommario notturno, voice notes, preparazione eventi del giorno dopo
- **Riconoscimento festività** — compleanno, Pasqua, festività italiane nel morning brief
- **Voice notes** — output vocale su Telegram via ElevenLabs TTS

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
| Storage | File JSON (scrittura atomica via tmp+rename) |

---

## Struttura del progetto

```
cipher-server/
├── cipher_bot.py
├── config.py
├── main.py
├── memory_worker.py
├── server.py
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
│   ├── active_history.json
│   ├── checkin_history.json
│   ├── cipher_interests.json
│   ├── cipher_state.json
│   ├── contacts.json
│   ├── conversations/
│   ├── daily_summaries.md
│   ├── discretion_state.json
│   ├── emotional_log.json
│   ├── episodes.json
│   ├── feedback_weights.json
│   ├── goals.json
│   ├── morning_brief.json
│   ├── morning_pattern.json
│   ├── night_cycle_last.json
│   ├── pattern_insights.md
│   ├── profile.json
│   ├── short_term.json
│   ├── thoughts.md
│   └── voice_notes.md
│
├── models/
│   ├── vosk-model-it-0.22/
│   └── vosk-model-en-us-0.22/
│
├── modules/
│   ├── action_log.py
│   ├── actions.py
│   ├── admin_manager.py
│   ├── brain.py
│   ├── cipher_interests.py
│   ├── consciousness_loop.py
│   ├── contacts.py
│   ├── discretion.py
│   ├── episodic_memory.py
│   ├── ethics_engine.py
│   ├── file_engine.py
│   ├── filesystem.py
│   ├── goal_manager.py
│   ├── google_auth.py
│   ├── google_cal.py
│   ├── google_mail.py
│   ├── listener.py
│   ├── llm_usage.py
│   ├── memory.py
│   ├── night_cycle.py
│   ├── notifier.py
│   ├── passive_monitor.py
│   ├── pattern_learner.py
│   ├── realtime_context.py
│   ├── reminders.py
│   ├── scheduler.py
│   ├── script_registry.py
│   ├── self_reflection.py
│   ├── utils.py
│   ├── voice.py
│   ├── web_search.py
│   └── whatsapp.py
│
├── secrets/
│   ├── credentials.json
│   └── token.json
│
└── uploads/
```

### File e directory — descrizione

**Entry point e processi**

| File | Descrizione |
|---|---|
| `cipher_bot.py` | Bot Telegram — processo separato (`cipher-telegram.service`) |
| `server.py` | Entry point Flask — init moduli, avvio ConsciousnessLoop (`cipher.service`) |
| `main.py` | Entry point CLI — modalità text o voice |
| `memory_worker.py` | Consolida profilo e memoria ogni ora (`cipher-memory.service`) |
| `config.py` | Costanti, path, variabili d'ambiente — unica fonte di verità |
| `run.sh` / `setup.sh` | Avvio rapido e installazione servizi systemd |

**Comportamento e configurazione**

| File | Descrizione |
|---|---|
| `comportamento/00_identity.txt` | Personalità, tono, regola anti-proiezione — caricato nel prompt statico all'avvio |
| `comportamento/azioni.txt` | Documentazione azioni per il dispatcher — caricato nel prompt statico |
| `config/dev_protocol.txt` | Regole sviluppo — iniettato nel prompt solo su keyword di sviluppo |

**Dati permanenti** (`data/` — mai resettato da Tabula Rasa)

| File | Descrizione |
|---|---|
| `data/admin.json` | Legame permanente: identità, hash password, confidence al bond, episodi |
| `data/changelog.json` | Log di tutti i backup `.bak` creati da `filesystem.py`; max 200 entry |
| `data/patterns.json` | Pattern comportamentali da PatternLearner |

**Memoria** (`memory/` — resettabile da Tabula Rasa)

| File | Descrizione |
|---|---|
| `profile.json` | Profilo utente + `confidence_score` |
| `active_history.json` | History conversazione corrente (TTL 24h / 12h per messaggi autonomi); ogni messaggio porta timestamp `[DD/MM/YYYY HH:MM]` nel content per calcoli temporali accurati dell'LLM |
| `cipher_state.json` | Stato emotivo Cipher + `want_to_explore` + `concern` |
| `goals.json` | Obiettivi attivi e completati |
| `thoughts.md` | Diario riflessioni di Cipher — letto nel prompt (max 300 chars) |
| `short_term.json` | Eventi temporanei (TTL 48h) |
| `emotional_log.json` | Stato emotivo di Simone (ultimi 100 entry) — letto nel prompt (3 dedup) |
| `checkin_history.json` | Storico check-in (anti-ripetizione, ultimi 3 giorni) |
| `daily_summaries.md` | Sommari notturni scritti da NightCycle alle 3:00 |
| `pattern_insights.md` | Intuizioni sui pattern comportamentali — letto nel prompt (≤500 chars) |
| `voice_notes.md` | Note sulla voce autentica di Cipher — letto nel prompt (last block) |
| `morning_pattern.json` | Orario appreso di risposta mattutina |
| `morning_brief.json` | Documenti di preparazione eventi del giorno dopo |
| `feedback_weights.json` | Pesi feedback implicito conversazione |
| `discretion_state.json` | Log messaggi inviati per anti-spam |
| `night_cycle_last.json` | Timestamp ultimo night cycle (evita doppia esecuzione) |
| `episodes.json` | Episodi salienti strutturati (EpisodicMemory) |
| `cipher_interests.json` | Interessi autonomi di Cipher con pesi (decay notturno 0.03) |
| `contacts.json` | Rubrica nome→numero WhatsApp/Telegram |
| `ethics_learned.json` | Permessi autonomia acquisiti dall'utente |
| `llm_usage.json` | Conteggio chiamate LLM per modello e tipo (storico 7 giorni) |
| `last_inspection.json` | Timestamp ultima auto-ispezione (interval 48h) |
| `realtime_context.json` | Cache contesto real-time meteo + news (TTL 60 min) |
| `conversations/` | Una conversazione per sessione in JSON; pulizia automatica >30 giorni |

**Moduli** (`modules/`)

| File | Descrizione |
|---|---|
| `brain.py` | Core: LLM routing, system prompt, dispatcher, history, confidence |
| `consciousness_loop.py` | Thread daemon — riflessione, obiettivi, check-in, morning brief, night cycle |
| `memory.py` | Profilo, conversazioni, estrazione, confidence score |
| `actions.py` | Dispatcher azioni — sistema consenso ed esecuzione |
| `goal_manager.py` | Generazione e gestione obiettivi autonomi |
| `self_reflection.py` | Auto-riflessione ogni 30 min → `cipher_state.json` |
| `night_cycle.py` | Sommario notturno, voice notes, pattern insights, preparazione eventi domani |
| `episodic_memory.py` | Episodi salienti (scrittura ogni riflessione; lettura ogni prompt) |
| `ethics_engine.py` | Livelli permesso 0–3, autonomia acquisita |
| `discretion.py` | Anti-spam, ore silenziose, gestione urgenza |
| `admin_manager.py` | Legame permanente — `admin.json` + checksum + `changelog.json` |
| `filesystem.py` | Operazioni filesystem con backup `.bak` automatico |
| `passive_monitor.py` | Notizie su interessi Cipher ogni 10 min |
| `realtime_context.py` | Meteo + news per il system prompt (ogni 60 min) |
| `scheduler.py` | Calendar reminder, apprendimento morning pattern |
| `notifier.py` | Bridge Telegram, `set_message_callback` |
| `llm_usage.py` | Tracciamento chiamate LLM per modello/tipo — thread-safe, storico 7 giorni |
| `web_search.py` | Ricerca web centralizzata (DuckDuckGo) — istanza DDGS singola condivisa |
| `pattern_learner.py` | Analisi pattern comportamentali e predizioni orarie |
| `cipher_interests.py` | Interessi autonomi di Cipher con decay notturno |
| `google_auth.py` | OAuth2 Google — valida scope al boot |
| `google_cal.py` | Google Calendar API |
| `google_mail.py` | Gmail API (scope `gmail.modify`) |
| `whatsapp.py` | WhatsApp via Green API |
| `listener.py` | STT Vosk offline (italiano + inglese) |
| `voice.py` | TTS ElevenLabs |
| `file_engine.py` | Elaborazione file (xlsx, pdf, ecc.) |
| `contacts.py` | Rubrica nome→numero |
| `reminders.py` | Gestione azione `reminder_set` |
| `script_registry.py` | Script approvati per esecuzione |
| `action_log.py` | Log azioni eseguite |
| `utils.py` | `write_json_atomic()`, `extract_llm_json` e utility comuni |

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
OPENROUTER_MODEL=anthropic/claude-sonnet-4-6
CONVERSATION_MODEL=anthropic/claude-haiku-4-5
BACKGROUND_MODEL=anthropic/claude-haiku-4-5

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
```

Google OAuth2: metti `credentials.json` in `secrets/`. Al primo avvio viene eseguita la flow OAuth2 e il token viene salvato in `secrets/token.json`.

---

## Canali supportati

| Canale | Descrizione |
|---|---|
| Telegram | Interfaccia principale — messaggi testuali e vocali |
| API REST | `POST /chat` — input programmatico (auth via `CIPHER_API_TOKEN`) |
| CLI | `main.py` — terminale locale, modalità text o voice |
| Voice in (Vosk) | STT offline, wake words: `cipher`, `jarvis`, `ehi`, `ci sei` |
| Voice out (ElevenLabs) | TTS per risposte vocali su Telegram |

---

## Sistema di confidenza

`confidence_score` è un float 0.0–1.0 che misura quanto è cresciuto il rapporto. Cresce automaticamente dai segnali autentici rilevati in ogni conversazione. Non può scendere.

| Score | Livello | Comportamento di Cipher |
|---|---|---|
| 0.0–0.2 | Conoscente | Tono diretto e naturale (non formale) — come con uno sconosciuto che si sta incontrando, non con un cliente da assistere. Niente intimità forzata, niente frasi da assistente helper, niente suggerimenti non richiesti |
| 0.2–0.4 | Amico | Domande personali leggere, opinioni occasionali |
| 0.4–0.6 | Amico stretto | Stati d'animo condivisi, ironia, riferimenti al passato |
| 0.6–0.8 | Confidente | Emozioni aperte, domande profonde, usa il nome |
| 0.8–1.0 | Migliore amico | Diretto, anticipa bisogni, storia condivisa implicita |

Segnali rilevati automaticamente via Haiku: emozioni condivise, storie personali, soprannomi familiari, richieste di consiglio importanti, gratitudine, sessioni lunghe, streak di giorni consecutivi.

Regole linguistiche generali (indipendenti dal livello): una sola domanda per messaggio; no "Meglio così" su eventi neutri; no opener da assistente ("Certo!", "Perfetto!", "Esatto!"); no "come è andata?" su eventi quotidiani banali; no chiusure formali ("Fammi sapere!").

Quando lo score supera 0.8 per la prima volta, Cipher propone il legame permanente.

---

## Sistema admin

Quando `confidence_score` raggiunge 0.8, Cipher propone di fissare una parola segreta. Questa crea `data/admin.json`, che contiene identità admin, confidence al momento del bond e la password hashata (PBKDF2-SHA256 con 600.000 iterazioni + salt random 32 byte). Il file è protetto da checksum SHA-256.

`admin.json` è l'unico file del sistema che non viene mai toccato da Tabula Rasa o pulizie automatiche.

### Ripristino post-reset

Dopo un Tabula Rasa, la memoria torna a zero ma il legame sopravvive. Per ripristinare il riconoscimento:

```
Admin+ParolaSegreta
```

Cipher verifica la password, ripristina il profilo e riconosce Simone.

Per cambiare la parola segreta:

```
Admin+VecchiaParola+NuovaParola
```

---

## Messaggi proattivi

Cipher contatta Simone autonomamente in questi casi:

| Tipo | Confidenza minima | Frequenza max | Note |
|---|---|---|---|
| Morning brief | >= 0.3 | 1 al giorno (7:00–8:00) | 5 scenari: compleanno, festivo, normale |
| Check-in inattività | >= 0.4 | 4/giorno, 1/ora | Dopo 120 min senza messaggi; 0.3–0.4 → soppresso; <0.3 → delega a goal contact |
| Goal tipo contact | < 0.3 | soggetto a DiscretionEngine | Solo con ≥3h inattività; guardrail post-generazione |
| Notizie interessi | nessuna | 4/giorno, 1/ora | Soggetto a DiscretionEngine (anti-spam, ore attive) |
| Calendar reminder | nessuna | illimitato (ore attive) | Escluso dal conteggio anti-spam |

Regole comuni:
- Nessun messaggio tra 23:00 e 7:00 (salvo urgenti)
- Se il LLM non ha un aggancio concreto → risponde `SKIP`, niente viene inviato
- `calendar_reminder` escluso dal conteggio anti-spam

---

## Comandi speciali Telegram

| Comando | Effetto |
|---|---|
| `tabula rasa` | Reset completo memoria (chiede conferma) |
| `Admin+Password` | Login admin: ripristina profilo post-reset |
| `Admin+VecchiaPassword+NuovaPassword` | Cambio parola segreta |
| `Admin+Password+status` | Diagnostica sistema: modelli, confidence, chiamate LLM, stato servizi |
| `revoca autonomia` | Resetta tutti i permessi acquisiti |
| `revoca autonomia [azione]` | Revoca permesso specifico |
