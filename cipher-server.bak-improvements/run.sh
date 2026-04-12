#!/bin/bash

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# Auto-entra nel venv
if [ -z "$VIRTUAL_ENV" ]; then
    if [ ! -d "venv" ]; then
        echo -e "${YELLOW}⚠ Venv non trovato — esegui prima ./setup.sh${NC}"
        exit 1
    fi
    exec bash -c "source venv/bin/activate && exec bash \"$0\" $*"
fi

clear
echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║         C I P H E R  –  Avvio               ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${CYAN}1${NC}) ${BOLD}Full mode${NC}     — Testo + Microfono + Voce TTS"
echo -e "  ${CYAN}2${NC}) ${BOLD}Testo + Voce${NC}  — Tastiera + risposta vocale"
echo -e "  ${CYAN}3${NC}) ${BOLD}Solo testo${NC}    — Nessun audio"
echo ""
echo -ne "  ${CYAN}→ Scelta [1/2/3]${NC}: "
read -r scelta

case "$scelta" in
    1) exec python main.py --mode both ;;
    2) exec python main.py --mode text ;;
    3) exec python main.py --mode text --no-tts ;;
    *) echo -e "${YELLOW}Scelta non valida${NC}" && exit 1 ;;
esac
