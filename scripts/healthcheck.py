#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SERVICE = "hackathon-bot.service"


def main() -> int:
    data_dir = Path(os.getenv("DATA_DIR", "/var/lib/hackathon-bot"))
    heartbeat = Path(os.getenv("HEARTBEAT_PATH", str(data_dir / "heartbeat")))
    max_age = int(os.getenv("HEALTH_HEARTBEAT_MAX_MINUTES", "10")) * 60
    problems: list[str] = []

    try:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", SERVICE],
            timeout=10,
            check=False,
        )
        if active.returncode != 0:
            problems.append(f"{SERVICE} is not active")
    except Exception as exc:
        problems.append(f"systemctl check failed: {exc}")

    if not heartbeat.exists():
        problems.append("heartbeat file missing")
    else:
        try:
            age = time.time() - heartbeat.stat().st_mtime
            if age > max_age:
                problems.append(f"heartbeat is stale ({int(age)}s)")
        except OSError as exc:
            problems.append(f"heartbeat stat failed: {exc}")

    db_path = Path(os.getenv("DB_PATH", str(data_dir / "bot.db")))
    if not db_path.exists():
        problems.append("database file missing")

    if not problems:
        return 0

    message = "Hackathon bot health check:\n" + "\n".join(f"- {p}" for p in problems)
    print(message, file=sys.stderr)
    _alert_throttled(message)
    return 1


def _alert_throttled(message: str) -> None:
    token = os.getenv("TG_TOKEN", "").strip()
    raw_admins = os.getenv("TG_ADMIN_IDS", "").replace(";", ",").replace(" ", ",")
    admins = [int(x) for x in raw_admins.split(",") if x.strip().isdigit()]
    if not token or not admins:
        return
    state_path = Path(os.getenv("DATA_DIR", "/var/lib/hackathon-bot")) / "health_alert_ts"
    now = time.time()
    try:
        last = float(state_path.read_text().strip())
    except (OSError, ValueError):
        last = 0.0
    if now - last < 30 * 60:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(str(now), encoding="utf-8")
        os.chmod(state_path, 0o600)
    except OSError:
        pass
    for admin in admins:
        try:
            body = json.dumps({"chat_id": admin, "text": message[:3500]}).encode()
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10):
                pass
        except Exception as exc:
            print(f"alert failed for {admin}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
