# Cipher — AI friend, not assistant

Cipher è un AI personale con **presenza autonoma continua**.
Non aspetta di essere chiamato — esiste, riflette, agisce e impara anche quando non stai parlando con lui.

Alimentato da **Claude Sonnet** via **OpenRouter** (o Anthropic diretto). Gira come server su Linux (anche Raspberry Pi). Interfaccia principale: Telegram.

---

## Filosofia

Cipher non è un assistente — è un amico.

Non risponde solo alle domande. Non riempie il silenzio con frasi generiche. Non proietta preoccupazioni passate su messaggi semplici. Scrive solo quando ha qualcosa di concreto da dire.

Il rapporto cresce nel tempo: all'inizio Cipher non sa niente di Simone e lo impara dalle conversazioni, come si conosce una persona nuova. Col tempo il tono si fa più vicino, ci sono più riferimenti condivisi, più complicità. Non è una progressione artificiale — emerge dalle conversazioni reali.

---

## Funzionalità

### Conversazione
- Risponde via Telegram (testo, vocale, foto, documenti)
- Memoria persistente della conversazione e del profilo utente
- Topic closure automatico: quando un argomento è risolto, non torna più nel contesto
- Google Calendar integrato
- Web search con DuckDuckGo
- Lettura e analisi file (PDF, Excel, CSV, testo)
- Text-to-Speech via ElevenLabs, Speech-to-Text offline via Vosk
- WhatsApp via Green API

### Autonomia
- **ConsciousnessLoop** — thread daemon con ciclo ogni ~60 secondi:
  - Auto-riflessione ogni 30 minuti (scrive su `thoughts.md`)
  - Generazione e esecuzione obiettivi autonomi
  - Check inattività → contatta Simone dopo 120 minuti con messaggio contestualizzato
  - Morning brief tra le 7:00 e le 8:00 (adattato a festività, compleanno, calendario)
  - Monitor passivo: notizie su argomenti di interesse, scadenze calendario
  - Night cycle alle 3:00 (consolidamento, sommario, decay interessi, pulizia 30gg)
- **6 stati emotivi**: `curious`, `content`, `bored`, `frustrated`, `protective`, `neutral`
- **Interessi propri**: Cipher ha curiosità indipendenti (cybersecurity, AI, filosofia, ecc.) che crescono o decadono nel tempo

### Memoria
- **Profilo utente** — si aggiorna in tempo reale estraendo informazioni dalle conversazioni
- **Short-term** — eventi/piani temporanei con TTL 48h
- **Active history** — sessione corrente con TTL 24h (12h per messaggi autonomi), max 20 messaggi
- **Episodic memory** — timeline strutturata degli eventi significativi
- **MemoryWorker** — processo separato che consolida la memoria dalle conversazioni
- **Filtro keyword chiuse** — argomenti risolti vengono esclusi automaticamente dal contesto futuro

### Discrezionalità
- Ore silenziose 23:00–07:00: solo messaggi urgenti
- Anti-spam: max 1 notifica/ora, max 4/giorno (i calendar reminder sono esclusi dal conteggio)
- Distanza minima tra messaggi non urgenti: 120 minuti
- Messaggi proattivi iniettati in history con prefisso `[messaggio autonomo DD/MM HH:MM]`

### Sicurezza e Etica
- Azioni sensibili richiedono conferma esplicita
- Dopo 3 approvazioni manuali → autonomia acquisita per quell'azione
- Scrittura file consentita solo dentro `home/`
- Script sandboxati (limiti CPU/RAM/file)

---

## Architettura

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
│  Memoria Episodica · Interessi                  │
│  Monitor Passivo · Ciclo Notturno               │
└─────────────────────────────────────────────────┘
```

### Moduli

| Modulo | Descrizione |
|---|---|
| `brain.py` | Core — LLM, memory, dispatcher azioni, prompt statico (una volta all'avvio), cache system prompt |
| `memory.py` | Profilo utente, conversazioni, contesto, filtro keyword chiuse |
| `utils.py` | Utility — parsing JSON da LLM, scritture JSON atomiche thread-safe |
| `consciousness_loop.py` | Loop autonomo — riflessione, obiettivi, check-in, morning brief, night cycle |
| `self_reflection.py` | Auto-riflessione, stato emotivo (6 stati), deduplicazione pensieri |
| `goal_manager.py` | Generazione e gestione obiettivi autonomi |
| `ethics_engine.py` | Livelli di permesso, consenso, autonomia acquisita |
| `discretion.py` | Decide quando e cosa inviare (ore silenziose, anti-spam, urgenza) |
| `episodic_memory.py` | Timeline eventi significativi con tag |
| `cipher_interests.py` | Interessi propri di Cipher, intensità, decay |
| `impact_tracker.py` | **Stub no-op** (disabilitato — incompatibile con filosofia amico) |
| `pattern_learner.py` | **Stub no-op** (disabilitato — Cipher impara tramite conversazione) |
| `passive_monitor.py` | Monitor background — notizie su interessi, scadenze calendario |
| `realtime_context.py` | Contesto real-time — meteo, ora, dati ambientali |
| `night_cycle.py` | Elaborazione notturna alle 3:00 (sommario, decay, pulizia, preparazione domani) |
| `scheduler.py` | Digest serale 20:00, task ricorrenti |
| `notifier.py` | Polling Telegram, timer, promemoria |
| `reminders.py` | Gestione promemoria e task schedulati |
| `actions.py` | Dispatcher azioni (web, calendar, file, shell, project_inspect) — sistema consenso |
| `file_engine.py` | Lettura e analisi file (PDF, Excel, CSV) |
| `filesystem.py` | Operazioni filesystem sandboxate in `home/` |
| `script_registry.py` | Registro script con approvazione esplicita |
| `google_auth.py` | OAuth2 Google Calendar — valida scope al boot |
| `google_cal.py` | Integrazione Google Calendar |
| `contacts.py` | Rubrica — risolve nomi in numeri WhatsApp/ID Telegram |
| `listener.py` | STT offline (Vosk) + wake word |
| `voice.py` | TTS (ElevenLabs) |
| `whatsapp.py` | WhatsApp via Green API |

---

## Requisiti

- Python 3.13+
- Linux (Ubuntu/Debian, anche Raspberry Pi)
- Telegram Bot Token (da @BotFather)
- OpenRouter API key con accesso a Claude Sonnet + Haiku
- Google Calendar credentials (OAuth2)
- Green API (WhatsApp) — opzionale

---

## Configurazione

### `.env` — variabili principali

| Variabile | Descrizione |
|---|---|
| `OPENROUTER_API_KEY` | **Obbligatorio** |
| `OPENROUTER_MODEL` | Default: `anthropic/claude-sonnet-4-6` |
| `LLM_PROVIDER` | `openrouter` (default) oppure `anthropic` |
| `BACKGROUND_MODEL` | Default: `anthropic/claude-haiku-4-5` |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `TELEGRAM_ALLOWED_ID` | ID utente Telegram autorizzato |
| `ELEVENLABS_API_KEY` | Per TTS voce |
| `ELEVENLABS_VOICE_ID` | ID voce ElevenLabs |
| `GREEN_API_INSTANCE_ID` | Instance ID Green API (WhatsApp) |
| `GREEN_API_TOKEN` | Token Green API (WhatsApp) |
| `CONSCIOUSNESS_ENABLED` | `true` (default) / `false` |

### Comportamento (`comportamento/`)

```
comportamento/
├── 00_identity.txt   ← personalità, tono, regola anti-proiezione, filosofia amico
└── azioni.txt        ← tutte le azioni disponibili (calendario, fs, shell, ecc.)
```

I file vengono letti in ordine alfabetico **una sola volta all'avvio**. Per ricaricarli senza restart: `brain.reload_static_prompt()`.

⚠️ Non mettere file `.bak` o `.old` in questa cartella — vengono inclusi nel system prompt.

### Dev protocol (`config/dev_protocol.txt`)

Caricato **condizionalmente**: solo se negli ultimi 5 messaggi compare una keyword di sviluppo (`modifica`, `bug`, `fix`, `codice`, `brain.py`, ecc.). Non appesantisce il prompt nelle conversazioni normali.

---

## Servizi systemd

| Servizio | Descrizione |
|---|---|
| `cipher.service` | Server Flask + coscienza autonoma |
| `cipher-telegram.service` | Bot Telegram |
| `cipher-memory.service` | Memory worker (consolidamento memoria) |
| `cipher-funnel.service` | Esposizione via Tailscale |

```bash
# Restart standard
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service

# Log live
sudo journalctl -u cipher.service -f

# Stato
sudo systemctl status cipher.service cipher-telegram.service cipher-memory.service
```

---

## Struttura cartelle

```
cipher-server/
├── server.py, cipher_bot.py, memory_worker.py, main.py, config.py
├── comportamento/     ← 2 file: personalità + azioni
├── config/            ← dev_protocol.txt
├── memory/            ← stato persistente runtime (non versionato)
├── apprendimento/     ← conoscenze da web per dominio
├── modules/           ← tutti i moduli Python
├── secrets/           ← credenziali Google OAuth2 (non versionato)
├── home/              ← sandbox filesystem utente
├── uploads/           ← file ricevuti via Telegram
└── venv/              ← virtual environment
```

---

## Autore

Simone Gallucci — [galluccisimone.it](https://galluccisimone.it)
