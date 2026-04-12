---
name: cipher-memory
description: Usare quando l'utente vuole leggere, modificare o ispezionare la memoria di Cipher, i suoi obiettivi, il suo stato, o i file in memory/. Anche quando vuole cambiare comportamento editando i file in comportamento/.
version: 1.0.0
---

# cipher-memory

Workflow per leggere e modificare la memoria e il comportamento di Cipher.

## File di memoria principali

| File | Contenuto |
|---|---|
| `memory/goals.json` | Obiettivi autonomi attivi di Cipher |
| `memory/cipher_state.json` | Stato interno corrente |
| `memory/ethics_log.md` | Log delle valutazioni etiche |
| `memory/pattern_insights.md` | Pattern appresi dalle conversazioni |

## Leggere la memoria

Usa `project_read` o leggi direttamente i file in `memory/`.
Non usare path assoluti: tutti i path sono relativi a `Config.MEMORY_DIR` (`memory/`).

## Modificare la memoria

**Regola critica:** scrittura permessa **solo** dentro `Config.MEMORY_DIR`.

Per modificare obiettivi o stato: edita i file JSON direttamente.
Per cancellare un obiettivo: rimuovi la entry dal JSON, non il file.

## Modificare il comportamento

I file in `comportamento/` vengono letti in **ordine alfabetico** e concatenati per formare il system prompt.

File presenti:
- `00_identity.txt` — personalità, tono, regole comunicazione
- `azioni.txt` — documentazione azioni disponibili
- `dev_protocol.txt` — regole di sviluppo interno
- `persone.md` — persone note a Cipher

Per cambiare come risponde Cipher: edita questi file, non il codice.

**Dopo ogni modifica al comportamento:**
```bash
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service
```

Il riavvio è obbligatorio perché il system prompt viene caricato all'avvio.
