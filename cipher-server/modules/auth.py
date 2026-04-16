"""
modules/auth.py – Identità utente corrente e proprietario del sistema.

SECURITY-STEP2: centralizza user_id in un punto solo. Quando passeremo al
multi-user, modificare SOLO questo file e tutto si adatta.

Due funzioni distinte per chiarezza semantica:
  get_current_user_id()  → chi sta facendo la richiesta (API, Telegram)
  get_system_owner_id()  → proprietario del sistema (task autonomi senza
                           richiesta utente associata)

Oggi entrambe ritornano "simone". La distinzione serve domani, quando un
utente guest != il proprietario del sistema e i task autonomi devono
comunque scrivere nella home corretta.
"""


def get_current_user_id() -> str:
    """
    Ritorna l'user_id della richiesta corrente.

    Usato da: endpoint Flask (/api/files/*), actions.py dispatch,
    file_engine, shell_guard (terminale web + shell_exec Telegram).

    TODO multi-user: leggere da Flask request context / JWT / session.
    Esempio futuro:
        from flask import g
        return g.user_id
    """
    return "simone"


def get_system_owner_id() -> str:
    """
    Ritorna l'user_id del proprietario del sistema Cipher.

    Usato da: consciousness_loop, goal_manager, night_cycle — task autonomi
    che partono da timer, non da richieste HTTP/Telegram, e non hanno un
    "utente corrente" associato.

    Distinto da get_current_user_id() per chiarezza semantica:
    domani un utente guest che usa il sistema non deve avere accesso
    ai file del proprietario nei task autonomi.

    TODO multi-user: leggere da config/env, non hardcoded.
    """
    return "simone"
