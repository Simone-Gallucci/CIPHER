# CLAUDE.md

## Comandi

```bash
source venv/bin/activate
python server.py             # avvia tutto (API + Telegram + loop)
python main.py --mode text   # CLI
pip install -r requirements.txt
```

## Vincoli

- Non hardcodare mai path o valori: usare sempre `Config.*`
- Scrittura file permessa **solo** dentro `Config.HOME_DIR` (`home/`)
- Memoria va in `Config.MEMORY_DIR` (`memory/`)
- Non rimuovere il troncamento di `Brain._history` in `brain.py:254`

## Gotcha

- Le chiamate LLM da `ConsciousnessLoop` **devono girare in thread separato** — farlo direttamente causa deadlock
- I moduli opzionali di `Brain` (`_impact_tracker`, `_pattern_learner`, `_episodic_memory`) vengono iniettati da `ConsciousnessLoop` dopo l'init — controllare sempre `if self._xxx:`
- Il system prompt si costruisce concatenando i file in `comportamento/` in ordine alfabetico — per cambiare il comportamento di Cipher editare quelli, non il codice
- Le azioni sensibili richiedono consenso esplicito: il dispatcher le mette in `_pending_*` e aspetta risposta dell'utente prima di eseguire. Frasi che danno consenso: `sì/si/ok/procedi/esegui/confermo` — frasi che annullano: `no/annulla/stop` (lista completa in `actions.py:CONSENT_PHRASES`)

## Aggiungere un'azione

1. Documentarla in `comportamento/azioni.md`
2. Implementarla in `ActionDispatcher.execute()` (`modules/actions.py`)
3. Se richiede consenso: impostare `self._pending_*` e restituire la richiesta all'utente
