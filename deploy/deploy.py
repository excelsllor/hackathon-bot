#!/usr/bin/env python3
"""Upload a new release, install dependencies if needed, switch the symlink, restart."""
from __future__ import annotations

import hashlib
import os
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import paramiko

HOST = os.getenv("VPS_HOST", "92.242.164.222")
USER = os.getenv("VPS_USER", "root")
KEY_PATH = Path(os.getenv("VPS_KEY", str(Path.home() / ".ssh" / "hackathon_bot_ed25519")))
ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
RELEASE = f"/opt/hackathon-bot/releases/{STAMP}"
REMOTE_TAR = f"/tmp/hackathon-bot-{STAMP}.tar.gz"


def connect() -> paramiko.SSHClient:
    last: Exception | None = None
    for attempt in range(5):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                HOST,
                port=22,
                username=USER,
                key_filename=str(KEY_PATH),
                timeout=30,
                banner_timeout=40,
                auth_timeout=30,
                look_for_keys=False,
                allow_agent=False,
            )
            return client
        except Exception as exc:
            last = exc
            client.close()
            print(f"SSH connect retry {attempt + 1}/5: {type(exc).__name__}")
            time.sleep(10)
    raise RuntimeError(f"SSH connection failed: {last}")


def run(client: paramiko.SSHClient, command: str, timeout: int = 1200) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    return stdout.channel.recv_exit_status(), out, err


def tarball() -> Path:
    temp = Path(tempfile.mkdtemp(prefix="hackathon-bot-"))
    archive = temp / f"hackathon-bot-{STAMP}.tar.gz"
    exclude = {
        ".venv",
        "venv",
        "data",
        ".secrets",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "tests",
        "deploy",
    }
    with tarfile.open(archive, "w:gz") as tar:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if any(part in exclude for part in rel.parts):
                continue
            if path.name.startswith(".env") and path.name != ".env.example":
                continue
            tar.add(path, arcname=str(rel))
    return archive


def main() -> int:
    if not KEY_PATH.exists():
        print("SSH key not found", file=sys.stderr)
        return 1
    archive = tarball()
    requirements = ROOT / "requirements.txt"
    req_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
    print("release:", STAMP, "archive:", archive.stat().st_size, "bytes")

    client = connect()
    try:
        code, previous, err = run(
            client, "readlink -f /opt/hackathon-bot/current || true", timeout=30
        )
        previous = previous.strip()
        if previous and not previous.startswith("/opt/hackathon-bot/releases/"):
            previous = ""

        sftp = client.open_sftp()
        try:
            sftp.put(str(archive), REMOTE_TAR)
        finally:
            sftp.close()

        code, out, err = run(client, f"mkdir -p {RELEASE} && tar -xzf {REMOTE_TAR} -C {RELEASE}")
        if code != 0:
            print("extract failed:", err or out, file=sys.stderr)
            return 1
        run(
            client,
            f"chown -R root:root {RELEASE} && "
            f"find {RELEASE} -type d -exec chmod 755 {{}} + && "
            f"find {RELEASE} -type f -exec chmod 644 {{}} +",
            timeout=60,
        )
        sftp = client.open_sftp()
        try:
            for unit in (
                "hackathon-bot.service",
                "hackathon-bot-backup.service",
                "hackathon-bot-backup.timer",
                "hackathon-bot-health.service",
                "hackathon-bot-health.timer",
            ):
                sftp.put(str(ROOT / "deploy" / unit), f"/etc/systemd/system/{unit}")
            sftp.chmod("/etc/systemd/system/hackathon-bot.service", 0o644)
        finally:
            sftp.close()
        run(client, "systemctl daemon-reload", timeout=60)

        code, out, err = run(
            client,
            "test -x /opt/hackathon-bot/venv/bin/python && echo yes || echo no",
            timeout=20,
        )
        if out.strip() != "yes":
            run(client, "python3 -m venv /opt/hackathon-bot/venv", timeout=300)
        code, current_hash, err = run(
            client, "cat /opt/hackathon-bot/requirements.sha256 2>/dev/null || true", timeout=20
        )
        if current_hash.strip() != req_hash:
            code, out, err = run(
                client,
                "/opt/hackathon-bot/venv/bin/python -m pip install --disable-pip-version-check "
                f"--no-cache-dir -r {RELEASE}/requirements.txt",
                timeout=1200,
            )
            if code != 0:
                print("pip install failed:", (err or out)[-3000:], file=sys.stderr)
                return 1
            run(
                client,
                f"printf '%s\\n' '{req_hash}' > /opt/hackathon-bot/requirements.sha256",
                timeout=20,
            )
            print("dependencies updated")
        else:
            print("dependencies unchanged")

        rollback = (
            f"ln -sfn '{previous}' /opt/hackathon-bot/current && "
            "systemctl restart hackathon-bot.service"
            if previous
            else ""
        )
        code, out, err = run(
            client,
            f"ln -sfn {RELEASE} /opt/hackathon-bot/current && systemctl restart hackathon-bot.service",
            timeout=120,
        )
        if code != 0:
            print("restart failed:", err or out, file=sys.stderr)
            if rollback:
                run(client, rollback, timeout=120)
                print("rolled back to", previous)
            return 1

        run(client, "sleep 8", timeout=20)
        code, active, err = run(
            client, "systemctl is-active hackathon-bot.service", timeout=20
        )
        if active.strip() != "active":
            print("service is not active after deploy", file=sys.stderr)
            code, logs, err = run(
                client, "journalctl -u hackathon-bot.service -n 80 --no-pager", timeout=30
            )
            print(logs[-6000:])
            if rollback:
                run(client, rollback, timeout=120)
                print("rolled back to", previous)
            return 1

        code, logs, err = run(
            client, "journalctl -u hackathon-bot.service -n 30 --no-pager", timeout=30
        )
        print("service active; recent logs:")
        print(logs[-5000:])
        run(
            client,
            "ls -1dt /opt/hackathon-bot/releases/* | tail -n +4 | xargs -r rm -rf",
            timeout=60,
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
