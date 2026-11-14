#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    data_dir = Path(os.getenv("DATA_DIR", "/var/lib/hackathon-bot"))
    db_path = Path(os.getenv("DB_PATH", str(data_dir / "bot.db")))
    backup_dir = Path(os.getenv("BACKUP_DIR", "/var/backups/hackathon-bot"))
    keep_days = int(os.getenv("BACKUP_KEEP_DAYS", "7"))

    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = backup_dir / f"bot-{stamp}.db"
    tmp_path = backup_dir / f".bot-{stamp}.tmp"

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(tmp_path, timeout=30)
    try:
        source.backup(target)
        check = target.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            print(f"integrity check failed: {check}", file=sys.stderr)
            return 1
    finally:
        target.close()
        source.close()

    os.chmod(tmp_path, 0o600)
    tmp_path.replace(final_path)

    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    for old in backup_dir.glob("bot-*.db"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass

    print(f"backup written: {final_path} ({final_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
