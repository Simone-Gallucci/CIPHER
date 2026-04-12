# Cipher – AI Assistant

Cipher è un assistente AI personale con **presenza autonoma continua**.
Non aspetta di essere chiamato — esiste, riflette, agisce e impara anche quando non stai parlando con lui.

Alimentato da **Claude Sonnet** via **OpenRouter** (o Anthropic diretto). Gira come server su Linux (anche Raspberry Pi).

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
- **ConsciousnessLoop** — thread daemon con ciclo principale ogni 60 secondi:
  - Auto-riflessione ogni 10 minuti (throttlata a 30 minuti quando Simone è inattivo)
  - Generazione obiettivi autonomi ogni 20 minuti (azioni disponibili: `web_search`, `send_telegram`, `read_calendar`, `self_reflect`, `write_memory`)
  - Esecuzione obiettivi ogni 5 minuti
  - Check inattività → contatta Simone dopo 60 minuti con messaggio contestualizzato (ora, giorno, ultimi messaggi, eventi calendario prossime 2 ore) — mai frasi generiche o calchi dall'inglese; eventi già iniziati da >30 minuti esclusi dal contesto; nei giorni festivi italiani inietta un blocco esplicito che vieta riferimenti a lavoro, stage o scuola
  - Regola anti-confabulazione definita una sola volta in `comportamento/00_identity.txt`, vale per tutte le chiamate LLM — non duplicata nei singoli prompt
  - Flag `_proactive_pending` per evitare messaggi proattivi multipli in attesa di risposta
  - I messaggi proattivi vengono iniettati in history con il prefisso `[messaggio autonomo DD/MM HH:MM]:` — il LLM li distingue dalle risposte a domande dirette di Simone
- **6 stati emotivi**: `curious`, `content`, `bored`, `frustrated`, `protective`, `neutral`

### Memoria
- **Memoria episodica** — timeline strutturata degli eventi significativi
- **Profilo utente** — si aggiorna in tempo reale estraendo informazioni dalle conversazioni
- **Diario** (`thoughts.md`) — Cipher scrive i propri pensieri ad ogni riflessione
- **Log etica** (`ethics_log.md`) — traccia decisioni etiche e apprendimento
- **MemoryWorker** — processo separato (`cipher-memory.service`) che consolida la memoria leggendo le conversazioni e salvando i fatti rilevanti nel profilo

### Interessi propri
Cipher ha curiosità indipendenti da quelle dell'utente:
- **Innati**: psicologia, cybersecurity, programmazione/elettronica, astronomia, letteratura distopica/scientifica/filosofica/fantascientifica, film e serie, musica
- **Scoperti**: quando una ricerca web lo incuriosisce, aggiunge automaticamente il topic agli interessi
- Gli interessi scoperti crescono se esplorati, decadono se ignorati

### Proattività e Discrezionalità
- **DiscretionEngine** — decide autonomamente *quando* e *cosa* inviare:
  - Ore silenziose 23:00–07:00 (solo messaggi urgenti passano)
  - Anti-spam: max 2 notifiche/ora, max 6/giorno
  - Deprioritizza azioni con bassa efficacia storica
- **Briefing mattutino** — gestito da `ConsciousnessLoop._send_morning_brief()`, mai dallo scheduler (rimosso per evitare il doppio invio). Inviato nella finestra 7:00–8:00 con orario adattivo. Comportamento per scenario: compleanno (prompt dedicato), festività (auguri brevi, nessun riferimento a lavoro/scuola/stage anche se presenti nel calendario), giorno normale con eventi (agenda + pensiero notturno se disponibile), giorno normale senza eventi (solo pensiero notturno se disponibile). Tutti i prompt impongono il tu, tono WhatsApp, e divieto esplicito di inventare riferimenti a conversazioni passate. Il colorId `"11"` (Tomato) filtra gli eventi lavorativi/scolastici nei giorni festivi.
- **Digest serale** alle 20:00 — solo se ha qualcosa di rilevante da dire: agenda di domani e promemoria pendenti. Non include mai stati interni di Cipher (obiettivi autonomi, riflessioni, interessi)
- **Monitor passivo** ogni 10 minuti — scadenze calendario (solo dopo le 9:00, saltate nei festivi italiani per eventi professionali), notizie su argomenti di interesse
- **Meta-cognizione** — traccia l'impatto di ogni azione proattiva e impara cosa funziona. Feedback esplicito: dopo azioni significative non valutate, Cipher appende una domanda di feedback al prossimo check-in (max 1/giorno). Proattivi ignorati da > 90 minuti vengono marcati automaticamente come neutral. Il tempo di risposta di Simone viene registrato per ogni azione valutata.
- **`project_inspect`** — Cipher può analizzare le proprie modifiche recenti al codice: esegue `git diff` + `git log`, analizza il diff con Sonnet in linguaggio naturale. Usa un marker in `memory/last_project_check.txt` per mostrare solo le novità dall'ultima ispezione.

### Pattern e Anticipazione
- Impara quando e su cosa Simone interagisce (ora, giorno, argomento) — topic estratto via LLM con sampling 1/3 per ridurre overhead
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
# Crea e configura le API key:
nano .env
# Copia le credenziali Google OAuth2:
mkdir -p secrets
cp credentials.json secrets/
```

---

## Configurazione

### `.env` — variabili principali

| Variabile | Descrizione |
|---|---|
| `OPENROUTER_API_KEY` | **Obbligatorio** — da openrouter.ai/keys |
| `OPENROUTER_MODEL` | Default: `anthropic/claude-sonnet-4-6` |
| `LLM_PROVIDER` | `openrouter` (default) oppure `anthropic` (diretto) |
| `BACKGROUND_MODEL` | Modello per task silenziosi — default: `anthropic/claude-haiku-4-5` |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram (da @BotFather) |
| `TELEGRAM_ALLOWED_ID` | ID utente Telegram autorizzato |
| `CIPHER_SERVER_URL` | URL del server (default: `http://localhost:5000`) |
| `ELEVENLABS_API_KEY` | Per TTS voce |
| `ELEVENLABS_VOICE_ID` | ID voce ElevenLabs |
| `GREEN_API_INSTANCE_ID` | Instance ID Green API (WhatsApp) |
| `GREEN_API_TOKEN` | Token Green API (WhatsApp) |
| `VOSK_MODEL_PATH` | Percorso modello STT offline (italiano) |
| `VOSK_WAKE_MODEL_PATH` | Percorso modello STT per wake word (inglese) |
| `WAKE_WORDS` | Wake word separate da virgola (default: `cipher,jarvis,ehi,ci sei,ehi amico`) |
| `INPUT_MODE` | `text` / `voice` / `both` |
| `MIC_DEVICE_INDEX` | Indice dispositivo microfono (default: `-1` = auto) |
| `SILENCE_TIMEOUT` | Secondi di silenzio prima di chiudere l'ascolto (default: `2.0`) |
| `SERVER_PORT` | Porta server Flask (default: `5000`) |
| `CONSCIOUSNESS_ENABLED` | `true` (default) / `false` — disabilita il loop autonomo |
| `GOOGLE_CREDENTIALS_FILE` | Override percorso credentials Google (default: `secrets/credentials.json`) |
| `GOOGLE_TOKEN_FILE` | Override percorso token Google (default: `secrets/token.json`) |

### Comportamento

I file nella cartella `comportamento/` definiscono la personalità di Cipher.
Vengono letti in ordine alfabetico e concatenati nel system prompt, iniettato sia nelle chiamate conversazionali che in quelle background (`_call_llm_silent`).

```
comportamento/
├── 00_identity.txt      # Chi è Cipher, tono, carattere
├── azioni.txt           # Tutte le azioni disponibili (calendario, fs, shell, ecc.)
└── user_identity.txt    # Informazioni stabili su Simone (identità, lavoro, passioni, persone)

config/
└── dev_protocol.txt     # Regole sviluppo Cipher — caricato solo se Simone parla di codice/moduli
```

I file in `comportamento/` vengono letti **una sola volta all'avvio** e salvati in memoria. La cache delle parti dinamiche del system prompt si invalida ogni 5 minuti o subito dopo un aggiornamento di memoria. Per ricaricare i file statici senza restart: `brain.reload_static_prompt()`.

---

## Avvio

### Server (raccomandato — gestisce Telegram + API + coscienza)

```bash
# Con systemd (autostart)
sudo systemctl start cipher.service          # server Flask + coscienza autonoma
sudo systemctl start cipher-telegram.service # bot Telegram
sudo systemctl start cipher-memory.service   # consolidamento memoria
sudo systemctl start cipher-funnel.service   # esposizione via Tailscale

# Restart tutti
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service

# Log in tempo reale
sudo journalctl -u cipher -f
sudo journalctl -u cipher-telegram -f
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

Il server Flask ascolta su `127.0.0.1` (loopback) e viene esposto verso l'esterno tramite `cipher-funnel.service` (Tailscale).

| Metodo | Path | Descrizione |
|---|---|---|
| `GET` | `/health` | Stato del server e modello attivo |
| `POST` | `/chat` | `{"message": "..."}` → risposta di Cipher |
| `POST` | `/chat` | `{"message": "...", "image_b64": "...", "media_type": "image/jpeg"}` → risposta con analisi immagine |
| `GET` | `/memory` | Profilo utente corrente |
| `GET` | `/memory/interests` | Interessi autonomi di Cipher |
| `POST` | `/reset` | Resetta la conversazione |
| `POST` | `/stt` | Audio (body raw) → testo trascritto |
| `POST` | `/wake` | Audio → `{"detected": bool, "text": "..."}` wake word |
| `POST` | `/tts` | `{"text": "..."}` → audio MP3 |
| `GET` | `/consciousness/status` | Stato emotivo corrente |
| `GET` | `/consciousness/thoughts` | Ultimi pensieri da `thoughts.md` |
| `GET` | `/consciousness/goals` | Obiettivi attivi da `goals.md` |

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
- **Vocale/Audio** → trascritto via Vosk → risposta + risposta vocale (OGG)
- **Foto** → analizzata via Claude Vision → risposta
- **Documento** → salvato in `uploads/`, Cipher chiede cosa fare (o usa la caption come istruzione diretta)

### Esempi in linguaggio naturale

**Conversazione**
```
Ciao, come stai?
Che ora è?
Cerca su internet [argomento]
Resetta la conversazione
```

**Google Calendar**
```
Quali eventi ho oggi?
Cosa ho in agenda questa settimana?
Crea un evento domani alle 15 chiamato Riunione
Aggiungi un appuntamento il 25 marzo alle 10 chiamato Dentista
Crea un evento venerdì dalle 14 alle 16 chiamato Meeting con descrizione progetto X
```

**WhatsApp**
```
Manda un messaggio a mamma con scritto Ciao!
Scrivi a papà su WhatsApp: ci vediamo stasera?
Manda un messaggio a 393XXXXXXXXX con scritto Ciao!
```

**Contatti**
```
Mostra i miei contatti
Aggiungi contatto: zio Marco, WhatsApp 393XXXXXXXXX
Modifica contatto mamma, WhatsApp 393YYYYYYYYY
Rimuovi contatto zio Marco
```

**Filesystem (`home/`)**
```
Cosa c'è nella mia home?
Leggi il file todo.txt
Crea un file chiamato appunti.txt con scritto: riunione ore 15
Aggiungi al file todo.txt: comprare latte
Elimina il file vecchio.txt
Rinomina il file bozza.txt in finale.txt
Sposta il file note.txt nella cartella archivio
Crea una cartella chiamata progetti
```

**Memoria**
```
Ricorda che il mio numero preferito è 7
Dimentica [informazione]
Dimentica tutto
```

**Gestione script**
```
approva nomescript.py   → autorizza l'esecuzione
revoca nomescript.py    → revoca l'autorizzazione
lista script            → mostra tutti gli script nel registro
```

---

## Moduli

| Modulo | Responsabilità |
|---|---|
| `brain.py` | Core — LLM, memory, dispatcher azioni, prompt statico (caricato all'avvio), cache system prompt |
| `memory.py` | Profilo utente, conversazioni, contesto |
| `utils.py` | Utility condivise — parsing JSON da LLM, scritture JSON atomiche (thread/process-safe) |
| `consciousness_loop.py` | Loop autonomo — riflessione, obiettivi, esecuzione, check-in inattività |
| `self_reflection.py` | Auto-riflessione, stato emotivo (6 stati), diario |
| `goal_manager.py` | Generazione e gestione obiettivi autonomi |
| `ethics_engine.py` | Livelli di permesso, consenso, apprendimento autonomia |
| `discretion.py` | Decide quando e cosa inviare (ore silenziose, anti-spam, efficacia) |
| `episodic_memory.py` | Timeline eventi significativi con tag |
| `cipher_interests.py` | Interessi propri di Cipher, intensità, decay |
| `impact_tracker.py` | Traccia efficacia azioni proattive |
| `pattern_learner.py` | Pattern comportamentali, previsioni |
| `passive_monitor.py` | Monitor background — calendario, notizie |
| `realtime_context.py` | Contesto real-time — meteo, ora, dati ambientali |
| `night_cycle.py` | Elaborazione notturna alle 3:00 |
| `scheduler.py` | Digest serale 20:00, task ricorrenti (briefing rimosso — gestito da `consciousness_loop.py`) |
| `notifier.py` | Polling Telegram, timer, promemoria |
| `reminders.py` | Gestione promemoria e task schedulati |
| `actions.py` | Dispatcher tutte le azioni (web, cal, file, shell, `project_inspect`) — sistema consenso |
| `file_engine.py` | Lettura e analisi file (PDF, Excel, CSV) |
| `filesystem.py` | Operazioni filesystem sandboxate in `home/` |
| `script_registry.py` | Registro script con approvazione esplicita — gli script approvati con descrizione vengono inclusi nel system prompt |
| `google_auth.py` | OAuth2 Google Calendar — valida scope al boot, elimina token se non corrispondono |
| `google_cal.py` | Integrazione Google Calendar |
| `contacts.py` | Rubrica contatti — risolve nomi in numeri WhatsApp/ID Telegram |
| `listener.py` | STT offline (Vosk) + wake word |
| `voice.py` | TTS (ElevenLabs) |
| `whatsapp.py` | Integrazione WhatsApp via Green API |

---

## Struttura directory

```
cipher-server/
├── server.py
├── main.py
├── cipher_bot.py
├── memory_worker.py
├── config.py
├── requirements.txt
├── setup.sh
├── run.sh
├── .env
├── .gitignore
│
├── secrets/
│   ├── credentials.json
│   └── token.json
│
├── modules/
│   ├── brain.py
│   ├── memory.py
│   ├── utils.py
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
│   ├── realtime_context.py
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
│   ├── contacts.py
│   ├── listener.py
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
│   ├── emotional_log.json
│   ├── feedback_weights.json
│   ├── pattern_insights.md
│   ├── voice_notes.md
│   ├── screenshots.md
│   └── conversations/
│
├── comportamento/
│   ├── 00_identity.txt
│   ├── azioni.txt
│   └── user_identity.txt
├── config/
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
│   ├── programmazione_python.txt
│   ├── raspberry.txt
│   ├── unix.txt
│   └── win.txt
│
├── home/
│   ├── allowed_scripts.json
│   └── scripts/
│
├── uploads/
├── voices/
└── models/
    ├── vosk-model-it-0.22/
    ├── vosk-model-small-it-0.22/
    ├── vosk-model-en-us-0.22/
    └── vosk-model-small-en-us-0.15/
```

**File radice** — i cinque entry point principali: `server.py` avvia il server Flask e la coscienza autonoma; `cipher_bot.py` è il bot Telegram; `memory_worker.py` è il processo separato di consolidamento memoria (`cipher-memory.service`); `main.py` è la CLI locale; `config.py` è l'unica fonte di verità per path e valori, letta da tutti i moduli.

**`secrets/`** — credenziali OAuth2 Google (`credentials.json`, `token.json`). Non versionato. I path di default puntano qui; si possono sovrascrivere con `GOOGLE_CREDENTIALS_FILE` e `GOOGLE_TOKEN_FILE` nel `.env`.

**`modules/`** — tutti i moduli Python del progetto. Vedere la tabella [Moduli](#moduli) per la descrizione di ciascuno.

**`memory/`** — dati persistenti generati a runtime: profilo utente, stato emotivo, interessi, episodi, obiettivi, pattern, autonomia etica, diario delle riflessioni, sessioni di conversazione. Non versionato.

**`comportamento/`** — system prompt di Cipher. I file vengono letti in ordine alfabetico e concatenati; modificare questi file per cambiare personalità, tono o azioni disponibili senza toccare il codice.

**`apprendimento/`** — base di conoscenza appresa da ricerche web, organizzata per dominio (arduino, cybersec, linguaggi di programmazione, raspberry, unix, ecc.).

**`home/`** — sandbox filesystem: unica directory dove Cipher può scrivere file ed eseguire script. `allowed_scripts.json` contiene il registro degli script approvati. Non versionato.

**`uploads/`** — file ricevuti via Telegram o WhatsApp. Non versionato.

**`voices/`** — audio TTS generati da ElevenLabs. Non versionato.

**`models/`** — modelli Vosk per STT offline. Non versionato (pesanti, si riscaricano con `setup.sh`).
