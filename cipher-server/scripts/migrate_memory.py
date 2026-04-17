#!/usr/bin/env python3
"""
scripts/migrate_memory.py – Migrazione memory/ → memory/user_simone/

SECURITY-STEP4: migra i file di memoria dalla struttura flat:
  cipher-server/memory/<file>
alla struttura per-utente:
  cipher-server/memory/user_simone/<file>

Lo script è IDEMPOTENTE:
- Se non ci sono file migrabili in memory/ (tutto già in user_simone/),
  esce senza modificare nulla.
- Se esiste un backup da migrazione parziale precedente, non ne crea
  uno nuovo.
- Se un file esiste già nella destinazione (partial migration), lo salta.

Backup obbligatorio in memory.backup_before_step4_<timestamp>/

Utilizzo:
  cd /home/Szymon/Cipher/cipher-server
  python scripts/migrate_memory.py
  python scripts/migrate_memory.py --dry-run    # simula senza modificare nulla
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ── Percorsi ──────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent
MEMORY_ROOT   = BASE_DIR / "memory"
USER_DIR      = MEMORY_ROOT / "user_simone"

# NOTE: i seguenti file sono semanticamente globali (stato interno
# di Cipher, non di un utente specifico) ma per ora vivono nella
# directory per-utente perché c'è un solo utente:
# cipher_state.json, cipher_interests.json, thoughts.md,
# goals.json, goals.md, outcome_log.json, llm_usage.json,
# realtime_context.json, discretion_state.json, night_cycle_last.json,
# morning_brief.json, morning_pattern.json, ethics_learned.json,
# ethics_log.md, action_log.json, memory_worker_state.json,
# daily_summaries.md, pattern_insights.md, voice_notes.md
#
# TODO(multi-user): separare in memory/global/ vs memory/user_<id>/


def _get_migratable_items(root: Path) -> list[Path]:
    """File e directory in MEMORY_ROOT migrabili (tutto tranne user_*/ e .gitkeep)."""
    if not root.is_dir():
        return []
    return [
        item for item in root.iterdir()
        if item.name != ".gitkeep"
        and not (item.is_dir() and item.name.startswith("user_"))
        and not item.name.startswith(".")
    ]


def _find_existing_backup(root: Path) -> Path | None:
    """Cerca un backup pre-step4 già esistente (da migrazione parziale precedente)."""
    parent = root.parent
    for d in parent.iterdir():
        if d.is_dir() and d.name.startswith("memory.backup_before_step4_"):
            return d
    return None


def _verify_backup(source: Path, backup: Path) -> bool:
    """Verifica che backup e source abbiano lo stesso numero di file."""
    src_count = sum(1 for _ in source.rglob("*") if _.is_file())
    bak_count = sum(1 for f in backup.rglob("*") if f.is_file() and f.name != "README.md")
    ok = src_count == bak_count
    if not ok:
        print(f"  ⚠️  Verifica backup fallita: {src_count} file in source, {bak_count} in backup")
    else:
        print(f"  ✓ Backup verificato: {bak_count} file")
    return ok


def _write_backup_readme(backup_path: Path, ts: str, dry_run: bool) -> None:
    """Scrive README nel backup con data limite cancellazione."""
    delete_after = (
        datetime.strptime(ts, "%Y%m%d_%H%M%S") + timedelta(days=30)
    ).strftime("%Y-%m-%d")
    readme = backup_path / "README.md"
    content = (
        f"# Backup pre-STEP4\n\n"
        f"Backup di `cipher-server/memory/` creato da "
        f"`scripts/migrate_memory.py` il {ts[:4]}-{ts[4:6]}-{ts[6:8]}.\n\n"
        f"**Cancellabile dopo:** {delete_after} "
        f"(30 giorni di stabilità del sistema)\n\n"
        f"In caso di problemi, ripristina con:\n"
        f"```bash\n"
        f"cp -a {backup_path}/* {MEMORY_ROOT}/\n"
        f"```\n"
    )
    if dry_run:
        print(f"  [DRY-RUN] Scriverebbe {readme}")
        return
    readme.write_text(content, encoding="utf-8")


def migrate(dry_run: bool = False) -> int:
    """Esegue la migrazione. Ritorna 0 su successo, 1 su errore."""
    print("=" * 60)
    print("SECURITY-STEP4: Migrazione memory/ → memory/user_simone/")
    print("=" * 60)

    # 1. Raccogli file migrabili
    migratable = _get_migratable_items(MEMORY_ROOT)
    if not migratable:
        print(f"\n✅ Nessun file migrabile in {MEMORY_ROOT}/.")
        print("   Migrazione già completa o directory vuota.")
        return 0

    print(f"\nFile/directory migrabili: {len(migratable)}")
    for item in sorted(migratable, key=lambda x: x.name):
        kind = "📂" if item.is_dir() else "📄"
        print(f"  {kind} {item.name}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    errors: list[str] = []

    # 2. Backup condizionale (skip se esiste già da parziale precedente)
    existing_backup = _find_existing_backup(MEMORY_ROOT)
    if existing_backup:
        print(f"\n[1/4] Backup già esistente: {existing_backup}")
        print("       (da migrazione parziale precedente — skip backup)")
    else:
        print(f"\n[1/4] Backup memory/ ...")
        backup_path = MEMORY_ROOT.parent / f"memory.backup_before_step4_{ts}"
        if dry_run:
            print(f"  [DRY-RUN] Creerebbe backup: {backup_path}")
        else:
            print(f"  Backup {MEMORY_ROOT} → {backup_path} ...", end=" ", flush=True)
            shutil.copytree(MEMORY_ROOT, backup_path)
            print("✓")
            _write_backup_readme(backup_path, ts, dry_run)
            if not _verify_backup(MEMORY_ROOT, backup_path):
                print("❌ Verifica backup fallita. Migrazione ANNULLATA.")
                return 1

    # 3. Crea USER_DIR con 0o700
    print(f"\n[2/4] Creazione {USER_DIR} ...")
    if not dry_run:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(USER_DIR, 0o700)
        print(f"  ✓ {USER_DIR} (0o700)")
    else:
        print(f"  [DRY-RUN] mkdir {USER_DIR} (0o700)")

    # 4. Sposta solo i file ancora in MEMORY_ROOT
    print(f"\n[3/4] Spostamento file ...")
    moved_count = 0
    for item in sorted(migratable, key=lambda x: x.name):
        dest = USER_DIR / item.name
        if dest.exists():
            print(f"  ⏭ {item.name} — già presente in destinazione, skip")
            continue
        if dry_run:
            print(f"  [DRY-RUN] {item.name} → user_simone/{item.name}")
            moved_count += 1
            continue
        try:
            shutil.move(str(item), str(dest))
            # chmod: 0o700 per directory, 0o600 per file
            if dest.is_dir():
                os.chmod(dest, 0o700)
                # chmod sui file dentro la directory spostata
                for f in dest.rglob("*"):
                    if f.is_file():
                        os.chmod(f, 0o600)
            else:
                os.chmod(dest, 0o600)
            print(f"  ✓ {item.name}")
            moved_count += 1
        except Exception as e:
            err = f"Errore spostamento {item.name}: {e}"
            print(f"  ✗ {err}")
            errors.append(err)

    # 5. Permessi su MEMORY_ROOT
    print(f"\n[4/4] Permessi ...")
    if not dry_run:
        try:
            os.chmod(MEMORY_ROOT, 0o700)
            print(f"  ✓ {MEMORY_ROOT} → 0o700")
        except OSError as e:
            print(f"  ⚠️  chmod {MEMORY_ROOT}: {e}")
    else:
        print(f"  [DRY-RUN] chmod {MEMORY_ROOT} → 0o700")

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

    print(f"✅ Migrazione completata: {moved_count} file/directory spostati.")
    print(f"\nStruttura finale {USER_DIR}:")
    for item in sorted(USER_DIR.rglob("*")):
        rel = item.relative_to(USER_DIR)
        prefix = "  📂 " if item.is_dir() else "  📄 "
        print(f"{prefix}{rel}")

    print(f"\nBackup conservato in {MEMORY_ROOT.parent}/")
    print("Cancellare manualmente dopo 30 giorni di stabilità.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migra memory/ in memory/user_simone/ (SECURITY-STEP4)"
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
