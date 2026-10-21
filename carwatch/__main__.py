import argparse
import json
import sys

from .config import ROOT, load_secrets


def main():
    p = argparse.ArgumentParser(prog="python -m carwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="check all sources now")
    r.add_argument("--scheduled", action="store_true", help="skip if already ran in today's 1pm window")
    r.add_argument("--force", action="store_true", help="with --scheduled: run anyway")
    r.add_argument("--dry-run", action="store_true", help="save the report but don't send or mark as notified")
    r.add_argument("--no-ai", action="store_true", help="skip claude -p verdicts")
    r.add_argument("--sources", help="comma-separated subset, e.g. dubicars")
    r.add_argument("-v", "--verbose", action="store_true")
    w = sub.add_parser("watch", help="hourly: official Toyota/Honda pages, instant alert for watch-list models")
    w.add_argument("--scheduled", action="store_true", help="skip outside watch hours (6am-11pm Dubai)")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("telegram-setup", help="find your chat id (message your bot first)")
    sub.add_parser("telegram-test", help="send a test message")
    sub.add_parser("telegram-sync", help="apply 'Not interested' taps and replies (runs every 15 min)")
    sub.add_parser("install-schedule", help="daily 1pm Dubai run via Task Scheduler / launchd / cron")
    sub.add_parser("uninstall-schedule")
    sub.add_parser("status", help="show recent runs")
    a = p.parse_args()

    if a.cmd == "run":
        from .runner import run, setup_logging
        setup_logging(a.verbose)
        sources = set(a.sources.split(",")) if a.sources else None
        return run(a.scheduled, a.dry_run, a.no_ai, sources, a.force)

    if a.cmd == "watch":
        from .runner import run, setup_logging
        setup_logging(a.verbose)
        return run(scheduled=a.scheduled, dry_run=a.dry_run, mode="watch")

    if a.cmd == "telegram-setup":
        from .notify import telegram_chat_ids
        secrets = load_secrets()
        token = secrets.get("TELEGRAM_BOT_TOKEN")
        if not token:
            print("Put TELEGRAM_BOT_TOKEN=... in secrets.env first (see README).")
            return 1
        chats = telegram_chat_ids(token)
        if not chats:
            print("No messages found. Open your bot in Telegram, press Start / send 'hi', then rerun this.")
            return 1
        for cid, name in chats:
            print(f"chat id {cid}  ({name})")
        if len(chats) == 1 and not secrets.get("TELEGRAM_CHAT_ID"):
            path = ROOT / "secrets.env"
            text = path.read_text(encoding="utf-8")
            if "TELEGRAM_CHAT_ID=" in text:
                text = text.replace("TELEGRAM_CHAT_ID=", f"TELEGRAM_CHAT_ID={chats[0][0]}", 1)
            else:
                text += f"\nTELEGRAM_CHAT_ID={chats[0][0]}\n"
            path.write_text(text, encoding="utf-8")
            print("Saved TELEGRAM_CHAT_ID to secrets.env")
        return 0

    if a.cmd == "telegram-test":
        from .notify import send_telegram
        send_telegram("✅ Car Watch is connected. Daily report arrives around 1pm Dubai time.", load_secrets())
        print("Sent.")
        return 0

    if a.cmd == "telegram-sync":
        import logging
        from logging.handlers import RotatingFileHandler
        from .config import LOG_DIR, ensure_dirs
        from .db import DB
        from .telegram_sync import sync
        ensure_dirs()  # quiet unless something happens: this runs every 15 minutes
        h = RotatingFileHandler(LOG_DIR / "telegram.log", maxBytes=500_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        try:
            changed = sync(DB(), load_secrets())
        except Exception as e:
            logging.getLogger("carwatch").warning("telegram-sync failed: %s", e)
            return 1
        if sys.stdout is not None:
            print(f"{changed} listing(s) updated")
        return 0

    if a.cmd == "install-schedule":
        from .schedule import install
        install()
        return 0

    if a.cmd == "uninstall-schedule":
        from .schedule import uninstall
        uninstall()
        return 0

    if a.cmd == "status":
        from .db import DB
        db = DB()
        for row in db.conn.execute("SELECT title, url, dismissed_at FROM listings WHERE dismissed=1 "
                                   "ORDER BY dismissed_at DESC"):
            print(f"not interested ({row['dismissed_at']}): {row['title']}  {row['url']}")
        for row in db.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10"):
            summary = json.loads(row["summary"]) if row["summary"] else {}
            print(f"#{row['id']} {row['started_at']} {row['status']:<7} "
                  f"new={summary.get('new', '-')} drops={summary.get('price_drops', '-')} "
                  f"{row['error'] or summary.get('notification', '')}")
            for src, st in summary.get("sources", {}).items():
                print(f"     {src}: {st}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
