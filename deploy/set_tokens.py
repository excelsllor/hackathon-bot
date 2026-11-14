#!/usr/bin/env python3
"""Update bot tokens/admin ids in /etc/hackathon-bot/env and restart the service.

Usage (values stay in your terminal, not in chat):
  $env:TG_TOKEN='...'; $env:VK_TOKEN='...'; python deploy/set_tokens.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import paramiko

HOST = os.getenv("VPS_HOST", "").strip()
USER = os.getenv("VPS_USER", "root")
KEY_PATH = Path(os.getenv("VPS_KEY", str(Path.home() / ".ssh" / "id_ed25519")))
ENV_PATH = "/etc/hackathon-bot/env"
UPDATABLE = (
    "TG_TOKEN",
    "TG_ADMIN_IDS",
    "VK_TOKEN",
    "VK_GROUP_ID",
    "VK_ADMIN_IDS",
)


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


def run(client: paramiko.SSHClient, command: str, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    return stdout.channel.recv_exit_status(), out, err


def validate_tg(token: str) -> bool:
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=15
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("TG token check failed:", exc, file=sys.stderr)
        return False
    if not data.get("ok"):
        print("TG token check failed:", data.get("description", "unknown error"), file=sys.stderr)
        return False
    print("TG token valid:", data.get("result", {}).get("username", "unknown"))
    return True


def validate_vk(token: str, group_id: str) -> bool:
    if not group_id or group_id == "0":
        print("VK: group id not set, skipping Long Poll check")
        return True
    query = urllib.parse.urlencode(
        {"group_id": group_id, "access_token": token, "v": "5.199"}
    )
    try:
        with urllib.request.urlopen(
            "https://api.vk.com/method/groups.getLongPollServer?" + query, timeout=15
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("VK token check failed:", exc, file=sys.stderr)
        return False
    if "error" in data:
        print("VK token check failed:", data["error"].get("error_msg", "unknown error"), file=sys.stderr)
        return False
    print("VK Long Poll token valid")
    return True


def main() -> int:
    if not HOST:
        print("VPS_HOST is not set", file=sys.stderr)
        return 1
    updates = {key: os.getenv(key, "").strip() for key in UPDATABLE}
    updates = {key: value for key, value in updates.items() if value}
    if not updates:
        print("nothing to update: set TG_TOKEN/VK_TOKEN or admin ids in environment", file=sys.stderr)
        return 1
    if not KEY_PATH.exists():
        print("SSH key not found", file=sys.stderr)
        return 1
    if updates.get("TG_TOKEN") and not validate_tg(updates["TG_TOKEN"]):
        return 1
    if updates.get("VK_TOKEN") and not validate_vk(
        updates["VK_TOKEN"], updates.get("VK_GROUP_ID", os.getenv("VK_GROUP_ID", "0"))
    ):
        return 1

    client = connect()
    try:
        sftp = client.open_sftp()
        try:
            with sftp.open(ENV_PATH, "r") as fh:
                lines = fh.read().decode("utf-8", "replace").splitlines()
        finally:
            sftp.close()

        replaced: set[str] = set()
        output: list[str] = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
            if key in updates:
                output.append(f"{key}={updates[key]}")
                replaced.add(key)
            else:
                output.append(line)
        for key, value in updates.items():
            if key not in replaced:
                output.append(f"{key}={value}")

        sftp = client.open_sftp()
        try:
            with sftp.open(ENV_PATH, "w") as fh:
                fh.write("\n".join(output).rstrip() + "\n")
            sftp.chmod(ENV_PATH, 0o600)
        finally:
            sftp.close()

        code, out, err = run(client, "systemctl restart hackathon-bot.service")
        if code != 0:
            print("restart failed:", err or out, file=sys.stderr)
            return 1
        run(client, "sleep 5", timeout=10)
        code, out, err = run(client, "systemctl is-active hackathon-bot.service", timeout=20)
        print("updated:", ", ".join(sorted(updates)))
        print("service:", out or err)
        code, logs, err = run(
            client, "journalctl -u hackathon-bot.service -n 40 --no-pager", timeout=30
        )
        print("--- recent logs ---")
        print(logs[-5000:])
        return 0 if out.strip() == "active" else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
