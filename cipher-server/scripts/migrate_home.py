#!/usr/bin/env python3
"""
scripts/migrate_home.py – Migrazione one-shot home/ → home/user_simone/

SECURITY-STEP2: migra i file esistenti dalla struttura flat:
  cipher-server/home/<file>
  cipher-server/uploads/<file>

alla struttura per-utente:
  cipher-server/home/user_simone/<file>
  cipher-server/home/user_simone/uploads/<file>

Lo script è IDEMPOTENTE: se home/user_simone/ esiste già e contiene
almeno un file, stampa "migrazione già effettuata" ed esce senza modificare
nulla.

Backup OBBLIGATORIO prima di qualsiasi spostamento:
  home.backup_before_step2_<YYYYMMDD_HHMMSS>/
  uploads.backup_before_step2_<YYYYMMDD_HHMMSS>/

I backup NON vengono cancellati automaticamente. Cancellare manualmente
dopo almeno 7 giorni di stabilità del sistema.

Utilizzo:
  cd /home/Szymon/Cipher/cipher-server
  python scripts/migrate_home.py
  python scripts/migrate_home.py --dry-run    # simula senza modificare nulla
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ── Percorsi ──────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent
HOME_ROOT   = BASE_DIR / "home"
USER_HOME   = HOME_ROOT / "user_simone"
USER_UPL    = USER_HOME / "uploads"
LEGACY_UPL  = BASE_DIR / "uploads"


def _count_real_files(directory: Path) -> int:
    """Conta file non-nascosti e non-DEPRECATED.md in una directory."""
    if not directory.is_dir():
        return 0
    return sum(
        1 for f in directory.iterdir()
        if f.name not in {"DEPRECATED.md", ".gitkeep"}
    )


def _check_idempotency() -> bool:
    """
    Ritorna True se la migrazione è già stata effettuata.
    Criterio: USER_HOME esiste e contiene almeno un file (non contando uploads/).
    """
    if not USER_HOME.is_dir():
        return False
    # Controlla se ci sono file diretti (non la sola uploads/)
    items = [
        i for i in USER_HOME.iterdir()
        if i.name not in {"uploads", ".gitkeep"}
    ]
    return len(items) > 0


def _backup(source: Path, ts: str, dry_run: bool) -> Path:
    """Crea backup di source in source.backup_before_step2_<ts>/."""
    backup_name = f"{source.name}.backup_before_step2_{ts}"
    backup_path = source.parent / backup_name
    if dry_run:
        print(f"  [DRY-RUN] Creerebbe backup: {backup_path}")
        return backup_path
    print(f"  Backup {source} → {backup_path} ...", end=" ", flush=True)
    shutil.copytree(source, backup_path)
    print("✓")
    return backup_path


def _write_backup_readme(backup_path: Path, source_name: str, ts: str, dry_run: bool) -> None:
    """Scrive README nel backup con data limite cancellazione."""
    delete_after = (
        datetime.strptime(ts, "%Y%m%d_%H%M%S") + timedelta(days=30)
    ).strftime("%Y-%m-%d")
    readme = backup_path / "README.md"
    content = (
        f"# Backup pre-STEP2\n\n"
        f"Backup di `cipher-server/{source_name}/` creato da "
        f"`scripts/migrate_home.py` il {ts[:8][:4]}-{ts[4:6]}-{ts[6:8]}.\n\n"
        f"**Cancellabile dopo:** {delete_after} "
        f"(7+ giorni di stabilità del sistema)\n\n"
        f"In caso di problemi, ripristina con:\n"
        f"```bash\n"
        f"cp -a {backup_path}/ {BASE_DIR / source_name}/\n"
        f"```\n"
    )
    if dry_run:
        print(f"  [DRY-RUN] Scriverebbe {readme}")
        return
    readme.write_text(content, encoding="utf-8")


def _verify_backup(source: Path, backup: Path) -> bool:
    """Verifica che backup e source abbiano lo stesso numero di file."""
    src_count = sum(1 for _ in source.rglob("*") if _.is_file())
    bak_count  = sum(1 for f in backup.rglob("*") if f.is_file() and f.name != "README.md")
    ok = src_count == bak_count
    if not ok:
        print(f"  ⚠️  Verifica backup fallita: {src_count} file in source, {bak_count} in backup")
    else:
        print(f"  ✓ Backup verificato: {bak_count} file")
    return ok


def migrate(dry_run: bool = False) -> int:
    """
    Esegue la migrazione. Ritorna 0 su successo, 1 su errore.
    """
    print("=" * 60)
    print("SECURITY-STEP2: Migrazione home/ → home/user_simone/")
    print("=" * 60)

    # 1. Idempotenza
    if _check_idempotency():
        print(f"\n✅ Migrazione già effettuata: {USER_HOME} contiene file.")
        print("   Nessuna modifica eseguita.")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    errors: list[str] = []
    moved_count = 0

    # 2. Raccolta file da spostare
    home_items = [
        i for i in HOME_ROOT.iterdir()
        if i.name != "user_simone"
    ] if HOME_ROOT.is_dir() else []

    upload_items = [
        i for i in LEGACY_UPL.iterdir()
        if i.name != "DEPRECATED.md"
    ] if LEGACY_UPL.is_dir() else []

    print(f"\nFile in home/         : {len(home_items)}")
    print(f"File in uploads/      : {len(upload_items)}")

    if not home_items and not upload_items:
        print("\nNessun file da migrare. Creo struttura vuota.")

    # 3. Backup home/
    if HOME_ROOT.is_dir() and home_items:
        print(f"\n[1/6] Backup home/ ...")
        backup_home = _backup(HOME_ROOT, ts, dry_run)
        if not dry_run:
            _write_backup_readme(backup_home, "home", ts, dry_run)
            if not _verify_backup(HOME_ROOT, backup_home):
                errors.append("Verifica backup home/ fallita")

    # 4. Backup uploads/
    if LEGACY_UPL.is_dir() and upload_items:
        print(f"\n[2/6] Backup uploads/ ...")
        backup_upl = _backup(LEGACY_UPL, ts, dry_run)
        if not dry_run:
            _write_backup_readme(backup_upl, "uploads", ts, dry_run)
            if not _verify_backup(LEGACY_UPL, backup_upl):
                errors.append("Verifica backup uploads/ fallita")

    if errors and not dry_run:
        print(f"\n❌ Errori nel backup: {errors}")
        print("   Migrazione ANNULLATA per sicurezza.")
        return 1

    # 5. Crea struttura destinazione
    print(f"\n[3/6] Creazione {USER_HOME} ...")
    if not dry_run:
        USER_HOME.mkdir(parents=True, exist_ok=True)
        os.chmod(USER_HOME, 0o700)
        USER_UPL.mkdir(parents=True, exist_ok=True)
        os.chmod(USER_UPL, 0o700)
    else:
        print(f"  [DRY-RUN] mkdir {USER_HOME} (0o700)")
        print(f"  [DRY-RUN] mkdir {USER_UPL} (0o700)")

    # 6. Sposta file da home/ → home/user_simone/
    print(f"\n[4/6] Spostamento file home/ → home/user_simone/ ...")
    for item in home_items:
        dest = USER_HOME / item.name
        if dry_run:
            print(f"  [DRY-RUN] {item} → {dest}")
            moved_count += 1
            continue
        try:
            shutil.move(str(item), str(dest))
            print(f"  ✓ {item.name}")
            moved_count += 1
        except Exception as e:
            err = f"Errore spostamento {item.name}: {e}"
            print(f"  ✗ {err}")
            errors.append(err)

    # 7. Sposta file da uploads/ → home/user_simone/uploads/
    print(f"\n[5/6] Spostamento file uploads/ → home/user_simone/uploads/ ...")
    for item in upload_items:
        dest = USER_UPL / item.name
        if dry_run:
            print(f"  [DRY-RUN] {item} → {dest}")
            moved_count += 1
            continue
        try:
            shutil.move(str(item), str(dest))
            print(f"  ✓ {item.name}")
            moved_count += 1
        except Exception as e:
            err = f"Errore spostamento uploads/{item.name}: {e}"
            print(f"  ✗ {err}")
            errors.append(err)

    # 8. Crea uploads/DEPRECATED.md
    print(f"\n[6/6] Creazione uploads/DEPRECATED.md ...")
    deprecated_md = LEGACY_UPL / "DEPRECATED.md"
    deprecated_content = (
        "# DEPRECATED\n\n"
        "Questa directory non è più in uso.\n\n"
        "I file di upload sono ora in `home/user_<id>/uploads/`.\n"
        "Non scrivere file qui direttamente.\n"
    )
    if dry_run:
        print(f"  [DRY-RUN] Scriverebbe {deprecated_md}")
    else:
        if LEGACY_UPL.is_dir():
            deprecated_md.write_text(deprecated_content, encoding="utf-8")
            print(f"  ✓ {deprecated_md}")
        else:
            print(f"  (uploads/ non esiste, salto)")

    # 9. Applica permessi 0o700
    if not dry_run:
        try:
            os.chmod(HOME_ROOT, 0o700)
        except OSError:
            pass

    # Report finale
    print("\n" + "=" * 60)
    if dry_run:
        print(f"[DRY-RUN] Avrebbe spostato {moved_count} elementi.")
        print("Nessuna modifica effettuata.")
        return 0

    if errors:
        print(f"⚠️  Migrazione parziale: {moved_count} file spostati, {len(errors)} errori:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"✅ Migrazione completata: {moved_count} file spostati.")
    print(f"\nStruttura finale {USER_HOME}:")
    for item in sorted(USER_HOME.rglob("*")):
        rel = item.relative_to(USER_HOME)
        prefix = "  📂 " if item.is_dir() else "  📄 "
        print(f"{prefix}{rel}")

    print(f"\nBackup conservati in {BASE_DIR}/")
    print("Cancellare manualmente dopo 7+ giorni di stabilità.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migra home/ e uploads/ in home/user_simone/ (SECURITY-STEP2)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula la migrazione senza modificare nulla",
    )
    args = parser.parse_args()
    sys.exit(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
