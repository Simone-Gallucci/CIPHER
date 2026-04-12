---
name: cipher-add-action
description: Usare quando l'utente chiede di aggiungere, creare o implementare una nuova azione in Cipher, oppure menziona "nuova azione", "ActionDispatcher", "actions.py", o vuole che Cipher sappia fare qualcosa di nuovo.
version: 1.0.0
---

# cipher-add-action

Workflow completo per aggiungere una nuova azione a Cipher.

## Passi obbligatori

### 1. Leggi prima il codice esistente

Leggi `modules/actions.py` per capire il pattern del dispatcher e le azioni già esistenti.
Leggi `comportamento/azioni.txt` per capire come sono documentate.

### 2. Documenta in comportamento/azioni.txt

Aggiungi la documentazione dell'azione nel file `comportamento/azioni.txt` seguendo il formato JSON già presente:

```
{"action": "nome_azione", "params": {"param1": "...", "param2": "..."}}
```

Aggiungi commenti che spieghino il comportamento, i parametri e i casi d'uso.

### 3. Implementa in ActionDispatcher.execute()

In `modules/actions.py`, aggiungi un branch nell'`ActionDispatcher.execute()`:

```python
elif action == "nome_azione":
    # implementazione
    return {"result": "..."}
```

**Regole critiche:**
- Mai hardcodare path: usa sempre `Config.*`
- Scrittura file solo dentro `Config.HOME_DIR` o `Config.MEMORY_DIR`
- Non usare path assoluti diretti

### 4. Se richiede consenso

Se l'azione è sensibile (modifica file, invia messaggi, esegue comandi):

```python
elif action == "nome_azione":
    self._pending_action = params
    return "Vuoi che esegua nome_azione con questi parametri: ...? (sì/no)"
```

Le frasi di consenso/annullamento sono in `actions.py:CONSENT_PHRASES`.

### 5. Verifica compatibilità con ConsciousnessLoop

- Le chiamate LLM devono girare in thread separato — mai chiamare il modello direttamente da `ConsciousnessLoop`
- I moduli opzionali (`_impact_tracker`, `_pattern_learner`, `_episodic_memory`) vengono iniettati dopo l'init: controlla sempre `if self._xxx:`

### 6. Riavvia e verifica

```bash
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service
sudo journalctl -u cipher -n 30 --no-pager
```

Cerca errori relativi alla nuova azione nel log.
