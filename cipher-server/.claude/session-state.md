# Stato sessione precedente
Data: 2026-04-12

## Modifiche sessione corrente

- **CREATO** `modules/humanizer.py` — classe `Humanizer` con metodo `process(text) -> str`. Chiama `OPENROUTER_MODEL` con system prompt focalizzato, fallback silenzioso sul testo originale.
- **MODIFICATO** `modules/brain.py` — import + istanza `self._humanizer`; aggancio in `think()` a 3 punti: shortcut pensieri (riga ~1082), shortcut obiettivi (riga ~1128), return principale (dopo `_detect_topic_closure`). Check `startswith('{')` al return principale.
- **MODIFICATO** `modules/consciousness_loop.py` — import + istanza `self._humanizer`; aggancio in `_notify()` prima di `_send_telegram()`, con guard SKIP e JSON.
- **AGGIORNATO** `CLAUDE.md` — tabella moduli core + vincoli critici.
- **AGGIORNATO** `README.md` — feature list + struttura moduli.

## File modificati (git status)
 M .gitignore
 M CLAUDE.md
 M README.md
 D apprendimento/arduino.txt
 D apprendimento/carte.txt
 D apprendimento/cybersec.txt
 D apprendimento/programmazione_AL.txt
 D apprendimento/programmazione_c.txt
 D apprendimento/programmazione_cpp.txt
 D apprendimento/programmazione_flutter.txt
 D apprendimento/programmazione_html.txt
 D apprendimento/programmazione_java.txt
 D apprendimento/programmazione_paython.txt
 D apprendimento/raspberry.txt
 D apprendimento/unix.txt
 D apprendimento/win.txt
 M cipher_bot.py
 M comportamento/00_identity.txt
 D comportamento/azioni.md
 M comportamento/azioni.txt
 D comportamento/dev_protocol.txt
 M config.py
 M main.py
 M memory_worker.py
 M modules/actions.py
 M modules/brain.py
 M modules/cipher_interests.py
 M modules/consciousness_loop.py
 M modules/discretion.py
 M modules/ethics_engine.py
 M modules/filesystem.py
 M modules/goal_manager.py
 M modules/google_auth.py
 M modules/google_cal.py
 M modules/google_mail.py
 M modules/impact_tracker.py
 M modules/memory.py
 D modules/memory_service.py
 M modules/night_cycle.py
 M modules/passive_monitor.py
 M modules/pattern_learner.py
 M modules/realtime_context.py
 M modules/scheduler.py
 M modules/self_reflection.py
 D modules/text_input.py
 D run_server.sh
 M server.py
?? ../cipher-server.bak-improvements/
?? ../cipher-server.full-bak/
?? .claude/
?? 00_identity.txt.bak
?? 00_identity.txt.bak4
?? azioni.txt.bak4
?? comportamento.bak/
?? comportamento.bak5/
?? config/
?? data/
?? memory_backup_20260409_114736/
?? modules/action_log.py
?? modules/admin_manager.py
?? modules/brain.py.bak
?? modules/brain.py.bak3
?? modules/brain.py.bak5
?? modules/consciousness_loop.py.bak
?? modules/contacts.py
?? modules/discretion.py.bak
?? modules/impact_tracker.py.bak
?? modules/night_cycle.py.bak
?? modules/pattern_learner.py.bak
?? modules/utils.py

## Diff summary
 cipher-server/.gitignore                           |    1 +
 cipher-server/CLAUDE.md                            |  574 ++++++++-
 cipher-server/README.md                            |  548 ++++----
 cipher-server/apprendimento/arduino.txt            |  461 -------
 cipher-server/apprendimento/carte.txt              |  475 -------
 cipher-server/apprendimento/cybersec.txt           |  155 ---
 cipher-server/apprendimento/programmazione_AL.txt  |  480 -------
 cipher-server/apprendimento/programmazione_c.txt   |  249 ----
 cipher-server/apprendimento/programmazione_cpp.txt |  236 ----
 .../apprendimento/programmazione_flutter.txt       |  387 ------
 .../apprendimento/programmazione_html.txt          |  328 -----
 .../apprendimento/programmazione_java.txt          |  288 -----
 .../apprendimento/programmazione_paython.txt       |  326 -----
 cipher-server/apprendimento/raspberry.txt          |  417 ------
 cipher-server/apprendimento/unix.txt               |  570 ---------
 cipher-server/apprendimento/win.txt                |  658 ----------
 cipher-server/cipher_bot.py                        |  155 +--
 cipher-server/comportamento/00_identity.txt        |  178 ++-
 cipher-server/comportamento/azioni.md              |   60 -
 cipher-server/comportamento/azioni.txt             |  128 +-
 cipher-server/comportamento/dev_protocol.txt       |   29 -
 cipher-server/config.py                            |   27 +-
 cipher-server/main.py                              |   17 +-
 cipher-server/memory_worker.py                     |   52 +-
 cipher-server/modules/actions.py                   |  259 +++-
 cipher-server/modules/brain.py                     | 1351 ++++++++++++++++++--
 cipher-server/modules/cipher_interests.py          |   50 +
 cipher-server/modules/consciousness_loop.py        |  969 ++++++++++++--
 cipher-server/modules/discretion.py                |   43 +-
 cipher-server/modules/ethics_engine.py             |   50 +-
 cipher-server/modules/filesystem.py                |   16 +
 cipher-server/modules/goal_manager.py              |  194 ++-
 cipher-server/modules/google_auth.py               |   14 +
 cipher-server/modules/google_cal.py                |  183 ++-
 cipher-server/modules/google_mail.py               |  222 +++-
 cipher-server/modules/impact_tracker.py            |  149 +--
 cipher-server/modules/memory.py                    |  363 +++++-
 cipher-server/modules/memory_service.py            |  146 ---
 cipher-server/modules/night_cycle.py               |   65 +-
 cipher-server/modules/passive_monitor.py           |   14 +-
 cipher-server/modules/pattern_learner.py           |  259 ++--
 cipher-server/modules/realtime_context.py          |   15 +-
 cipher-server/modules/scheduler.py                 |  203 +--
 cipher-server/modules/self_reflection.py           |  177 ++-
 cipher-server/modules/text_input.py                |   41 -
 cipher-server/run_server.sh                        |    4 -
 cipher-server/server.py                            |   32 +-
 47 files changed, 4815 insertions(+), 6803 deletions(-)

## Ultimi commit
1236c7d Upload
6902337 Migliorie comportamento, TTS vocale Telegram, rimozione UI web
0373c6d Initial commit
