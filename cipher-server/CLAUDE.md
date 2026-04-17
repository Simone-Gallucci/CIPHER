# CLAUDE.md — Cipher Project

Guida operativa per Claude Code. Leggere **prima** di toccare qualsiasi file.

---

## Cosa è Cipher

Cipher è un AI companion con memoria persistente, riflessione autonoma, obiettivi propri e messaggi proattivi via Telegram. Stack: Python, Flask, Claude LLM (via OpenRouter o Anthropic diretto), ElevenLabs TTS, Vosk STT, Google Calendar/Gmail. Chiunque interagisca è trattato allo stesso modo — la relazione cresce dai comportamenti, non dai nomi dichiarati.

4 servizi systemd: `cipher.service` (Flask + ConsciousnessLoop), `cipher-telegram.service` (bot Telegram), `cipher-memory.service` (memory_worker), `cipher-funnel.service` (Tailscale).

---

## Architettura

- **Conversazionale**: Telegram/Web/CLI → `server.py` → `Brain.think()` → LLM (Sonnet) → risposta
- **Autonomo**: `ConsciousnessLoop` (thread daemon ~60s) → riflessione, obiettivi, check-in, morning brief → LLM (Haiku/Sonnet)
- **Memoria**: `memory.py` → profilo, conversazioni, episodi, pattern → file JSON/MD in `memory/user_<id>/`

```
Telegram/Web → cipher_bot.py/web → POST /chat (127.0.0.1) → server.py
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

---

## Path e infrastruttura

- Path progetto: `~/Cipher/cipher-server/`
- Repo: `Simone-Gallucci/CIPHER` (branch main)
- Server: Linux, utente `Szymon`
- Flask su `127.0.0.1:5000`, esposto via Tailscale funnel (`https://cipher.taila739e7.ts.net/web`)
- 4 servizi systemd: `cipher.service`, `cipher-telegram.service`, `cipher-memory.service`, `cipher-funnel.service`
- Python venv: `venv/bin/python3`

```bash
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service
sudo journalctl -u cipher.service -f
sudo systemctl status cipher.service cipher-telegram.service cipher-funnel.service cipher-memory.service
venv/bin/python3 -m py_compile modules/nome.py   # verifica sintassi
```

---

## Moduli di sicurezza (post-hardening Step 1–5)

### shell_guard.py (Step 1)

Sostituisce `subprocess.run(shell=True)` con esecuzione argv-list validata. Whitelist di binari consentiti (~30), blocca pattern pericolosi (`;`, `&&`, `$()`, backtick), supporta pipe tra comandi whitelisted. Validazione path traversal integrata. Ambiente pulito: nessuna variabile da `os.environ` propagata ai subprocess.

- Usato da: `server.py` (endpoint `/api/terminal`), `modules/actions.py` (`shell_exec`)
- Audit log: `logs/shell_audit.log`

### path_guard.py (Step 2)

Validazione path centralizzata con `Path.resolve()` + `relative_to()`. Previene `../`, symlink escape, null-byte injection. Gestisce due zone: home utente (`home/user_<id>/`) e progetto (`cipher-server/`). Crea home per-utente con permessi `0o700`.

- Funzioni: `validate_path()`, `validate_project_path()`, `get_user_home()`, `_safe_resolve()`
- Audit log: `logs/file_audit.log`

### prompt_sanitizer.py (Step 3a + 3b)

Difesa prompt injection con 33+ pattern regex (EN/IT). Copre: classic injection ("ignore instructions"), role injection, exfiltration, end-of-document pivot, leet speak normalization. Wrapping XML-like (`<user_data>...</user_data>`) per dati untrusted nel system prompt.

- Funzioni: `detect_injection_attempt()`, `sanitize_memory_field()`, `wrap_untrusted()`, `normalize_leet()`
- Usato da: `memory.py` (estrazione), `brain.py` (system prompt building), `file_engine.py`
- Audit log: `logs/injection_audit.log`

### auth.py (Step 2 + 4)

Identità utente centralizzata. Attualmente hardcoded a `"simone"`, predisposto per multi-user via Flask request context.

- Funzioni: `get_current_user_id()`, `get_system_owner_id()`, `get_user_memory_dir(user_id)`
- Usato da: `server.py`, `brain.py`, `memory.py`, `consciousness_loop.py`, tutti i moduli che accedono a file utente

### admin_lockout.py (Step 5)

Lockout persistente per tentativi admin falliti. Stato su disco (`data/lockouts.json`, permessi `0o600`). Cache in-memory con flush a ogni cambio di stato.

- Parametri: `MAX_FAILED_ATTEMPTS=5`, `LOCKOUT_DURATION_MINUTES=30`
- Audit log: `logs/admin_audit.log` (JSONL, RotatingFileHandler 5MB × 10)

### message_rate_limiter.py (Step 5)

Rate limiting per-utente sui messaggi. Stato su disco (`data/rate_limits.json`, permessi `0o600`). Cleanup TTL automatico (record >1h rimossi). Complementare al rate limit HTTP per-IP di Flask.

- Parametri: `MAX_PER_MINUTE=10`, `MAX_PER_HOUR=60`
- Inserito in `Brain.think()`, prima di qualsiasi chiamata LLM
- `sender_id` propagato da: `cipher_bot.py` (Telegram chat_id via HTTP payload), `main.py` ("cli"), `server.py` (estrae `chat_id` dal JSON)

---

## Struttura memoria

- Root: `memory/` (permessi `0o700`)
- Per-utente: `memory/user_<id>/` (permessi `0o700`)
- File: `profile.json`, `active_history.json`, `cipher_state.json`, `emotional_log.json`, `episodes.json`, `goals.json`, `thoughts.md`, ecc.
- Permessi file: `0o600`
- Scrittura atomica: `write_json_atomic(path, data, permissions=0o600)` da `modules/utils.py`
- `conversations/` dentro `user_<id>/` (1 file JSON per sessione, pulizia >30gg)
- `data/patterns.json` è in `data/` (globale, non per-utente — TODO: migrare in futuro)

---

## Struttura home utente

- Root: `home/` (creata da `config.py` al boot)
- Per-utente: `home/user_<id>/` (creata da `path_guard.py` con permessi `0o700`)
- `uploads/` dentro `home/user_<id>/`

---

## Audit log

| File | Contenuto | Formato |
|---|---|---|
| `logs/shell_audit.log` | Comandi shell (eseguiti e bloccati) | JSONL, RotatingFileHandler 5MB × 10 |
| `logs/file_audit.log` | Accessi file (lettura, scrittura, listing, blocchi) | JSONL, RotatingFileHandler 5MB × 10 |
| `logs/injection_audit.log` | Tentativi prompt injection rilevati | JSONL, RotatingFileHandler 5MB × 10 |
| `logs/admin_audit.log` | Tentativi admin (successi, fallimenti, lockout) | JSONL, RotatingFileHandler 5MB × 10 |

---

## Regole operative per Claude Code

### Deploy

- git commit ≠ deploy — dopo ogni commit: `sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service`
- Test end-to-end via curl PRIMA del commit
- Verificare `git log`/`git status` DOPO ogni commit
- Verificare servizi UP dopo restart: `curl http://127.0.0.1:5000/health`

### Sicurezza — regole assolute

- **NON** usare `shell=True` in subprocess — usare `ShellGuard`
- **NON** concatenare input utente in comandi shell
- **NON** leggere/scrivere file fuori da `get_user_home()`/`get_user_memory_dir()`
- **NON** inserire dati untrusted nel system prompt senza `wrap_untrusted()`
- **NON** propagare `os.environ` ai subprocess
- Scritture JSON condivise: sempre `write_json_atomic()` con `permissions=0o600`
- Dir utente: sempre `0o700`
- Auth token header: `X-Cipher-Token` — valore in `.env` come `CIPHER_API_TOKEN`

### Vincoli architetturali

- Mai hardcodare path — usare `Config.*` o `get_user_home()`/`get_user_memory_dir()`
- Flask su `127.0.0.1` — non cambiare a `0.0.0.0`
- `_route_model()` ritorna sempre Sonnet — non modificare senza valutare costo/latenza
- `_call_llm_quality()` senza system prompt — deliberato per NightCycle
- `_call_llm_opus()` ha system prompt — deliberato per auto-ispezione
- Confidence score non decresce mai
- `glob("*")` su `comportamento/` carica tutto — mai file `.bak`/`.old` in quella directory
- Moduli opzionali Brain: `_pattern_learner`, `_episodic_memory` partono `None` — ogni riferimento fa `if self._xxx:`
- Max 3 goal attivi concorrenti
- Max 20 messaggi in history: `MAX_HISTORY_MESSAGES = 10` (10 coppie)
- `admin.json` e `changelog.json` sono permanenti — mai toccarli in Tabula Rasa o pulizie
- Consenso richiesto per: `shell_exec`, `fs_delete`, `project_write`, `gmail_send`
- Morning brief gestito solo da `consciousness_loop.py:_send_morning_brief()` — non reintrodurre nello scheduler
- LLM provider fallback: `_fallback_client` tenta provider alternativo se primario fallisce

### Formato dati — non alterare

- Tag messaggi proattivi: `[messaggio autonomo DD/MM/YYYY HH:MM]: ...`
- Timestamp history: prefisso `[DD/MM/YYYY HH:MM]` su user e assistant
- Regola 11 system prompt: vieta timestamp nell'output
- `## Task in corso` inietta solo titoli obiettivi — mai descrizioni

---

## Flusso Brain.think()

1. Rate limiting per-utente (`MessageRateLimiter.check()`)
2. Intercetta `Admin+Password` → `_handle_admin_command(sender_id=...)` con `AdminLockout`
3. `_awaiting_bond_password` → `_handle_bond_password()`
4. Controlla `tabula rasa` / `revoca autonomia` / reset (two-phase confirm)
5. Controlla audit / pensieri keywords
6. `consciousness.handle_consent_response()`
7. `dispatcher.has_pending()` → `check_consent()`
8. `handle_forget_command` / `handle_remember_command`
9. Append history con timestamp
10. `_get_system_prompt()` (TTL 300s)
11. `_pre_action.gather()` → `verified_data` iniettato DOPO la TTL cache
12. `_call_llm(verified_data=...)` → risposta (sempre Sonnet)
13. `extract_all_action_json` → `ActionDispatcher.execute()`
14. Thread daemon (delay 10s): estrazione memoria, pattern, emotional_log, confidence
15. Se `source=="web"`: inietta nota UI actions in `verified_data`

---

## Modelli LLM

| Funzione | Metodo | Modello | Parametri |
|---|---|---|---|
| Conversazione principale | `_call_llm()` | Sonnet (`OPENROUTER_MODEL`) | max_tokens=1024, temp=0.4 |
| Messaggi proattivi visibili | `_call_llm_visible()` | Sonnet | max_tokens=512, temp=0.4 |
| Output creativo / NightCycle | `_call_llm_quality()` | Sonnet | max_tokens=512, temp=0.5, **no system prompt** |
| Background silenzioso | `_call_llm_silent()` | Haiku (`BACKGROUND_MODEL`) | max_tokens=256, temp=0.2 |
| Auto-ispezione | `_call_llm_opus()` | Opus (`OPUS_MODEL`) | max_tokens=1024, con system prompt |

---

## System prompt

**Statico**: `sorted(glob("*"))` su `comportamento/`. Ricaricabile: `brain.reload_static_prompt()`. Mai `.bak`/`.old` in `comportamento/`.

**Dinamico** (TTL 300s): static prompt → data/ora → 14 regole fondamentali → memory context → confidence → onboarding/conoscente → pattern insights → voice notes → real-time context → task in corso → cipher state (sanitizzato con `_meta_keywords`) → ultimo pensiero → stato emotivo → dev protocol → morning brief nota → modalità voce → **[DATI VERIFICATI]** (iniettato dopo TTL cache).

`[DATI VERIFICATI]` (PreActionLayer): L1 sempre (datetime + calendar_today cache 300s), L2 rule-based (email_unread cache 60s se keyword email).

---

## Confidence e relazione

`confidence_score` float 0.0–1.0 in `memory/user_<id>/profile.json`. **Non può scendere mai.**

| Score | Livello |
|---|---|
| 0.0–0.2 | Conoscente — diretto, no suggerimenti non richiesti |
| 0.2–0.4 | Amico — una domanda personale per sessione, dal contesto |
| 0.4–0.6 | Amico stretto — domande naturali, ironia leggera |
| 0.6–0.8 | Confidente — emozioni aperte, nome/soprannomi |
| 0.8–1.0 | Migliore amico — diretto, anticipa bisogni |

**Bond trigger**: `confidence >= 0.8` + `bond_proposed=False` → propone legame → `data/admin.json` (PBKDF2-SHA256).

**Admin lockout**: 5 tentativi falliti → 30 minuti lockout, persistente su disco, audit log.

---

## Messaggi proattivi

| Tipo | Confidence min | Frequenza |
|---|---|---|
| Morning brief | >= 0.3 | 1/giorno, 7:00–8:00 |
| Check-in inattività | >= 0.4 | max 4/giorno, 1/ora (120 min inattività) |
| Notizie interessi | nessuna | max 4/giorno, 1/ora (DiscretionEngine) |
| Calendar reminder | nessuna | illimitato (escluso conteggio anti-spam) |

Ore silenziose: 23:00–07:00. `DiscretionEngine`: `MAX_PER_HOUR=1`, `MAX_PER_DAY=4`.

---

## Comandi speciali

| Comando | Effetto |
|---|---|
| `tabula rasa` | Reset completo memoria (chiede conferma) |
| `Admin+Password` | Login admin |
| `Admin+Password+status` | Diagnostica |
| `Admin+Vecchia+Nuova` | Cambio password |
| `revoca autonomia` | Reset permessi (chiede conferma) |

---

## File e moduli principali

### Entry point

| File | Ruolo |
|---|---|
| `config.py` | Configurazione centralizzata, path, modelli |
| `server.py` | Flask server, endpoint, init moduli |
| `cipher_bot.py` | Bot Telegram (processo separato) |
| `memory_worker.py` | Consolida profilo ogni ora |
| `main.py` | CLI text/voice/both |

### Moduli core

| File | Ruolo |
|---|---|
| `modules/brain.py` | LLM routing, system prompt, dispatcher, history, admin |
| `modules/memory.py` | Profilo, conversazioni, estrazione, confidence |
| `modules/consciousness_loop.py` | Thread daemon — task periodici (~60s) |
| `modules/self_reflection.py` | Auto-riflessione → `cipher_state.json` |
| `modules/goal_manager.py` | Genera/gestisce obiettivi (5 tipi) |
| `modules/actions.py` | ActionDispatcher — 30+ tipi, consenso |
| `modules/pre_action_layer.py` | Dati verificati real-time |
| `modules/night_cycle.py` | Sommario notturno, voice notes, pattern insights |
| `modules/episodic_memory.py` | Episodi salienti, recall query-based |

### Moduli sicurezza

| File | Ruolo |
|---|---|
| `modules/shell_guard.py` | Whitelist shell, validazione argv |
| `modules/path_guard.py` | Validazione path, per-user home |
| `modules/prompt_sanitizer.py` | Difesa prompt injection, wrapping |
| `modules/auth.py` | Identità utente centralizzata |
| `modules/admin_lockout.py` | Lockout admin persistente |
| `modules/message_rate_limiter.py` | Rate limit per-utente persistente |
| `modules/ethics_engine.py` | Livelli permesso 0–3, autonomia |
| `modules/discretion.py` | Anti-spam, ore silenziose |

### Moduli servizi esterni

| File | Ruolo |
|---|---|
| `modules/google_auth.py` | OAuth2 Google |
| `modules/google_cal.py` | Google Calendar API |
| `modules/google_mail.py` | Gmail API (scope `gmail.modify`) |
| `modules/whatsapp.py` | WhatsApp Green API |
| `modules/voice.py` | TTS ElevenLabs |
| `modules/listener.py` | STT Vosk offline |
| `modules/web_search.py` | DDGS text + news |
| `modules/file_engine.py` | Elaborazione file (xlsx, pdf) |

### File dati permanenti (`data/`)

| File | Contenuto |
|---|---|
| `admin.json` | Legame permanente (PBKDF2-SHA256 + checksum) |
| `changelog.json` | Log backup `.bak` (max 200) |
| `patterns.json` | Pattern da PatternLearner |
| `lockouts.json` | Stato lockout admin (permessi 0o600) |
| `rate_limits.json` | Stato rate limiting (permessi 0o600) |

### File memoria per-utente (`memory/user_<id>/`)

- **Profilo**: `profile.json`, `active_history.json`, `conversations/`
- **Stato Cipher**: `cipher_state.json`, `thoughts.md`, `cipher_interests.json`
- **Stato utente**: `emotional_log.json`, `feedback_weights.json`, `short_term.json`
- **Obiettivi**: `goals.json`, `goals.md`
- **Notturno**: `daily_summaries.md`, `pattern_insights.md`, `voice_notes.md`, `morning_pattern.json`
- **Infrastruttura**: `discretion_state.json`, `llm_usage.json`, `action_log.json`, `realtime_context.json`
- **Autonomia**: `ethics_learned.json`, `ethics_log.md`, `contacts.json`, `episodes.json`

---

## Endpoint Flask

Pubblici (no auth): `/health`, `/web`, `/web/`
Rate limit HTTP: 30 req/min normali; 120 req/min per `/api/terminal`, `/api/files/*`, `/api/dashboard`, `/api/history`

| Endpoint | Metodo | Funzione |
|---|---|---|
| `/chat` | POST | Conversazione principale (con rate limit per-utente via sender_id) |
| `/stt` | POST | Speech-to-text Vosk |
| `/tts` | POST | Text-to-speech ElevenLabs |
| `/web` | GET | Dashboard JARVIS HUD (token server-side) |
| `/api/dashboard` | GET | Stato sistema |
| `/api/history` | GET | History conversazione |
| `/api/files/*` | GET/POST/DELETE | File manager (path validation via `path_guard`) |
| `/api/terminal` | POST | Shell sandbox via `ShellGuard` |
| `/api/calendar` | GET/POST | Google Calendar CRUD |
| `/api/calendar/<id>` | PUT/DELETE | Modifica/elimina evento |
| `/consciousness/*` | GET | Status, thoughts, goals |
| `/memory`, `/reset` | GET/POST | Memoria e reset |

---

## Dashboard web — `web/index.html`

File unico (~91 KB, HTML + CSS + JS inline). Tema JARVIS HUD rosso/cyan.

- **Base layer**: chat sempre visibile con infobar
- **Wheel selector**: icona 120px in alto a destra, apre 5 label (FS, BASH, CAL, GOALS, INFO)
- **Popup flottanti**: draggabili, z-index incrementale, più aperti simultaneamente

Vincoli UI critici:
- Non spezzare in più file
- `#chat-input font-size: 16px` — sotto 16px iOS Safari zoom su focus
- Keyboard fix mobile: `height: 100%` (non `100vh`) + `interactive-widget=resizes-content`
- Popup mobile: mai `!important` su `left`/`top` (impedirebbe drag)
- `touchmove` handler deve avere `e.preventDefault()`

---

## Guardrail Anthropic

Pattern che triggherano il safety filter:
1. Prompt user-role con `"Sei [nome]"` — usare descrizioni funzionali
2. Titoli obiettivi con termini analitici/manipolativi
3. `comportamento/00_identity.txt` con identity-override phrases
4. `emotional_reason`/`want_to_explore` con `_meta_keywords`

Moduli background (`GoalManager`, `SelfReflection`) devono sempre includere `{"role": "system", ...}` con descrizione funzionale.

---

## TODO e escalation future

- `realtime_context`: wrappare e sanificare dati meteo/news (TODO in brain.py)
- `web_search.py`: refactorare per `list[dict]` per wrapping per-snippet
- Path `/chat`: no entry in `injection_audit.log` per attacchi diretti al messaggio
- `patterns.json`: migrare da `data/` a `memory/user_<id>/`
- Multi-user: separare file globali da per-utente in `memory/`
- `admin.json`: considerare per-utente in futuro
- `.env` ha permessi `644` — dovrebbe essere `600` (`chmod 600 .env`)
