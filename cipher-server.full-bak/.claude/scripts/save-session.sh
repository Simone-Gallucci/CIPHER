#!/bin/bash
# Salva lo stato della sessione corrente in .claude/session-state.md
# Viene eseguito dal hook Stop alla fine di ogni sessione Claude Code

cd /home/Szymon/Cipher/cipher-server

{
  echo "# Stato sessione precedente"
  echo "Data: $(date '+%Y-%m-%d %H:%M')"
  echo ""

  echo "## File modificati (git status)"
  git status --short 2>/dev/null || echo "nessuna modifica"
  echo ""

  echo "## Diff summary"
  git diff --stat 2>/dev/null
  git diff --cached --stat 2>/dev/null
  echo ""

  echo "## Ultimi commit"
  git log --oneline -5 2>/dev/null

} > /home/Szymon/Cipher/cipher-server/.claude/session-state.md
