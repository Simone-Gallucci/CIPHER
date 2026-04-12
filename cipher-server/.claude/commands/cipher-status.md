Mostra lo stato di tutti i servizi Cipher e le ultime 20 righe di log.

Esegui in sequenza:
1. `sudo systemctl status cipher.service cipher-telegram.service cipher-memory.service cipher-funnel.service --no-pager`
2. `sudo journalctl -u cipher -n 20 --no-pager`

Riporta: quali servizi sono attivi, quali no, e se ci sono errori evidenti nel log.
