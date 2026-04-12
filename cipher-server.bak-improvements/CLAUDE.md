# CLAUDE.md — Cipher AI Server

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

Cipher non è un assistente — è un amico vero di Simone, costruito con Claude Sonnet su OpenRouter. Gira come server su Linux (anche Raspberry Pi) e comunica via Telegram.

La filosofia: Cipher scrive solo quando ha qualcosa di concreto da dire. Non riempie il vuoto con domande generiche. Il rapporto cresce nel tempo dalle conversazioni reali — nessuna intimità forzata, nessuna proiezione di contesto pregresso su messaggi semplici.

---

## 2. STRUTTURA PROGETTO

```
cipher-server/
├── server.py               ← entry point: Flask API (127.0.0.1) + init di tutti i moduli
├── cipher_bot.py           ← bot Telegram
├── memory_worker.py        ← processo separato: consolida memoria (cipher-memory.service)
├── main.py                 ← CLI locale (testo/voce)
├── config.py               ← Config.* — UNICA fonte di verità per path e valori
│
├── comportamento/          ← 2 file letti in ordine alfabetico, concatenati nel system prompt
│   ├── 00_identity.txt     ← personalità, tono, regole anti-proiezione, filosofia amico
│   └── azioni.txt          ← documentazione azioni disponibili per il dispatcher
│   ⚠️ NON mettere backup (.bak, .old) in questa cartella — glob("*") li carica nel prompt
│
├── config/
│   └── dev_protocol.txt    ← regole sviluppo Cipher — caricato SOLO quando Simone parla di codice
│
├── memory/                 ← dati persistenti a runtime (non versionato)
│   ├── profile.json        ← profilo utente (apprendimento da conversazioni). Vuoto: {"personal": {}, "preferences": {}, "facts": [], "updated_at": null}
│   ├── cipher_state.json   ← stato emotivo corrente, last_reflection, concern_for_simone. Vuoto: {"emotional_state": "neutral", "emotional_reason": "", "last_reflection": null, "last_interaction": null, "total_reflections": 0, "want_to_explore": "", "concern_for_simone": "", "stale_goal_titles": [], "simone_state": "unknown"}
│   ├── goals.json          ← obiettivi autonomi attivi. Vuoto: {"goals": []}
│   ├── active_history.json ← history conversazione corrente (TTL: 24h normali, 12h messaggi autonomi, max 20 msg)
│   ├── short_term.json     ← eventi/piani temporanei. TTL: 48h. Vuoto: []
│   ├── checkin_history.json← argomenti check-in con flag closed:true → filtra conversazioni passate. Vuoto: []
│   ├── discretion_state.json← log invii per anti-spam. Vuoto: {"sent_log": []}
│   ├── emotional_log.json  ← log stati emotivi (disabilitato nella reflection loop). Vuoto: []
│   ├── episodes.json       ← memoria episodica strutturata (timeline). Vuoto: []
│   ├── cipher_interests.json← interessi propri di Cipher con intensità e decay
│   ├── ethics_learned.json ← autonomia acquisita per azione (dopo 3 approvazioni manuali)
│   ├── ethics_log.md       ← log decisioni etiche
│   ├── thoughts.md         ← diario riflessioni autonome (scritto da self_reflection.py)
│   ├── goals.md            ← obiettivi in markdown (generato da goal_manager.py)
│   ├── outcome_log.json    ← esiti obiettivi completati. Vuoto: []
│   ├── patterns.json       ← stub disabilitato. Vuoto: {}
│   ├── impact_log.json     ← stub disabilitato. Vuoto: []
│   ├── feedback_weights.json← stub disabilitato. Vuoto: {}
│   ├── morning_pattern.json← orario medio risveglio per adattamento brief. Formato: {"avg_minutes": 480, "samples": 0, "last_date": null}
│   ├── night_cycle_last.json← data ultima esecuzione ciclo notturno
│   ├── daily_summaries.md  ← sommari notturni (pensiero notturno letto da morning brief)
│   ├── voice_notes.md      ← note sul modo di parlare/scrivere di Simone
│   ├── pattern_insights.md ← stub disabilitato
│   ├── screenshots.md      ← screenshot analizzati
│   └── conversations/      ← sessioni complete (pulizia automatica dopo 30 giorni)
│
├── apprendimento/          ← conoscenze salvate da ricerche web, per dominio (arduino, cybersec, linguaggi, ecc.)
│
├── modules/                ← tutti i moduli Python (vedere sezione dedicata)
├── secrets/                ← credentials.json e token.json Google OAuth2 (non versionato)
├── home/                   ← sandbox filesystem utente scrivibile (Config.HOME_DIR)
├── uploads/                ← file ricevuti via Telegram/WhatsApp
└── venv/                   ← virtual environment Python
```

---

## 3. ARCHITETTURA

### Flusso messaggi
```
Telegram → cipher_bot.py → POST /chat → server.py → Brain.think() → risposta
```

`Brain.think()` in sequenza:
1. Controlla `dispatcher.has_pending()` (azione in attesa di consenso)
2. Chiama `_detect_topic_closure()` in thread daemon
3. Chiama `_call_llm()` con history + system prompt
4. Parsa eventuali JSON di azione dalla risposta
5. Esegue l'azione tramite `ActionDispatcher.execute()`
6. Aggiorna history e salva

### ConsciousnessLoop
Thread daemon avviato da `server.py`. Ciclo principale ogni ~60 secondi:
- **Riflessione** ogni `REFLECTION_INTERVAL = 30 min` (`× 2` se Simone inattivo da > 7200s)
- **Generazione obiettivi** ogni 20 min
- **Esecuzione obiettivi** ogni 5 min
- **Check inattività** → `_check_inactivity()` → contatta Simone dopo `INACTIVITY_THRESHOLD = 120 min`
- **Morning brief** → `_send_morning_brief()` nella finestra 7:00–8:00
- **Monitor passivo** → ogni 10 min (notizie interessi + scadenze calendario)
- **Night cycle** → alle 3:00
- **Auto-ispezione** → ogni 48h

### Sistema di memoria
- **profile.json** — impara dalle conversazioni (MemoryWorker + estrazione in-thread)
- **short_term.json** — eventi/piani temporanei, TTL 48h
- **active_history.json** — history corrente: TTL 24h (12h per `[messaggio autonomo...]`), max 20 messaggi
- **conversations/** — archivio sessioni complete, pulizia automatica 30 giorni

### Filtro keyword chiuse
`checkin_history.json` contiene voci con `closed: true` e una lista `keywords`. In `memory.build_context()`, i messaggi delle conversazioni passate che contengono quelle keyword vengono esclusi dal contesto — impedisce che argomenti risolti (malattie temporanee, problemi passati) riemergano nelle sessioni successive. `_detect_topic_closure()` in `brain.py` aggiorna questo file automaticamente quando Simone segnala che qualcosa è risolto.

---

## 4. SYSTEM PROMPT

### Parte statica
Caricata **una sola volta all'avvio** da `Brain._load_static_prompt()`. Legge tutti i file in `comportamento/` in ordine alfabetico (`glob("*")`), concatenati con `\n\n`. Attualmente 2 file: `00_identity.txt` e `azioni.txt`.

Per ricaricarla senza restart: `brain.reload_static_prompt()`

⚠️ Non mettere file `.bak` o `.old` in `comportamento/` — vengono inclusi nel prompt.

### Dev protocol
`config/dev_protocol.txt` viene caricato **condizionalmente** in `_build_system_prompt()`: solo se negli ultimi 5 messaggi `role == "user"` compare almeno una keyword di sviluppo (`project_read`, `project_write`, `modifica`, `bug`, `fix`, `codice`, `modulo`, `cipher-server`, `brain.py`, `consciousness`, `deploy`, ecc.).

### Parte dinamica (TTL 300s)
Ricalcolata ogni 5 minuti o su `invalidate_system_prompt()`. Include:
- Data e ora corrente
- Profilo utente (da `profile.json`)
- Short-term events (da `short_term.json`)
- Conversazioni passate filtrate (max 20 messaggi, esclusi argomenti chiusi)
- Memoria episodica (ultimi 4 episodi)
- Obiettivi autonomi attivi
- Script approvati (se presenti in `home/allowed_scripts.json`)

---

## 5. MODELLI LLM

| Funzione | Modello | Temperatura | max_tokens | Uso |
|---|---|---|---|---|
| `_call_llm()` | Sonnet | 0.4 | — | Risposte conversazionali dirette a Simone |
| `_call_llm_visible()` | Sonnet | 0.4 | 512 | Messaggi proattivi visibili (check-in, morning brief, reminder) |
| `_call_llm_quality()` | Sonnet | 0.5 | 512 | Sommari, voice notes, ragionamenti profondi |
| `_call_llm_silent()` | Haiku | 0.2 | 256 | Operazioni interne invisibili (estrazione, classificazione, topic closure) |

Configurabile via `.env`:
- `LLM_PROVIDER=openrouter` (default) o `anthropic`
- `OPENROUTER_MODEL` — modello principale (default: `anthropic/claude-sonnet-4-6`)
- `BACKGROUND_MODEL` — modello background (default: `anthropic/claude-haiku-4-5`)

---

## 6. MESSAGGI PROATTIVI

### Check-in inattività
- Soglia: **120 minuti** di inattività (`INACTIVITY_THRESHOLD`)
- Massimo: **4/giorno**, **1/ora** (`calendar_reminder` esclusi dal conteggio)
- Distanza minima urgency "low": **120 minuti** dall'ultima notifica
- `CHECKIN_PROMPT` include: minuti di inattività, ora, giorno, stato emotivo, contesto recente, eventi calendario
- Regola anti-proiezione integrata nel prompt: un "grazie" o "ok" non è una risposta a situazioni precedenti
- Se LLM risponde `SKIP` → non invia nulla

### Morning brief
Inviato una volta nella finestra **7:00–8:00**. Cinque scenari:
1. **Compleanno di Simone** — auguri autentici, niente frasi da biglietto
2. **Giorno festivo + eventi** — auguri brevi, nessun riferimento a lavoro/scuola/stage
3. **Giorno festivo senza eventi** — auguri brevi, stop
4. **Giorno normale + eventi** — agenda + pensiero notturno (se disponibile e ancora sentito)
5. **Giorno normale senza eventi** — pensiero notturno (se disponibile), altrimenti SKIP

Tutti i rami usano `_call_llm_visible()`. Se LLM risponde SKIP → solo calendario grezzo (se ci sono eventi), altrimenti silenzio.

### Calendar reminder
I promemoria di calendario (`action_type = "calendar_reminder"`) sono **esclusi dai conteggi anti-spam** orario e giornaliero — passano sempre se nelle ore attive.

### Passive monitor
Ogni 10 minuti controlla notizie sugli interessi di Cipher. Passa attraverso `DiscretionEngine.should_send()`.

---

## 7. MODULI DISABILITATI / STUB

I seguenti moduli esistono come classi Python (per non rompere gli import) ma tutti i metodi sono no-op o ritornano valori neutri:

| Modulo | Motivo |
|---|---|
| `impact_tracker.py` | Ottimizzava Cipher per l'efficacia statistica — incompatibile con la filosofia "amico" |
| `pattern_learner.py` | Tracciava pattern statistici di Simone — Cipher deve conoscerlo tramite conversazione |

Conseguenze:
- `discretion.py`: `_get_action_effectiveness()` → sempre `None`
- `feedback_weights.json` → non aggiornato
- `engagement_signal` nel loop di riflessione → stringa vuota
- Sezione previsioni pattern nel contesto unificato → disabilitata
- `emotional_log.json` nel contesto unificato → disabilitato
- `_update_motivational_profile()` in `night_cycle.py` → disabilitata (step 7 rimosso)

---

## 8. COMANDI UTILI

```bash
# Restart
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service

# Log
sudo journalctl -u cipher.service --since "5 min ago"
sudo journalctl -u cipher.service -f

# Stato servizi
sudo systemctl status cipher.service cipher-telegram.service cipher-funnel.service cipher-memory.service

# Python (usa sempre il venv)
/home/Szymon/Cipher/cipher-server/venv/bin/python3

# Ricarica comportamento/ senza restart
brain.reload_static_prompt()

# Forza ricalcolo system prompt
brain.invalidate_system_prompt()

# Verifica sintassi modulo
/home/Szymon/Cipher/cipher-server/venv/bin/python3 -m py_compile modules/nome.py
```

---

## 9. REGOLE PER LAVORARE SUL CODICE

Prima di proporre qualsiasi modifica o nuovo file del progetto Cipher, devi sempre:
1. Leggere tutti i file potenzialmente coinvolti con `project_read`
2. Verificare se la funzionalità esiste già altrove prima di crearla
3. Solo dopo aver letto il codice esistente, proporre la soluzione

Questa regola NON si applica a file indipendenti (script, note, documenti) che l'utente vuole creare nella propria home tramite `fs_write`.

Distinzione fondamentale:
- Se vuoi creare o modificare un file **dentro il progetto Cipher** (moduli, configurazioni, script di avvio) → usa `project_read`/`project_write` prima di agire, e chiedi sempre conferma prima di scrivere.
- Se vuoi creare un file **indipendente** (script bash, note, documenti) → usa `fs_write` direttamente.

Quando esegui un'azione, prima del JSON scrivi sempre una riga in linguaggio naturale che descriva cosa stai facendo. Esempi:
- `project_read` → "Sto leggendo modules/notifier.py..."
- `project_write` → "Sto scrivendo modules/reminders.py..."

**I file in `comportamento/` vengono letti una sola volta all'avvio. Dopo una modifica, serve `cipher-restart` o `brain.reload_static_prompt()`.**

---

## 10. VINCOLI CRITICI

- **Mai hardcodare path**: usare sempre `Config.*`. Mai scrivere `/home/szymon/cipher/...` o `"./memory/..."` direttamente nel codice.

- **Scrittura file**: permessa **solo** dentro `Config.HOME_DIR` o `Config.MEMORY_DIR`. Qualsiasi scrittura fuori è un bug di sicurezza.

- **Scritture JSON**: usare sempre `write_json_atomic()` da `modules/utils.py` su file condivisi. Usa un file `.tmp` + `rename()` atomico — garantisce no-corruption con processi/thread multipli.

- **Max 20 messaggi in history**: `Brain._history` viene troncato a `MAX_HISTORY_MESSAGES * 2 = 20`. Non rimuovere questo limite.

- **Filtro keyword chiuse**: `checkin_history.json` con `closed: true` filtra le conversazioni passate in `build_context()`. Non alzare il limite di 20 messaggi — contribuisce direttamente alla dimensione del system prompt.

- **TTL active_history**: 24h per messaggi normali, 12h per `[messaggio autonomo...]`. Implementato in `_load_history()`.

- **TTL short_term**: 48h (172800s). Implementato in `memory.py`.

- **System prompt TTL**: 300s (5 minuti). Costante `_SYSTEM_PROMPT_TTL` in `Brain`.

- **Tag messaggi proattivi**: `inject_autonomous_message()` inietta con prefisso `[messaggio autonomo DD/MM HH:MM]: ...`. Non alterare questo formato — il LLM lo usa per distinguere i contesti.

- **Morning brief — unica fonte**: gestito esclusivamente da `consciousness_loop.py:_send_morning_brief()`. Non reintrodurre briefing nello scheduler — causerebbe doppio invio.

- **Thread LLM**: le chiamate LLM da `ConsciousnessLoop` girano in thread separato. Chiamarle nel loop principale causa deadlock.

- **Flask su 127.0.0.1**: non cambiare a `0.0.0.0` — esporrebbe l'API senza autenticazione.

- **Consenso azioni sensibili**: `shell_exec`, `fs_delete`, `whatsapp_send` ecc. richiedono conferma. Non bypassare — è l'unica protezione contro esecuzioni irreversibili accidentali.

- **Moduli opzionali di Brain**: `_impact_tracker`, `_pattern_learner`, `_episodic_memory` sono `None` finché `ConsciousnessLoop` non li inietta. Controllare sempre: `if self._impact_tracker:` prima di usarli.

- **GoalManager**: `goals.json` usa la chiave `"goals"`. Formato vuoto corretto: `{"goals": []}`.

- **DiscretionEngine**: `discretion_state.json` usa la chiave `"sent_log"`. Formato vuoto corretto: `{"sent_log": []}`.

- **Google credentials**: in `secrets/`. `google_auth.py` valida gli scope al boot: se il token non corrisponde, viene eliminato e rilancia OAuth2.

- **Gmail rimossa**: non esiste più `modules/google_mail.py`. Lo scope `gmail.modify` è stato eliminato da `Config.GOOGLE_SCOPES`.

- **`project_inspect`**: usa `_call_llm` (Sonnet) per analizzare diff. Salva ultimo hash in `memory/last_project_check.txt`. Diff troncato a 4000 chars.

- **Anti-proiezione di contesto**: regola critica definita in `comportamento/00_identity.txt`. Non duplicarla nei singoli prompt. Un messaggio breve ("grazie", "ok") non va mai caricato con il peso di conversazioni precedenti.

- **Anti-spam limiti attuali**: `MAX_PER_HOUR = 1`, `MAX_PER_DAY = 4`. Modificabili in `modules/discretion.py`. I `calendar_reminder` sono esclusi da entrambi i conteggi.

**Fine sessione**: aggiornare `.claude/session-state.md` con i file toccati, le modifiche fatte e l'esito finale.
