#!/bin/bash
# install.sh – Installa e configura Cipher Client da zero
# Funziona su Linux (Debian/Ubuntu/Parrot) e Termux (Android)

set -e

# ── Colori ────────────────────────────────────────────────────────────
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()      { echo -e "  ${GREEN}✓ $1${NC}"; }
warn()    { echo -e "  ${YELLOW}⚠ $1${NC}"; }
info()    { echo -e "  ${CYAN}→ $1${NC}"; }
err()     { echo -e "  ${RED}✗ $1${NC}"; }
section() { echo -e "\n${CYAN}${BOLD}── $1 ──────────────────────────────────────────${NC}"; }
ask() {
    local prompt="$1" var="$2" default="$3"
    echo -ne "${CYAN}  → ${prompt}${NC}"
    [ -n "$default" ] && echo -ne " ${YELLOW}[${default}]${NC}"
    echo -ne ": "
    read -r input
    eval "$var=\"${input:-$default}\""
}
ask_secret() {
    local prompt="$1" var="$2"
    echo -ne "${CYAN}  → ${prompt}${NC}: "
    read -rs input
    echo ""
    eval "$var=\"$input\""
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

clear
echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     C I P H E R  –  Client Setup            ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Rileva ambiente ───────────────────────────────────────────────────
if [ -n "$TERMUX_VERSION" ] || [ -d "/data/data/com.termux" ]; then
    ENV="termux"
else
    ENV="linux"
fi
echo -e "  Ambiente: ${CYAN}${BOLD}${ENV}${NC}"

# ── 1. Dipendenze sistema ─────────────────────────────────────────────
section "1/4  Dipendenze sistema"

if [ "$ENV" = "termux" ]; then
    pkg install -y python mpg123 termux-api ffmpeg
    ok "Dipendenze Termux installate"
else
    sudo apt-get update -q
    sudo apt-get install -y \
        python3 python3-pip python3-venv \
        mpg123 ffmpeg portaudio19-dev libsndfile1 \
        --no-install-recommends -q
    ok "Dipendenze sistema installate"
fi

# ── 2. Venv + dipendenze Python ───────────────────────────────────────
section "2/4  Ambiente Python"

if [ "$ENV" = "termux" ]; then
    # Termux: pip globale, no venv
    if [ -z "$VIRTUAL_ENV" ]; then
        pip install --upgrade pip -q
        pip install requests rich python-dotenv elevenlabs sounddevice -q
        ok "Dipendenze Python installate (Termux)"
    fi
else
    # Linux: venv auto-create e re-exec dentro di esso
    if [ -z "$VIRTUAL_ENV" ]; then
        if [ ! -d "venv" ]; then
            python3 -m venv venv
            ok "Venv creato"
        fi
        exec bash -c "source \"${SCRIPT_DIR}/venv/bin/activate\" && exec bash \"$0\" $*"
    fi
    pip install --upgrade pip -q
    pip install requests rich python-dotenv elevenlabs sounddevice -q
    ok "Dipendenze Python installate"
fi

# ── 3. Configurazione .env ────────────────────────────────────────────
section "3/4  Configurazione"

if [ -f ".env" ]; then
    warn ".env già esistente — leggo i valori esistenti"
    CIPHER_SERVER_URL=$(grep "^CIPHER_SERVER_URL=" .env | cut -d'=' -f2-)
    ELEVENLABS_API_KEY=$(grep "^ELEVENLABS_API_KEY=" .env | cut -d'=' -f2-)
    ELEVENLABS_VOICE_ID=$(grep "^ELEVENLABS_VOICE_ID=" .env | cut -d'=' -f2-)
    ok "Configurazione caricata da .env esistente"
else
    echo ""
    ask "URL del server Cipher (es. http://IP:5000)" CIPHER_SERVER_URL "http://100.70.208.36:5000"

    echo ""
    echo -ne "  ${CYAN}→ Configurare ElevenLabs TTS? [s/N]${NC}: "
    read -r use_elevenlabs
    if [[ "$use_elevenlabs" =~ ^[sS]$ ]]; then
        ask_secret "ELEVENLABS_API_KEY" ELEVENLABS_API_KEY
        ask "ELEVENLABS_VOICE_ID" ELEVENLABS_VOICE_ID "JBFqnCBsd6RMkjVDRZzb"
    else
        ELEVENLABS_API_KEY=""
        ELEVENLABS_VOICE_ID="JBFqnCBsd6RMkjVDRZzb"
        warn "ElevenLabs saltato — TTS disabilitato"
    fi

    cat > .env << ENVEOF
# Cipher Client – configurazione generata da install.sh

CIPHER_SERVER_URL=${CIPHER_SERVER_URL}

# ElevenLabs (TTS)
ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY}
ELEVENLABS_VOICE_ID=${ELEVENLABS_VOICE_ID}

# Microfono (opzionale)
MIC_DEVICE_INDEX=-1
WAKE_WORDS=cipher,jarvis,ehi,ci sei
ENVEOF
    ok ".env creato"
fi

# ── 4. Systemd (solo Linux, opzionale) ───────────────────────────────
section "4/4  Avvio automatico"

if [ "$ENV" = "linux" ]; then
    echo -ne "  ${CYAN}→ Installare come servizio systemd (avvio automatico)? [s/N]${NC}: "
    read -r install_service

    if [[ "$install_service" =~ ^[sS]$ ]]; then
        SERVICE_USER="${USER:-$(whoami)}"
        VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python"

        sudo tee /etc/systemd/system/cipher-client.service > /dev/null << SERVICEEOF
[Unit]
Description=Cipher Client
After=network.target sound.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_PYTHON} ${SCRIPT_DIR}/cipher_client.py
Restart=on-failure
RestartSec=5
EnvironmentFile=${SCRIPT_DIR}/.env

[Install]
WantedBy=multi-user.target
SERVICEEOF

        sudo systemctl daemon-reload
        ok "Servizio systemd installato"

        echo -ne "  ${CYAN}→ Abilitare all'avvio del sistema? [s/N]${NC}: "
        read -r enable_boot
        if [[ "$enable_boot" =~ ^[sS]$ ]]; then
            sudo systemctl enable cipher-client.service
            ok "Servizio abilitato al boot"
        fi
    fi
else
    warn "Systemd non disponibile su Termux — avvio manuale"
fi

# ── Riepilogo finale ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     Installazione completata!                ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Server configurato: ${CYAN}${CIPHER_SERVER_URL}${NC}"
echo ""
echo -e "${BOLD}  ── Avvio manuale ───────────────────────────────────${NC}"

if [ "$ENV" = "termux" ]; then
    echo -e "  ${CYAN}python cipher_client.py${NC}"
else
    echo -e "  ${CYAN}source venv/bin/activate && python cipher_client.py${NC}"
    echo -e "  oppure: ${CYAN}./run_client.sh${NC}"
fi

if [[ "${install_service}" =~ ^[sS]$ ]]; then
    echo ""
    echo -e "${BOLD}  ── Gestione servizio systemd ────────────────────────${NC}"
    echo -e "  Avvia  : ${CYAN}sudo systemctl start cipher-client${NC}"
    echo -e "  Stato  : ${CYAN}sudo systemctl status cipher-client${NC}"
    echo -e "  Log    : ${CYAN}journalctl -fu cipher-client${NC}"
    echo -e "  Ferma  : ${CYAN}sudo systemctl stop cipher-client${NC}"
fi

echo ""
echo -ne "  ${CYAN}→ Avviare il client adesso? [S/n]${NC}: "
read -r avvia

if [[ ! "$avvia" =~ ^[nN]$ ]]; then
    echo ""
    ok "Avvio Cipher Client..."
    echo ""
    exec python cipher_client.py
fi
