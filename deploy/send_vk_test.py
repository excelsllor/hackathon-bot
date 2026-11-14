#!/usr/bin/env python3
"""Send one correctly encoded VK message with a keyboard (diagnostics)."""
from __future__ import annotations

import json
import os
import random
import sys

import httpx


def main() -> int:
    token = os.getenv("VK_TOKEN", "").strip()
    peer_id = os.getenv("VK_PEER_ID", "275613114").strip()
    if not token:
        print("VK_TOKEN is not set", file=sys.stderr)
        return 1

    keyboard = {
        "one_time": False,
        "inline": False,
        "buttons": [
            [
                {"action": {"type": "text", "label": "Начать"}, "color": "primary"},
                {"action": {"type": "text", "label": "Найти"}},
            ],
            [
                {"action": {"type": "text", "label": "Подписки"}},
                {"action": {"type": "text", "label": "Настройки"}},
            ],
            [
                {"action": {"type": "text", "label": "Помощь"}},
            ],
        ],
    }
    message = "Бот на связи. Нажми «Найти» или напиши «Начать»."
    response = httpx.post(
        "https://api.vk.com/method/messages.send",
        data={
            "access_token": token,
            "v": "5.199",
            "peer_id": peer_id,
            "random_id": str(random.randint(1, 2_000_000_000)),
            "message": message,
            "keyboard": json.dumps(keyboard, ensure_ascii=False),
            "disable_mentions": "1",
        },
        timeout=20,
    )
    print("send:", response.status_code, response.text[:300])
    if response.status_code != 200 or "error" in response.text:
        return 1

    check = httpx.get(
        "https://api.vk.com/method/messages.getConversations",
        params={"access_token": token, "v": "5.199", "count": 5},
        timeout=20,
    )
    data = check.json()
    items = data.get("response", {}).get("items", [])
    if items:
        last = items[0].get("last_message", {})
        print("last_text_ascii:", ascii(last.get("text", ""))[:500])
        print("last_out:", last.get("out"), "peer:", last.get("peer_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
