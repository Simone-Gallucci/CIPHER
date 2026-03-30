#!/bin/bash
cd "$(dirname "$0")"
# Avvia PulseAudio se su Termux
if [ -d "/data/data/com.termux" ]; then
    pulseaudio --start 2>/dev/null
fi
# Attiva venv se presente
[ -d "venv" ] && source venv/bin/activate
python cipher_client.py
