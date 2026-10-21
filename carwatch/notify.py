import logging
import os
import re

import requests

log = logging.getLogger(__name__)

# Overridable only for local testing against a mock server.
API_BASE = os.environ.get("CARWATCH_TELEGRAM_API", "https://api.telegram.org")

TELEGRAM_LIMIT = 4000  # API limit is 4096 chars per message


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list:
    """Split on blank lines so no listing block is cut in half."""
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def dismiss_keyboard(part: str, numbering: dict):
    """One '👎 #N' button per numbered listing shown in this message part, 4 per row."""
    buttons = [{"text": f"👎 #{n}", "callback_data": f"x|{numbering[int(n)]}"}
               for n in re.findall(r"^<b>#(\d+)</b>", part, re.M) if int(n) in numbering]
    if not buttons:
        return None
    return {"inline_keyboard": [buttons[i:i + 4] for i in range(0, len(buttons), 4)]}


def send_telegram(text: str, secrets: dict, numbering: dict = None):
    token, chat_id = secrets.get("TELEGRAM_BOT_TOKEN"), secrets.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in secrets.env")
    for part in split_message(text):
        body = {"chat_id": chat_id, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}
        keyboard = dismiss_keyboard(part, numbering or {})
        if keyboard:
            body["reply_markup"] = keyboard
        r = requests.post(f"{API_BASE}/bot{token}/sendMessage", json=body, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300]}")


def telegram_chat_ids(token: str) -> list:
    """Chats that have messaged the bot recently (send it any message first)."""
    r = requests.get(f"{API_BASE}/bot{token}/getUpdates", timeout=30)
    r.raise_for_status()
    chats = {}
    for upd in r.json().get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name", "")
    return list(chats.items())


def notify(text: str, cfg: dict, secrets: dict, numbering: dict = None) -> str:
    method = cfg.get("notify", {}).get("method", "telegram")
    if method == "none":
        return "not sent (notify.method is none)"
    if method == "telegram":
        if not secrets.get("TELEGRAM_BOT_TOKEN"):
            log.warning("Telegram not configured; report saved to reports/ only")
            return "not sent (Telegram not configured)"
        send_telegram(text, secrets, numbering)
        return "sent via Telegram"
    raise ValueError(f"Unknown notify.method: {method}")
