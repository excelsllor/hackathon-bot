#!/usr/bin/env python3
"""One-time server preparation and first deploy (idempotent enough for reruns)."""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import paramiko

HOST = os.getenv("VPS_HOST", "92.242.164.222")
USER = os.getenv("VPS_USER", "root")
KEY_PATH = Path(
    os.getenv("VPS_KEY", str(Path.home() / ".ssh" / "hackathon_bot_ed25519"))
)
ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
RELEASE_DIR = f"/opt/hackathon-bot/releases/{STAMP}"
REMOTE_TAR = f"/tmp/hackathon-bot-{STAMP}.tar.gz"


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        port=22,
        username=USER,
        key_filename=str(KEY_PATH),
        timeout=20,
        banner_timeout=25,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def run(
    client: paramiko.SSHClient, command: str, timeout: int = 120, check: bool = True
) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    if check and code != 0:
        raise RuntimeError(f"command failed ({code}): {command[:200]}\n{err or out}")
    return code, out, err


def make_tarball() -> Path:
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


def upload(client: paramiko.SSHClient, local: Path, remote: str) -> None:
    sftp = client.open_sftp()
    try:
        sftp.put(str(local), remote)
    finally:
        sftp.close()


def write_remote(client: paramiko.SSHClient, path: str, content: str, mode: int) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "w") as fh:
            fh.write(content)
        sftp.chmod(path, mode)
    finally:
        sftp.close()


def main() -> int:
    if not KEY_PATH.exists():
        print("SSH key not found", file=sys.stderr)
        return 1
    archive = make_tarball()
    print("archive:", archive, archive.stat().st_size, "bytes")

    client = connect()
    try:
        code, out, err = run(client, "systemctl is-active hysteria-server.service", check=False)
        print("hysteria before:", out or err)

        code, out, err = run(
            client,
            "export DEBIAN_FRONTEND=noninteractive; "
            "dpkg -s python3-venv >/dev/null 2>&1 || "
            "(apt-get update -qq && apt-get install -y --no-install-recommends python3-venv)",
            timeout=600,
        )
        print("python3-venv ready")

        setup = r"""
set -e
id -u hackbot >/dev/null 2>&1 || useradd --system --home-dir /var/lib/hackathon-bot --shell /usr/sbin/nologin hackbot
install -d -m 700 -o hackbot -g hackbot /var/lib/hackathon-bot
install -d -m 755 -o root -g root /opt/hackathon-bot /opt/hackathon-bot/releases
install -d -m 700 -o root -g root /etc/hackathon-bot
install -d -m 700 -o root -g root /var/backups/hackathon-bot
"""
        run(client, setup, timeout=60)
        print("user and directories ready")

        upload(client, archive, REMOTE_TAR)
        run(client, f"mkdir -p {RELEASE_DIR} && tar -xzf {REMOTE_TAR} -C {RELEASE_DIR}")
        run(client, f"ln -sfn {RELEASE_DIR} /opt/hackathon-bot/current")
        run(
            client,
            f"chown -R root:root {RELEASE_DIR} && "
            f"find {RELEASE_DIR} -type d -exec chmod 755 {{}} + && "
            f"find {RELEASE_DIR} -type f -exec chmod 644 {{}} + && "
            f"chmod 755 /opt/hackathon-bot /opt/hackathon-bot/releases",
        )
        print("release uploaded:", RELEASE_DIR)

        code, out, err = run(
            client, "test -x /opt/hackathon-bot/venv/bin/python && echo yes || echo no", check=False
        )
        if out.strip() != "yes":
            run(client, "python3 -m venv /opt/hackathon-bot/venv", timeout=300)
            print("venv created")
        run(
            client,
            "/opt/hackathon-bot/venv/bin/python -m pip install --disable-pip-version-check "
            f"--no-cache-dir -r {RELEASE_DIR}/requirements.txt",
            timeout=1200,
        )
        print("dependencies installed")

        env_content = """TG_TOKEN=
TG_ADMIN_IDS=
VK_TOKEN=
VK_GROUP_ID=0
VK_ADMIN_IDS=
DATA_DIR=/var/lib/hackathon-bot
DB_PATH=/var/lib/hackathon-bot/bot.db
HEARTBEAT_PATH=/var/lib/hackathon-bot/heartbeat
LOG_LEVEL=INFO
TZ=Europe/Moscow
PARSER_ONLY=0
DIGEST_DEFAULT_TIME=10:00
USER_AGENT=HackathonBot/1.0 (+contact: set-in-/etc/hackathon-bot/env)
ON_DEMAND_TTL_MINUTES=45
WAIT_FOR_REFRESH_SECONDS=15
"""
        write_remote(client, "/etc/hackathon-bot/env", env_content, 0o600)

        for unit in (
            "hackathon-bot.service",
            "hackathon-bot-backup.service",
            "hackathon-bot-backup.timer",
            "hackathon-bot-health.service",
            "hackathon-bot-health.timer",
        ):
            local = ROOT / "deploy" / unit
            upload(client, local, f"/etc/systemd/system/{unit}")
            run(client, f"chmod 644 /etc/systemd/system/{unit}")
        run(client, "systemctl daemon-reload")
        run(client, "systemctl enable --now hackathon-bot.service")
        run(
            client,
            "systemctl enable --now hackathon-bot-backup.timer hackathon-bot-health.timer",
        )
        print("systemd units enabled")

        run(client, "sleep 8", timeout=20)
        code, out, err = run(client, "systemctl is-active hackathon-bot.service", check=False)
        print("service after start:", out or err)

        run(client, "sleep 20", timeout=30)
        code, logs, err = run(
            client, "journalctl -u hackathon-bot.service -n 50 --no-pager", check=False
        )
        print("--- journal ---")
        print(logs[-6000:])

        code, out, err = run(
            client,
            "stat -c '%a %U:%G %n' /etc/hackathon-bot/env /var/lib/hackathon-bot "
            "/var/backups/hackathon-bot /opt/hackathon-bot/current",
            check=False,
        )
        print("--- perms ---")
        print(out)
        code, out, err = run(
            client,
            "runuser -u hackbot -- test -w /opt/hackathon-bot && echo WRITABLE || echo READONLY",
            check=False,
        )
        print("app write access to /opt:", out)
        code, out, err = run(
            client,
            "ss -tulpn 2>/dev/null | grep -E ':(22|443) ' || true",
            check=False,
        )
        print("--- listeners ---")
        print(out)
        code, out, err = run(
            client, "systemctl is-active hysteria-server.service", check=False
        )
        print("hysteria after:", out or err)

        code, out, err = run(client, "systemctl start hackathon-bot-backup.service", check=False)
        print("backup service:", code, out or err)
        code, out, err = run(
            client, "ls -l /var/backups/hackathon-bot", check=False
        )
        print("--- backups ---")
        print(out)

        code, out, err = run(
            client,
            "/opt/hackathon-bot/venv/bin/python - <<'PY'\n"
            "import sqlite3\n"
            "con=sqlite3.connect('/var/lib/hackathon-bot/bot.db')\n"
            "print('items', con.execute('select count(*) from items').fetchone()[0])\n"
            "print('active', con.execute('select count(*) from items where is_active=1').fetchone()[0])\n"
            "print('states', con.execute('select count(*) from source_state').fetchone()[0])\n"
            "PY",
            check=False,
        )
        print("--- db ---")
        print(out or err)

        code, out, err = run(
            client,
            "systemd-analyze security --no-pager hackathon-bot.service 2>/dev/null | tail -3",
            check=False,
        )
        print("--- systemd exposure ---")
        print(out or err)

        if code == 0:
            return 0
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
