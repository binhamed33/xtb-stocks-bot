"""Discord webhook embeds. Never raises."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

log = logging.getLogger(__name__)
GREEN, RED, ORANGE, BLUE, GREY, PURPLE = 0x2ECC71, 0xE74C3C, 0xF39C12, 0x3498DB, 0x95A5A6, 0x9B59B6


class Discord:
    def __init__(self, webhook: str | None, username: str = "XTB Stocks Bot") -> None:
        self.webhook = webhook
        self.username = username

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def _post(self, body: dict[str, Any]) -> bool:
        if not self.webhook:
            log.info("Discord (not configured): %s", json.dumps(body, ensure_ascii=False)[:300])
            return False
        body["username"] = self.username
        req = urllib.request.Request(
            self.webhook, data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "xtb-stocks-bot/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Discord post failed: %s", exc)
            return False

    def text(self, content: str) -> bool:
        return self._post({"content": content[:1900]})

    def embed(self, title: str, description: str = "", fields: list[tuple[str, str, bool]] | None = None,
              color: int = BLUE, footer: str = "") -> bool:
        e: dict[str, Any] = {"title": title[:256], "color": color}
        if description:
            e["description"] = description[:4000]
        if fields:
            e["fields"] = [{"name": n[:256], "value": (v or "-")[:1024], "inline": i} for n, v, i in fields][:25]
        if footer:
            e["footer"] = {"text": footer[:2048]}
        return self._post({"embeds": [e]})

    def embeds(self, embeds: list[dict[str, Any]]) -> bool:
        # Discord allows up to 10 embeds per message
        ok = True
        for i in range(0, len(embeds), 10):
            ok &= self._post({"embeds": embeds[i:i + 10]})
        return ok
