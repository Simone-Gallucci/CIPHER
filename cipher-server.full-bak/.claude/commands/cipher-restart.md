Riavvia tutti i servizi Cipher e verifica che siano attivi.

Esegui in sequenza:
1. `sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service`
2. Aspetta 2 secondi
3. `sudo systemctl is-active cipher.service cipher-telegram.service cipher-memory.service`

Riporta lo stato finale di ciascun servizio.
