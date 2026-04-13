# Stato sessione precedente
Data: 2026-04-13 13:17

## File modificati (git status)
 M .claude/session-state.md
 ? .claude/worktrees/elated-feistel
 M CLAUDE.md
 M README.md
 M comportamento/00_identity.txt
 M comportamento/azioni.txt
 M main.py
 M modules/actions.py
 M modules/admin_manager.py
 M modules/brain.py
 M modules/cipher_interests.py
 M modules/consciousness_loop.py
 M modules/discretion.py
 M modules/episodic_memory.py
 M modules/file_engine.py
 D modules/humanizer.py
 D modules/impact_tracker.py
 M modules/night_cycle.py
 M modules/passive_monitor.py
 M modules/pattern_learner.py
 M modules/realtime_context.py
 M modules/voice.py
 M server.py
?? modules/llm_usage.py
?? modules/web_search.py

## Diff summary
 cipher-server/.claude/session-state.md      | 128 ++-----------
 cipher-server/CLAUDE.md                     |  44 +++--
 cipher-server/README.md                     |  27 +--
 cipher-server/comportamento/00_identity.txt |  31 +++-
 cipher-server/comportamento/azioni.txt      |   3 +
 cipher-server/main.py                       |   6 +-
 cipher-server/modules/actions.py            |  66 +++++++
 cipher-server/modules/admin_manager.py      |  19 +-
 cipher-server/modules/brain.py              | 275 ++++++++++++++++++----------
 cipher-server/modules/cipher_interests.py   |  21 ++-
 cipher-server/modules/consciousness_loop.py |  53 +-----
 cipher-server/modules/discretion.py         |  12 +-
 cipher-server/modules/episodic_memory.py    |  50 ++++-
 cipher-server/modules/file_engine.py        |  29 ++-
 cipher-server/modules/humanizer.py          |  62 -------
 cipher-server/modules/impact_tracker.py     |  47 -----
 cipher-server/modules/night_cycle.py        |  12 +-
 cipher-server/modules/passive_monitor.py    |   4 -
 cipher-server/modules/pattern_learner.py    |  40 ++--
 cipher-server/modules/realtime_context.py   |   5 +-
 cipher-server/modules/voice.py              |  35 ++--
 cipher-server/server.py                     |  36 +++-
 22 files changed, 517 insertions(+), 488 deletions(-)

## Ultimi commit
b2a7371 fix
c387659 fix
1236c7d Upload
6902337 Migliorie comportamento, TTS vocale Telegram, rimozione UI web
0373c6d Initial commit
