"""Reads your Telegram button taps and replies, and marks cars as dismissed ("Not interested").

Car Watch isn't an always-on server, so `python -m carwatch telegram-sync` runs every 15 minutes from
the scheduler (and at the start of each daily run). Telegram keeps unread bot updates for 24 hours.

- Tap "👎 #N" under a report: the car is never reported again (no new-listing or price-drop alerts).
  The button turns into "↩️ #N"; tap it to undo.
- Or type: "remove 3", "remove 3 7", "undo 3". Numbers refer to the latest report.
Only messages from your own TELEGRAM_CHAT_ID are acted on.
"""
import json
import logging
import re
from html import escape

import requests

from .db import DB
from .notify import API_BASE, send_telegram

log = logging.getLogger(__name__)

COMMAND_RE = re.compile(r"^/?(remove|dismiss|undo|restore)\s+((?:#?\d+[\s,]*)+)$", re.I)
HELP = ("Tap 👎 #N under a listing to remove it from consideration (tap ↩️ to undo).\n"
        "Or type: remove 3  ·  remove 3 7  ·  undo 3  (numbers from the latest report)")


def _api(token, method, **params):
    r = requests.post(f"{API_BASE}/bot{token}/{method}", json=params, timeout=30)
    if not r.ok:
        log.warning("Telegram %s failed: %s %s", method, r.status_code, r.text[:200])
    return r


def _toggle_button(markup: dict, key: str, dismissed: bool) -> dict:
    for row in markup.get("inline_keyboard", []):
        for btn in row:
            if btn.get("callback_data", "").split("|", 1)[-1] == key:
                n = re.search(r"#\d+", btn["text"]).group(0)
                btn["text"] = f"↩️ {n}" if dismissed else f"👎 {n}"
                btn["callback_data"] = f"{'u' if dismissed else 'x'}|{key}"
    return markup


def sync(db: DB, secrets: dict) -> int:
    """Process pending updates. Returns how many listings changed state."""
    token, chat_id = secrets.get("TELEGRAM_BOT_TOKEN"), str(secrets.get("TELEGRAM_CHAT_ID") or "")
    if not token or not chat_id:
        return 0
    offset = int(db.get_kv("tg_offset", "0"))
    r = requests.post(f"{API_BASE}/bot{token}/getUpdates",
                      json={"offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]},
                      timeout=30)
    r.raise_for_status()
    changed = 0
    for upd in r.json().get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        cq = upd.get("callback_query")
        msg = upd.get("message")
        if cq:
            message = cq.get("message") or {}
            if str(message.get("chat", {}).get("id")) != chat_id:
                continue
            action, _, key = (cq.get("data") or "").partition("|")
            dismiss = action == "x"
            row = db.get(key)
            if row is None:
                _api(token, "answerCallbackQuery", callback_query_id=cq["id"], text="Listing not found")
                continue
            db.set_dismissed(key, dismiss)
            changed += 1
            log.info("Telegram: %s %s (%s)", "dismissed" if dismiss else "restored", key, row["title"])
            # Old button taps can't be answered after a while; that's harmless.
            _api(token, "answerCallbackQuery", callback_query_id=cq["id"],
                 text=f"{'Removed' if dismiss else 'Restored'}: {row['title']}"[:190])
            if message.get("reply_markup"):
                _api(token, "editMessageReplyMarkup", chat_id=chat_id, message_id=message["message_id"],
                     reply_markup=_toggle_button(message["reply_markup"], key, dismiss))
        elif msg and str(msg.get("chat", {}).get("id")) == chat_id:
            text = (msg.get("text") or "").strip()
            m = COMMAND_RE.match(text)
            if m:
                changed += handle_command(db, secrets, m.group(1).lower(), m.group(2))
            elif text.lower() in ("/start", "/help", "help"):
                send_telegram(HELP, secrets)
    db.set_kv("tg_offset", str(offset))
    return changed


def handle_command(db: DB, secrets: dict, verb: str, numbers: str) -> int:
    numbering = json.loads(db.get_kv("last_numbering", "{}"))
    dismiss = verb in ("remove", "dismiss")
    done, unknown = [], []
    for n in re.findall(r"\d+", numbers):
        key = numbering.get(n)
        row = db.get(key) if key else None
        if row is None:
            unknown.append(f"#{n}")
            continue
        db.set_dismissed(key, dismiss)
        done.append(f"#{n} {escape(row['title'])}")
    reply = f"{'Removed' if dismiss else 'Restored'}:\n" + "\n".join(done) if done else ""
    if unknown:
        reply += f"\nNot in the latest report: {', '.join(unknown)}"
    send_telegram(reply.strip(), secrets)
    log.info("Telegram command %s %s -> %s", verb, numbers, done or unknown)
    return len(done)
