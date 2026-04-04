# Cipher – AI Assistant

Cipher è un assistente AI personale con **presenza autonoma continua**.
Non aspetta di essere chiamato — esiste, riflette, agisce e impara anche quando non stai parlando con lui.

Alimentato da **Claude Sonnet** via **OpenRouter**. Gira come server su Linux (anche Raspberry Pi).

Per l'accesso remoto da PC o Android usa [`cipher-client`](../cipher-client).

---

## Indice

- [Architettura](#architettura)
- [Funzionalità](#funzionalità)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Avvio](#avvio)
- [API Server](#api-server)
- [Comandi Telegram](#comandi-telegram)
- [Moduli](#moduli)
- [Struttura directory](#struttura-directory)

---

## Architettura

Cipher è composto da tre strati:

```
┌─────────────────────────────────────────────────┐
│  INTERFACCE                                     │
│  Telegram Bot │ API Flask │ CLI (testo/voce)    │
│  WhatsApp (Green API)                           │
└────────────────────────┬────────────────────────┘
                         │
┌────────────────────────▼────────────────────────┐
│  BRAIN                                          │
│  LLM (OpenRouter/Claude) + Memory + Dispatcher  │
└────────────────────────┬────────────────────────┘
                         │
┌────────────────────────▼────────────────────────┐
│  COSCIENZA AUTONOMA (thread daemon)             │
│  Riflessione · Obiettivi · Discrezionalità      │
│  Memoria Episodica · Interessi · Pattern        │
│  Monitor Passivo · Ciclo Notturno               │
└─────────────────────────────────────────────────┘
```

---

## Funzionalità

### Conversazione
- Risponde via Telegram, WhatsApp, API REST, CLI testo e microfono
- Memoria persistente della conversazione e del profilo utente
- Integrazione Google Calendar
- Web search con DuckDuckGo
- Lettura e analisi file (PDF, Excel, CSV, testo)
- Text-to-Speech via ElevenLabs
- Speech-to-Text offline via Vosk

### Autonomia e Coscienza
- **ConsciousnessLoop** — thread daemon che gira ogni 60 secondi:
  - Auto-riflessione ogni 10 minuti → aggiorna stato emotivo
  - Generazione obiettivi autonomi ogni 20 minuti (azioni disponibili: `web_search`, `send_telegram`, `read_calendar`, `self_reflect`, `write_memory`)
  - Esecuzione obiettivi ogni 5 minuti
  - Check inattività → contatta l'utente dopo 30 minuti
- **7 stati emotivi**: curious, content, bored, frustrated, protective, neutral

### Memoria
- **Memoria episodica** — timeline strutturata degli eventi significativi
- **Profilo utente** — si aggiorna in tempo reale estraendo informazioni dalle conversazioni
- **Diario** (`thoughts.md`) — Cipher scrive i propri pensieri ad ogni riflessione
- **Log etica** (`ethics_log.md`) — traccia decisioni etiche e apprendimento

### Interessi propri
Cipher ha curiosità indipendenti da quelle dell'utente:
- **Innati**: psicologia, cybersecurity, programmazione/elettronica, astronomia, letteratura distopica/scientifica/filosofica/fantascientifica, film e serie, musica
- **Scoperti**: quando una ricerca web lo incuriosisce, aggiunge automaticamente il topic agli interessi
- Gli interessi scoperti crescono se esplorati, decadono se ignorati

### Proattività e Discrezionalità
- **DiscretionEngine** — decide autonomamente *quando* e *cosa* inviare:
  - Ore silenziose 23:00–07:00 (solo urgenti passano)
  - Anti-spam: max 3 notifiche/ora, max 12/giorno
  - Deprioritizza azioni con bassa efficacia storica
- **Briefing mattutino** alle 7:30 — messaggio naturale generato via LLM con pensiero notturno e agenda del giorno (niente liste o sezioni fisse)
- **Digest serale** alle 20:00 — solo se ha qualcosa di rilevante da dire
- **Monitor passivo** ogni 10 minuti — scadenze calendario (solo dopo le 9:00, saltate nei festivi italiani per eventi professionali), notizie su argomenti di interesse
- **Meta-cognizione** — traccia l'impatto di ogni azione proattiva e impara cosa funziona

### Pattern e Anticipazione
- Impara quando e su cosa l'utente interagisce (ora, giorno, argomento)
- Genera previsioni per anticipare i bisogni prima che vengano espressi

### Etica e Sicurezza
- **EthicsEngine** — 4 livelli di permesso per ogni azione
- Azioni sensibili richiedono consenso esplicito
- Dopo 3 approvazioni manuali → autonomia acquisita per quell'azione
- Esecuzione script sandboxata (limiti CPU/RAM/file, ambiente isolato)
- Scrittura file consentita solo dentro `home/`

---

## Installazione

```bash
git clone <repo>
cd cipher-server
chmod +x setup.sh && ./setup.sh
nano .env   # crea e configura le API key
```

---

## Configurazione

### `.env` — variabili principali

| Variabile | Descrizione |
|---|---|
| `OPENROUTER_API_KEY` | **Obbligatorio** — da openrouter.ai/keys |
| `OPENROUTER_MODEL` | Default: `anthropic/claude-sonnet-4-6` |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram (da @BotFather) |
| `TELEGRAM_ALLOWED_ID` | ID utente Telegram autorizzato |
| `CIPHER_SERVER_URL` | URL del server (default: `http://100.127.57.5:5000`) |
| `ELEVENLABS_API_KEY` | Per TTS voce |
| `ELEVENLABS_VOICE_ID` | ID voce ElevenLabs |
| `GREEN_API_INSTANCE_ID` | Instance ID Green API (WhatsApp) |
| `GREEN_API_TOKEN` | Token Green API (WhatsApp) |
| `VOSK_MODEL_PATH` | Percorso modello STT offline |
| `VOSK_WAKE_MODEL_PATH` | Percorso modello STT per wake word (inglese) |
| `WAKE_WORDS` | Wake word separate da virgola (default: `cipher,jarvis,ehi,...`) |
| `INPUT_MODE` | `text` / `voice` / `both` |
| `MIC_DEVICE_INDEX` | Indice dispositivo microfono (default: `-1` = auto) |
| `SILENCE_TIMEOUT` | Secondi di silenzio prima di chiudere l'ascolto (default: `2.0`) |
| `SERVER_PORT` | Porta server Flask (default: `5000`) |

### Comportamento

I file nella cartella `comportamento/` definiscono la personalità di Cipher.
Vengono letti in ordine alfabetico e concatenati nel system prompt.

```
comportamento/
├── 00_identity.txt      # Chi è Cipher, tono, carattere
├── azioni.md            # Azioni web (web_fetch, web_explore_spa, ecc.)
├── azioni.txt           # Tutte le azioni disponibili (calendario, fs, shell, ecc.)
└── dev_protocol.txt     # Regole operative
```

Quando Simone chiede a Cipher "che obiettivi hai?", "che programmi hai?" o simili, Cipher elenca gli obiettivi attivi dal proprio contesto con una frase descrittiva per ciascuno. Se non ne ha, lo dice e aggiunge cosa vorrebbe esplorare.

---

## Avvio

### Server (raccomandato — gestisce Telegram + API)

```bash
# Con systemd (autostart)
sudo systemctl start cipher.service
sudo systemctl start cipher-telegram.service
sudo systemctl start cipher-funnel.service   # espone via Tailscale

# Manuale
source venv/bin/activate
python server.py
```

### CLI locale

```bash
./run.sh                             # menu interattivo
source venv/bin/activate
python main.py --mode text           # solo tastiera
python main.py --mode voice          # solo microfono
python main.py --mode both           # tastiera + microfono
python main.py --mode text --no-tts  # testo senza audio
```

**Comandi in-chat:**
```
resetta              → cancella la storia della sessione
dimentica tutto      → cancella memoria + profilo
ricorda che [fatto]  → salva manualmente un fatto
esci / spegni        → spegne Cipher
```

---

## API Server

Il server Flask espone queste route:

| Metodo | Path | Descrizione |
|---|---|---|
| `GET` | `/health` | Stato del server e modello attivo |
| `POST` | `/chat` | `{"message": "..."}` → risposta di Cipher |
| `GET` | `/memory` | Profilo utente corrente |
| `POST` | `/reset` | Resetta la conversazione |
| `POST` | `/stt` | Audio (body raw) → testo trascritto |
| `POST` | `/wake` | Audio → `{"detected": bool}` wake word |
| `POST` | `/tts` | `{"text": "..."}` → audio MP3 |
| `GET` | `/consciousness/status` | Stato emotivo corrente |
| `GET` | `/consciousness/thoughts` | Ultimi pensieri da `thoughts.md` |
| `GET` | `/consciousness/goals` | Obiettivi attivi |

---

## Comandi Telegram

| Comando | Descrizione |
|---|---|
| `/start` | Attiva il bot |
| `/reset` | Resetta la conversazione |
| `/stato` | Stato emotivo e obiettivi attivi |
| `/pensieri` | Ultimi pensieri autonomi |
| `/obiettivi` | Lista obiettivi attivi |

**Tipi di messaggio supportati:**
- **Testo** → risposta diretta
- **Vocale/Audio** → trascritto via Vosk → risposta
- **Foto** → analizzata via Claude Vision → risposta
- **Documento** → salvato in `uploads/`, Cipher chiede cosa fare

**Gestione script:**
```
approva nomescript.py   → autorizza l'esecuzione
revoca nomescript.py    → revoca l'autorizzazione
lista script            → mostra tutti gli script nel registro
```

---

## Moduli

| Modulo | Responsabilità |
|---|---|
| `brain.py` | Core — LLM, memory, dispatcher azioni |
| `memory.py` | Profilo utente, conversazioni, contesto |
| `consciousness_loop.py` | Loop autonomo — riflessione, obiettivi, esecuzione |
| `self_reflection.py` | Auto-riflessione, stato emotivo, diario |
| `goal_manager.py` | Generazione e gestione obiettivi autonomi |
| `ethics_engine.py` | Livelli di permesso, consenso, apprendimento |
| `discretion.py` | Decide quando e cosa inviare (ore, spam, efficacia) |
| `episodic_memory.py` | Timeline eventi significativi con tag |
| `cipher_interests.py` | Interessi propri di Cipher, intensità, decay |
| `impact_tracker.py` | Traccia efficacia azioni proattive |
| `pattern_learner.py` | Pattern comportamentali, previsioni |
| `passive_monitor.py` | Monitor background — calendario, email, notizie |
| `night_cycle.py` | Elaborazione notturna alle 3:00 |
| `scheduler.py` | Briefing 7:30, digest 20:30, task ricorrenti |
| `notifier.py` | Polling Telegram, timer, promemoria |
| `reminders.py` | Gestione promemoria e task schedulati |
| `actions.py` | Dispatcher tutte le azioni (web, cal, mail, file, shell) |
| `file_engine.py` | Lettura e analisi file (PDF, Excel, CSV) |
| `filesystem.py` | Operazioni filesystem sandboxate in `home/` |
| `script_registry.py` | Registro script con approvazione |
| `google_auth.py` | OAuth2 Google (Calendar + Gmail) |
| `google_cal.py` | Integrazione Google Calendar |
| `google_mail.py` | Integrazione Gmail |
| `listener.py` | STT offline (Vosk) + wake word |
| `text_input.py` | Input da tastiera (CLI) |
| `voice.py` | TTS (ElevenLabs) |
| `whatsapp.py` | Integrazione WhatsApp via Green API |

---

## Struttura directory

**File radice:** `server.py` entry point Flask, `main.py` entry point CLI, `cipher_bot.py` bot Telegram, `config.py` configurazione centralizzata, `credentials.json` / `token.json` OAuth2 Google.

**`modules/`** — tutti i moduli Python. `brain.py` core LLM/memory/dispatcher; `consciousness_loop.py` loop autonomo; `self_reflection.py` stato emotivo e diario; `goal_manager.py` obiettivi; `ethics_engine.py` permessi e consenso; `discretion.py` decide quando/cosa inviare; `episodic_memory.py` timeline eventi; `cipher_interests.py` interessi propri con decay; `impact_tracker.py` efficacia azioni; `pattern_learner.py` previsioni comportamentali; `passive_monitor.py` monitor background cal/mail/news; `night_cycle.py` elaborazione notturna alle 3:00; `scheduler.py` briefing 7:30 e digest 20:30; `actions.py` dispatcher web/cal/mail/fs/shell; `file_engine.py` lettura PDF/Excel/CSV; `filesystem.py` operazioni fs sandboxate; `google_auth/cal/mail.py` integrazione Google; `listener.py` STT Vosk + wake word; `voice.py` TTS ElevenLabs; `whatsapp.py` Green API.

**`memory/`** — dati persistenti generati a runtime: profilo utente, stato emotivo, interessi, episodi, obiettivi, pattern, autonomia etica, diario riflessioni, sessioni conversazione.

**`comportamento/`** — system prompt di Cipher: identità e tono (`00_identity.txt`), azioni disponibili (`azioni.txt`, `azioni.md`), regole operative (`dev_protocol.txt`). Letti in ordine alfabetico e concatenati.

**`apprendimento/`** — base di conoscenza appresa: arduino, cybersec, programmazione (C/C++/Java/Flutter/HTML/Python/AL), raspberry, unix, win, carte.

**`home/`** — sandbox: unica directory dove Cipher può scrivere file ed eseguire script. **`uploads/`** — file ricevuti via Telegram/WhatsApp. **`voices/`** — audio TTS generati. **`models/`** — modelli Vosk STT.

```
cipher-server/
├── server.py
├── main.py
├── cipher_bot.py
├── config.py
├── requirements.txt
├── setup.sh / run.sh / run_server.sh
├── .env
├── .gitignore
├── credentials.json
├── token.json
│
├── modules/
│   ├── brain.py
│   ├── memory.py
│   ├── consciousness_loop.py
│   ├── self_reflection.py
│   ├── goal_manager.py
│   ├── ethics_engine.py
│   ├── discretion.py
│   ├── episodic_memory.py
│   ├── cipher_interests.py
│   ├── impact_tracker.py
│   ├── pattern_learner.py
│   ├── passive_monitor.py
│   ├── night_cycle.py
│   ├── scheduler.py
│   ├── notifier.py
│   ├── reminders.py
│   ├── actions.py
│   ├── file_engine.py
│   ├── filesystem.py
│   ├── script_registry.py
│   ├── google_auth.py
│   ├── google_cal.py
│   ├── google_mail.py
│   ├── listener.py
│   ├── text_input.py
│   ├── voice.py
│   └── whatsapp.py
│
├── memory/
│   ├── profile.json
│   ├── cipher_state.json
│   ├── cipher_interests.json
│   ├── episodes.json
│   ├── goals.json
│   ├── goals.md
│   ├── patterns.json
│   ├── ethics_learned.json
│   ├── ethics_log.md
│   ├── thoughts.md
│   ├── screenshots.md
│   └── conversations/
│
├── comportamento/
│   ├── 00_identity.txt
│   ├── azioni.md
│   ├── azioni.txt
│   └── dev_protocol.txt
│
├── apprendimento/
│   ├── arduino.txt
│   ├── carte.txt
│   ├── cybersec.txt
│   ├── programmazione_AL.txt
│   ├── programmazione_c.txt
│   ├── programmazione_cpp.txt
│   ├── programmazione_flutter.txt
│   ├── programmazione_html.txt
│   ├── programmazione_java.txt
│   ├── programmazione_paython.txt
│   ├── raspberry.txt
│   ├── unix.txt
│   └── win.txt
│
├── home/
│   ├── allowed_scripts.json
│   ├── backup_sd.sh
│   ├── mkdir.sh
│   ├── show_cipher.sh
│   ├── touch.sh
│   └── scripts/
│       └── test.sh
│
├── uploads/
├── voices/
└── models/
    ├── vosk-model-it-0.22/
    ├── vosk-model-small-it-0.22/
    ├── vosk-model-en-us-0.22/
    └── vosk-model-small-en-us-0.15/
```
