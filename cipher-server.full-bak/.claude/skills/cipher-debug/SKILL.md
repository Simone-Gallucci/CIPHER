---
name: cipher-debug
description: Usare quando Cipher ha un errore, non risponde, crasha, si comporta in modo strano, o l'utente menziona "non funziona", "errore", "crash", "bug", "log", "journalctl".
version: 1.0.0
---

# cipher-debug

Workflow per diagnosticare e risolvere problemi su Cipher.

## 1. Leggi i log

```bash
sudo journalctl -u cipher -n 50 --no-pager
```

## 2. Identifica la componente

| Sintomo nel log | Componente |
|---|---|
| `ConsciousnessLoop` / `consciousness` | Loop autonomo |
| `ActionDispatcher` / `execute` | Dispatcher azioni |
| `telegram` / `cipher_bot` | Bot Telegram |
| `Flask` / `server` / `API` | API REST |
| `Brain` / `_history` | Cervello / history |

## 3. Checklist per ogni componente

### ConsciousnessLoop
- Le chiamate LLM girano in thread separato? Se no → deadlock
- `CONSCIOUSNESS_ENABLED` è `true` nel `.env`?

### Brain / moduli opzionali
I moduli `_impact_tracker`, `_pattern_learner`, `_episodic_memory` vengono iniettati **dopo** l'init. Controlla sempre:
```python
if self._impact_tracker:
    ...
```
Se manca il check → `NoneType` error.

### ActionDispatcher
- Path hardcodati? Devono usare `Config.*`
- Scrittura fuori da `Config.HOME_DIR`? → PermissionError o comportamento inatteso
- Azione sensibile senza `_pending_*`? → esegue senza consenso

### Bot Telegram
- Token valido in `.env`?
- `TELEGRAM_ALLOWED_ID` corrisponde all'ID corretto?

## 4. Fix e verifica

Dopo il fix:
```bash
sudo systemctl restart cipher.service
sudo journalctl -u cipher -n 20 --no-pager
```

Cerca che il log non mostri lo stesso errore.
