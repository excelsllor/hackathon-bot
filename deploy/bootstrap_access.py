#!/usr/bin/env python3
"""One-time read/write access bootstrap: SSH key, root key-only, password rotation."""
from __future__ import annotations

import os
import secrets
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

import paramiko

HOST = os.getenv("VPS_HOST", "").strip()
USER = os.getenv("VPS_USER", "root")
PASSWORD = os.getenv("VPS_PASSWORD", "")
KEY_PATH = Path(
    os.getenv("VPS_KEY", str(Path.home() / ".ssh" / "id_ed25519"))
)
PUB_PATH = Path(str(KEY_PATH) + ".pub")
ROOT = Path(__file__).resolve().parents[1]


def run(client: paramiko.SSHClient, command: str, timeout: int = 60) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    return code, out, err


def connect_key() -> paramiko.SSHClient:
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


def connect_password() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        port=22,
        username=USER,
        password=PASSWORD,
        timeout=20,
        banner_timeout=25,
        auth_timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def install_public_key(client: paramiko.SSHClient) -> None:
    pub = PUB_PATH.read_text(encoding="utf-8").strip()
    if not pub.startswith("ssh-"):
        raise RuntimeError("public key file is invalid")
    code, out, err = run(client, "mkdir -p /root/.ssh && chmod 700 /root/.ssh")
    if code != 0:
        raise RuntimeError(f"cannot prepare /root/.ssh: {err or out}")

    sftp = client.open_sftp()
    try:
        try:
            with sftp.open("/root/.ssh/authorized_keys", "r") as fh:
                existing = fh.read().decode("utf-8", "replace")
        except FileNotFoundError:
            existing = ""
        if pub not in existing:
            with sftp.open("/root/.ssh/authorized_keys", "w") as fh:
                fh.write(existing + ("" if existing.endswith("\n") or not existing else "\n") + pub + "\n")
        sftp.chmod("/root/.ssh/authorized_keys", 0o600)
    finally:
        sftp.close()
    print("public key installed")


def harden_ssh(client: paramiko.SSHClient) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    code, out, err = run(
        client,
        "cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.bak-hackathon-"
        + stamp
        + " && ls -l /etc/ssh/sshd_config.bak-hackathon-"
        + stamp,
    )
    if code != 0:
        raise RuntimeError(f"cannot backup sshd_config: {err or out}")
    print("sshd_config backup:", out.splitlines()[-1] if out else "ok")

    # 00- file wins for global PermitRootLogin because Include is parsed first.
    hardening = "/etc/ssh/sshd_config.d/00-hackathon-hardening.conf"
    block = (
        "# hackathon-bot: root key-only\n"
        "PermitRootLogin prohibit-password\n"
    )
    sftp = client.open_sftp()
    try:
        with sftp.open(hardening, "w") as fh:
            fh.write(block)
        sftp.chmod(hardening, 0o600)
    finally:
        sftp.close()

    match_block = (
        "\n# hackathon-bot: disable root password auth\n"
        "Match User root\n"
        "    PasswordAuthentication no\n"
        "    KbdInteractiveAuthentication no\n"
    )
    code, out, err = run(client, "cat /etc/ssh/sshd_config")
    config = out if code == 0 else ""
    if "hackathon-bot: disable root password auth" not in config:
        with client.open_sftp() as sftp, sftp.open("/etc/ssh/sshd_config", "a") as fh:
            fh.write(match_block)

    code, out, err = run(client, "sshd -t")
    if code != 0:
        raise RuntimeError(f"sshd -t failed: {err or out}")
    code, out, err = run(client, "systemctl reload ssh 2>&1 || systemctl reload sshd 2>&1")
    if code != 0:
        raise RuntimeError(f"sshd reload failed: {err or out}")
    print("ssh hardening applied")


def verify_ssh(client: paramiko.SSHClient) -> None:
    # key login
    key_client = connect_key()
    code, out, err = run(key_client, "id -u")
    key_client.close()
    if code != 0 or out.strip() != "0":
        raise RuntimeError("key login verification failed")

    # password login must fail
    try:
        bad = connect_password()
    except paramiko.AuthenticationException:
        print("root password login disabled: ok")
        return
    except Exception as exc:
        raise RuntimeError(f"password login test error: {exc}") from exc
    bad.close()
    raise RuntimeError("root password login still works after hardening")

    # match config sanity
    code, out, err = run(
        client,
        "sshd -T -C user=root,host=localhost,addr=127.0.0.1 2>/dev/null | "
        "grep -Ei '^(permitrootlogin|passwordauthentication)'",
    )
    print("root match config:", out.replace("\n", "; "))


def rotate_root_password(client: paramiko.SSHClient) -> None:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_+="
    new_password = "".join(secrets.choice(alphabet) for _ in range(40))
    stdin, stdout, stderr = client.exec_command("chpasswd", timeout=30)
    stdin.write(f"root:{new_password}\n")
    stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", "replace").strip()
    if code != 0:
        raise RuntimeError(f"chpasswd failed: {err}")
    secrets_dir = ROOT / ".secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    password_file = secrets_dir / "vps_root_password.txt"
    password_file.write_text(
        "VPS root password (rotated). Store in a password manager, then delete this file.\n"
        + new_password
        + "\n",
        encoding="utf-8",
    )
    print("root password rotated; saved to", password_file)


def main() -> int:
    if not HOST:
        print("VPS_HOST is not set", file=sys.stderr)
        return 1
    if not PASSWORD:
        print("VPS_PASSWORD is not set", file=sys.stderr)
        return 1
    if not KEY_PATH.exists() or not PUB_PATH.exists():
        print("SSH key pair not found", file=sys.stderr)
        return 1

    password_client = connect_password()
    try:
        install_public_key(password_client)
        key_client = connect_key()
        code, out, err = run(key_client, "id -u")
        key_client.close()
        if code != 0 or out.strip() != "0":
            raise RuntimeError("key login test failed before hardening")

        try:
            harden_ssh(password_client)
            verify_ssh(password_client)
        except Exception:
            # Keep access: restore the backup if hardening broke something.
            code, out, err = run(
                password_client,
                "ls -1t /etc/ssh/sshd_config.bak-hackathon-* 2>/dev/null | head -1",
            )
            backup = out.strip().splitlines()[0] if out.strip() else ""
            if backup:
                run(
                    password_client,
                    f"cp -a '{backup}' /etc/ssh/sshd_config && rm -f /etc/ssh/sshd_config.d/00-hackathon-hardening.conf && sshd -t && (systemctl reload ssh || systemctl reload sshd) || true",
                )
            raise
        rotate_root_password(password_client)
        print("access bootstrap complete")
        return 0
    finally:
        password_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
