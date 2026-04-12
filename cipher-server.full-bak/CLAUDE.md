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

## Comandi rapidi

```bash
source venv/bin/activate
python server.py             # avvia tutto (API + coscienza autonoma)
python main.py --mode text   # CLI interattiva
pip install -r requirements.txt

# Systemd
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service
sudo systemctl start cipher-funnel.service   # esposizione via Tailscale
sudo journalctl -u cipher -f                 # log in tempo reale
sudo journalctl -u cipher-telegram -f
```

---

## Architettura

```
server.py               ← entry point: Flask API (127.0.0.1) + init di tutti i moduli
memory_worker.py        ← processo separato: consolida memoria dalle conversazioni
config.py               ← Config.* — UNICA fonte di verità per path e valori
modules/
  brain.py              ← Brain: gestione history, LLM call, dispatch azioni, prompt statico (caricato all'avvio), cache system prompt
  utils.py              ← parsing JSON da LLM, scritture JSON atomiche (thread/process-safe)
  consciousness_loop.py ← ConsciousnessLoop: loop autonomo in thread separato
  actions.py            ← ActionDispatcher: esegue azioni, gestisce consenso
  memory.py             ← profilo utente e persistenza conversazioni
  goal_manager.py       ← obiettivi autonomi di Cipher
  scheduler.py          ← digest serale 20:00, task ricorrenti (briefing rimosso — gestito da consciousness_loop)
  notifier.py           ← invio messaggi Telegram proattivi
  self_reflection.py    ← riflessione periodica (6 stati emotivi)
  pattern_learner.py    ← apprendimento pattern conversazionali
  episodic_memory.py    ← memoria episodica a lungo termine
  impact_tracker.py     ← tracciamento impatto azioni
  discretion.py         ← decide quando/cosa inviare (ore silenziose, anti-spam)
  cipher_interests.py   ← interessi propri di Cipher con decay
  passive_monitor.py    ← monitor background cal/mail/notizie
  night_cycle.py        ← elaborazione notturna alle 3:00
  file_engine.py        ← lettura/modifica file utente (Excel, PDF, ecc.)
  filesystem.py         ← fs_* actions su Config.HOME_DIR
  realtime_context.py   ← meteo, ora, contesto real-time
  google_auth.py        ← OAuth2 Google
  google_cal.py         ← Google Calendar
  contacts.py           ← rubrica contatti (risoluzione nome → numero WhatsApp/Telegram)
  whatsapp.py           ← WhatsApp via Green API
  voice.py              ← TTS (ElevenLabs)
  listener.py           ← STT Vosk + wake word
  reminders.py          ← gestione promemoria
  script_registry.py    ← registro script con approvazione
secrets/                ← credentials.json e token.json Google OAuth2 (non versionato)
comportamento/          ← system prompt costruito concatenando i file in ordine alfabetico (3 file)
  00_identity.txt       ← personalità, tono, regole di comunicazione
  azioni.txt            ← documentazione azioni disponibili
  user_identity.txt     ← informazioni stabili su Simone (nome, residenza, salute, lavoro, passioni, persone)
  ⚠️ NON mettere backup (.bak, .old, ecc.) dentro questa cartella — il glob("*") li carica nel system prompt
config/
  dev_protocol.txt      ← regole lettura progetto — caricato SOLO quando Simone parla di sviluppo Cipher
memory/                 ← file JSON e markdown di stato persistente (non versionato)
home/                   ← filesystem utente scrivibile (Config.HOME_DIR) (non versionato)
apprendimento/          ← conoscenze salvate da ricerche web
uploads/                ← file caricati da Simone (non versionato)
```

---

## Vincoli critici

- **Mai hardcodare path**: usare sempre `Config.*`. Esempio corretto: `Config.HOME_DIR / "file.txt"`. Mai scrivere `/home/szymon/cipher/...` o `"./memory/..."` direttamente nel codice.

- **Scrittura file**: permessa **solo** dentro `Config.HOME_DIR` o `Config.MEMORY_DIR`. Qualsiasi scrittura fuori da queste due directory è un bug di sicurezza — Cipher non deve poter scrivere liberamente nel filesystem.

- **Scritture JSON**: usare sempre `write_json_atomic()` da `modules/utils.py`. Questa funzione usa un file `.tmp` + `rename()` atomico su Linux — garantisce che il file non venga mai corrotto se il processo muore a metà scrittura, e che due thread/processi non si pestino i piedi. Mai usare `path.write_text(json.dumps(...))` direttamente su file condivisi.

- **Google credentials**: si trovano in `secrets/` — `Config.GOOGLE_CREDENTIALS_FILE` e `Config.GOOGLE_TOKEN_FILE` puntano lì per default. Non spostare questi file e non cambiare i path in `config.py` senza aggiornare anche `secrets/`.

- **Thread LLM**: le chiamate LLM da `ConsciousnessLoop` devono girare in thread separato con `threading.Thread`. Chiamare il LLM direttamente nel loop principale causa deadlock perché il GIL + i lock interni di `Brain` si bloccano a vicenda.

- **Troncamento history**: `Brain._history` viene troncato a `MAX_HISTORY_MESSAGES * 2` messaggi. Non rimuovere questo limite — senza di esso la history cresce illimitatamente e ogni chiamata LLM diventa più lenta e costosa fino a crashare per token overflow.

- **Consenso**: le azioni sensibili (es. `shell_exec`, `fs_delete`) impostano `self._pending_action` + `self._pending_params` nel dispatcher e restituiscono una richiesta di conferma all'utente. Non bypassare questo meccanismo — è l'unica protezione contro esecuzioni accidentali di azioni irreversibili.

- **Flask su 127.0.0.1**: il server ascolta solo sul loopback. L'esposizione esterna avviene tramite `cipher-funnel.service` (Tailscale), che è controllato e autenticato. Cambiare a `0.0.0.0` esporrebbe l'API senza autenticazione su tutta la rete locale.

- **Confabulazione — regola critica**: la regola anti-confabulazione è definita **una sola volta** in `comportamento/00_identity.txt` e si applica a tutte le chiamate LLM (sia `_call_llm` che `_call_llm_silent`). La regola copre sia ciò che dice Simone sia ciò che Cipher legge nel contesto (history, short-term memory, calendario): nulla di vago o incerto va completato con deduzioni. Non duplicare questa regola nei singoli prompt — se non funziona, si rinforza in `00_identity.txt`. In `memory.py:extract_from_message()` il prompt vieta esplicitamente inferenze — messaggi vaghi devono restituire array vuoti.

- **Check-in inattività**: il `CHECKIN_PROMPT` in `consciousness_loop.py` include ora, giorno, eventi di calendario delle prossime 2 ore, e — se oggi è un giorno festivo italiano — un blocco `{holiday_context}` esplicito che vieta qualsiasi riferimento a lavoro, stage, scuola o impegni professionali. `_generate_checkin_message()` chiama `_get_italian_holiday()` per iniettare questo contesto. Frasi generiche ("come sta andando?", "dimmi un po'") e calchi dall'inglese ("tutto bene da lì?") sono vietati esplicitamente. Il prompt include anche un vincolo anti-confabulazione: nessun riferimento a dettagli specifici (malattie, situazioni personali) se non citati esplicitamente nei messaggi recenti. Gli eventi di calendario già iniziati da più di 30 minuti vengono filtrati e non compaiono nel contesto del check-in.

- **Tag messaggi proattivi**: `_notify()` in `consciousness_loop.py` inietta i messaggi proattivi in `Brain._history` con il prefisso `[messaggio autonomo DD/MM HH:MM]: ...`. Questo distingue il contesto autonomo dal contesto conversazionale — il LLM sa che il messaggio di Simone successivo è una risposta al proattivo, non all'ultima azione eseguita. Non rimuovere o alterare questo prefisso.

- **Morning brief — unica fonte**: il briefing mattutino è gestito **esclusivamente** da `consciousness_loop.py:_send_morning_brief()`. `scheduler.py` non invia più nessun briefing (`_send_briefing` e `_should_send_briefing` sono stati eliminati). Non reintrodurre un secondo briefing nello scheduler — causerebbe doppio invio. Il pensiero notturno (`daily_summaries.md`) viene letto da `_send_morning_brief()` solo nei giorni normali (non festivi).

- **Digest serale**: contiene solo ciò che è utile a Simone — agenda di domani e promemoria pendenti. Non includere mai obiettivi autonomi di Cipher, conteggi di riflessioni interne, o interessi futuri di Cipher: non sono informazioni utili per Simone.

- **Gmail rimossa**: l'integrazione Gmail è stata rimossa completamente. Non esiste più `modules/google_mail.py`, né le azioni `gmail_list/read/send`. Lo scope `gmail.modify` è stato eliminato da `Config.GOOGLE_SCOPES`. `google_auth.py` valida automaticamente gli scope al boot: se il token salvato non corrisponde agli scope configurati, viene eliminato e viene rilanciato il flusso OAuth2.

- **Contatti** (`modules/contacts.py`): rubrica persistente in `memory/contacts.json`. Risolve nomi in numeri WhatsApp o ID Telegram — `whatsapp_send` accetta sia numeri che nomi ("mamma", "papà"). Azioni disponibili: `contact_list`, `contact_add`, `contact_remove`, `contact_update`. Il matching è case-insensitive e controlla chiave principale + aliases.

- **Feedback esplicito (`impact_tracker.py`)**: `should_ask_explicit_feedback()` restituisce il preview di un'azione pendente significativa; il `ConsciousnessLoop._check_inactivity()` lo usa per appendere una domanda di feedback al check-in (max 1/giorno, solo per azioni con contenuto > 80 chars, non reminder). `evaluate_response()` ora salva anche `response_time_seconds` e aggiorna `feedback_weights.json` per tipo di azione proattiva. `mark_ignored()` — chiamato dal loop principale dopo 90 min di silenzio, marca il proattivo come neutral.

- **Pattern sampling (`brain.py`)**: `_topic_sample_counter` in Brain — chiama LLM per estrarre topic 1 ogni 3 messaggi (mod 3 == 0), registra "generico" per gli altri. Riduce silent LLM calls ~66%.

- **Script discovery (`brain.py:_build_system_prompt`)**: legge `home/allowed_scripts.json` e aggiunge al system prompt la sezione `## Script approvati disponibili` con nome e descrizione degli script approvati. Cipher può usarli autonomamente con `shell_exec`. Se non ci sono script approvati con descrizione la sezione non appare.

- **`project_inspect` (`actions.py`)**: action su richiesta — esegue `git diff` + `git log` per mostrare le modifiche recenti al codice. Usa `_call_llm` (Sonnet) per analizzare il diff in linguaggio naturale. Salva l'hash dell'ultimo commit analizzato in `memory/last_project_check.txt` — chiamate successive mostrano solo le novità. Diff troncato a 4000 chars con avviso. Fallback a scan mtime se git non disponibile. `ActionDispatcher._llm_fn` viene iniettato da `server.py` con `brain._dispatcher.set_llm(brain._call_llm)`.

- **GoalManager — niente falliti nel markdown**: `_write_markdown()` in `goal_manager.py` non scrive più la sezione `❌ Falliti`. I goal completati vengono inclusi solo se completati negli ultimi 3 giorni (max 10). `_clean_fail_reason()` rimuove automaticamente i Python traceback da `fail_reason` — salva solo la riga di errore leggibile (max 200 chars).

- **Conversazioni passate nel contesto** (`memory.py:build_context()`): include gli ultimi 20 messaggi dalle sessioni precedenti (non la sessione corrente). Contenuto troncato a 500 chars/messaggio. I messaggi che contengono keyword di argomenti chiusi (voci con `closed: true` in `checkin_history.json`) vengono esclusi prima dell'inclusione nel contesto — questo impedisce che argomenti risolti (es. malattie temporanee) riemergano nelle sessioni successive. Non alzare il limite di 20 messaggi — contribuisce direttamente alla dimensione del system prompt.

- **DiscretionEngine — limiti anti-spam**: `MAX_PER_HOUR = 2` (era 3), `MAX_PER_DAY = 6` (era 12). Modificare in `modules/discretion.py`.

---

## Moduli opzionali di Brain

`_impact_tracker`, `_pattern_learner`, `_episodic_memory` non vengono passati al costruttore di `Brain` — vengono iniettati da `ConsciousnessLoop` dopo l'init, perché dipendono da moduli che a loro volta dipendono da `Brain` (dipendenza circolare). Questo significa che all'avvio, per i primi secondi, questi attributi sono `None`. Controllare sempre prima di usarli:

```python
if self._impact_tracker:
    self._impact_tracker.track(...)
```

Non fare mai `self._impact_tracker.track(...)` direttamente — crasha se la coscienza non è ancora partita o è disabilitata (`CONSCIOUSNESS_ENABLED=false`).

---

## System prompt

I file in `comportamento/` (attualmente 3: `00_identity.txt`, `azioni.txt`, `user_identity.txt`) vengono letti **una sola volta all'avvio** da `Brain._load_static_prompt()` e salvati in `self._static_prompt`. `_build_system_prompt(memory_ctx, history, static_prompt)` riceve il testo statico come parametro — non rilegge mai i file durante l'esecuzione. Per ricaricarli senza restart: `brain.reload_static_prompt()`. Il file `config/dev_protocol.txt` viene caricato **in modo condizionale**: solo se negli ultimi 5 messaggi `role == "user"` compare almeno una keyword di sviluppo (`project_read`, `project_write`, `modifica`, `bug`, `fix`, `codice`, `modulo`, `script`, `cipher-server`, `brain.py`, `consciousness`, `deploy`, ecc.).

Oltre alla parte statica, il prompt include dinamicamente: data e ora, profilo utente, obiettivi autonomi, stato emotivo recente, pattern comportamentali, voice notes, contesto real-time. Per modificare personalità, tono o azioni disponibili: editare i file in `comportamento/`, non il codice Python.

Il system prompt viene iniettato sia nelle chiamate conversazionali (`_call_llm`) che in quelle background silenziose (`_call_llm_silent`). La cache delle parti dinamiche si invalida automaticamente ogni 5 minuti (`_SYSTEM_PROMPT_TTL = 300s`) o subito tramite `invalidate_system_prompt()` dopo un aggiornamento di memoria.

---

## Sistema di consenso

Le azioni sensibili (es. `shell_exec`, `fs_delete`, `whatsapp_send`) non vengono eseguite immediatamente. Il dispatcher le mette in `self._pending_action` + `self._pending_params` e restituisce una richiesta di conferma testuale all'utente. Al messaggio successivo, `Brain.think()` controlla prima `dispatcher.has_pending()` e chiama `check_consent()`.

- Consenso riconosciuto: `sì / si / ok / procedi / esegui / confermo`
- Annullamento: `no / annulla / stop`
- Lista completa delle frasi: `actions.py:CONSENT_PHRASES`

Dopo 3 approvazioni manuali della stessa azione, `EthicsEngine` la promuove ad autonomia acquisita — non richiede più conferma.

---

## Aggiungere un'azione

1. Documentarla in `comportamento/azioni.txt` — Cipher deve sapere che esiste e come usarla nel JSON `{"action": "...", "params": {...}}`
2. Implementarla in `ActionDispatcher.execute()` in `modules/actions.py` — aggiungere un `elif action == "nome_azione":` con la logica
3. Se richiede consenso: impostare `self._pending_action` e `self._pending_params`, poi restituire la stringa di richiesta all'utente senza eseguire
4. Se usa path: usare sempre `Config.*`, mai path assoluti o costruiti a mano

---

## LLM Provider

Configurabile via `.env` senza toccare il codice. `config.py` gestisce automaticamente base URL, model ID e header corretti per ciascun provider.

```
LLM_PROVIDER=openrouter   # default — nessun rate limit TPM, pay-per-use
LLM_PROVIDER=anthropic    # Anthropic diretto — richiede Tier 2/3 per volume
```

- Modello principale (`_call_llm`, `_call_llm_quality`): `Config.OPENROUTER_MODEL` — default `anthropic/claude-sonnet-4-6`
- Modello background (`_call_llm_silent`): `Config.BACKGROUND_MODEL` — default `anthropic/claude-haiku-4-5`, più veloce e economico per task silenziosi (estrazione, classificazione, riflessioni autonome)

---

## Deploy

- Accesso al server: SSH da Termux su Android
- 4 servizi systemd attivi: `cipher.service` (API + coscienza), `cipher-telegram.service` (bot), `cipher-memory.service` (consolidamento memoria), `cipher-funnel.service` (Tailscale)
- Restart standard: `sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service`
- Log live: `sudo journalctl -u cipher -f`
- Il servizio `cipher-funnel` raramente va restartato — solo se Tailscale perde la connessione

**Fine sessione**: aggiornare `.claude/session-state.md` con i file toccati, le modifiche fatte e l'esito finale. Questo file viene letto all'avvio della sessione successiva per riprendere senza dover rileggere tutto da capo.
