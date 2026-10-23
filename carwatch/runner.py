import json
import logging
import os
import socket
import sys
import time
import traceback
from datetime import timedelta
from html import escape
from logging.handlers import RotatingFileHandler

from . import report, telegram_sync
from .config import (LOG_DIR, REPORT_DIR, daily_time, ensure_dirs, load_config, load_secrets,
                     now_dubai)
from .db import DB, norm, same_car
from .filters import annotate, check_criteria
from .models import SourceBlocked
from .notify import notify, send_telegram
from .scoring import score, sort_key
from .sources import SOURCES
from .summarize import add_verdicts

log = logging.getLogger("carwatch")


def setup_logging(verbose=False):
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOG_DIR / "run.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stdout is not None:  # None under pythonw (Task Scheduler)
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def current_window_start(cfg):
    """Start of the current daily window = the most recent daily_time (Dubai), with 10 min slack for early
    triggers. A scheduled daily run skips if one already succeeded in this window, so a late backup
    trigger can't send a second daily report."""
    hour, minute = daily_time(cfg)
    now = now_dubai() + timedelta(minutes=10)
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


def wait_for_network(max_wait=300):
    """After waking from sleep Wi-Fi can take a moment; wait up to 5 minutes."""
    deadline = time.monotonic() + max_wait
    while True:
        try:
            socket.create_connection(("api.telegram.org", 443), timeout=10).close()
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(15)


def process_source(src, db, cfg, now_iso, max_details):
    """Scrape one source. Returns (new_listings, price_drops, scanned_count)."""
    cards = src.search()
    new, drops, details_used = [], [], 0
    for l in cards:
        prev = db.get(l.key)
        if prev is not None and prev["dismissed"]:
            db.upsert(l, False, now_iso)  # you said "Not interested": track it, never report it
            continue
        if prev is not None and l.description is None:
            l.description = prev["description"]
        annotate(l, cfg)
        reasons = check_criteria(l, cfg)
        matches = not reasons
        # A card that only fails "not a hybrid" may still say hybrid/HEV in its description.
        maybe = l.description is None and all(r.startswith("not a hybrid") for r in reasons)
        if l.reserved and prev is not None and prev["notified"]:
            db.reset_notified(l.key)  # reported before, now reserved: alert again if it comes back
        elif (matches or maybe) and (prev is None or not prev["notified"]):
            if l.description is None and details_used < max_details:
                try:
                    src.fetch_details(l)
                    details_used += 1
                except SourceBlocked:
                    raise
                except Exception as e:  # one bad detail page shouldn't kill the run
                    log.warning("detail page failed for %s: %s", l.url, e)
            annotate(l, cfg)  # re-check with the description
            # body unknown = detail page not fetched (cap reached); try again next run
            matches = not check_criteria(l, cfg) and (bool(l.body) or l.priority_source)
            if matches:
                new.append(l)
        elif matches and prev["notified"] and l.price and l.price < (prev["notified_price"] or prev["price"] or 0):
            l.previous_price = prev["notified_price"] or prev["price"]
            drops.append(l)
        db.upsert(l, matches, now_iso)
    db.commit()
    log.info("%s: %d scanned, %d new matches, %d price drops, %d detail pages, %d requests",
             src.name, len(cards), len(new), len(drops), details_used, src.http.request_count)
    return new, drops, len(cards)


def dedupe(new, db):
    """The same car is often on several sites. Drop ones already reported from another site, and merge
    same-run duplicates into the cheapest listing. Returns (kept, dropped)."""
    kept, dropped = [], []
    for l in sorted(new, key=lambda x: (not x.priority_source, x.price or 0)):  # keep the official listing
        earlier = db.find_duplicate(l)
        twin = next((k for k in kept if k.source != l.source and same_car(k, l)), None)
        if earlier:
            log.info("%s is the same car as already-reported %s; skipping", l.url, earlier)
            dropped.append(l)
        elif twin:
            twin.also_on.append(l.url)
            dropped.append(l)
        else:
            kept.append(l)
    return kept, dropped


def watch_match(l, cfg) -> bool:
    """Hourly watch rules, e.g. RAV4 2026+, any Camry, any Accord (hybrid/available is checked earlier)."""
    for rule in cfg["watch"]["rules"]:
        make, _, model = rule["model"].partition(" ")
        if (norm(l.make) == norm(make) and norm(l.model).startswith(norm(model))
                and (l.year or 0) >= rule.get("min_year", 0)):
            return True
    return False


def gh_annotation(level: str, title: str, message: str):
    """A GitHub Actions annotation: shown on the public run page, readable without signing in."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.stdout is None:
        return
    esc = lambda s: s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")  # noqa: E731
    print(f"::{level} title={esc(title).replace(',', '%2C').replace(':', '%3A')}::{esc(message)}", flush=True)


def write_github_summary(summary: dict, report_text: str):
    """On GitHub Actions: per-site results as annotations (public) and a fuller job summary (signed-in)."""
    sites = "\n".join(f"{site}: {st}" for site, st in summary["sources"].items())
    gh_annotation("notice", f"Car Watch {summary['mode']} run",
                  f"{summary['new']} new, {summary['price_drops']} price drops. Telegram: {summary['notification']}. "
                  f"AI verdicts: {summary['ai']}\n{sites}")
    failed = {site: st for site, st in summary["sources"].items() if not st.startswith("✅")}
    if failed:
        gh_annotation("warning", f"Car Watch: {len(failed)} site(s) not checked",
                      "\n".join(f"{site}: {st}" for site, st in failed.items()))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Car Watch: {summary['mode']} run",
             f"**{summary['new']} new, {summary['price_drops']} price drops** · Telegram: {summary['notification']}"
             f" · AI verdicts: {summary['ai']}", "", "| Site | Result |", "| --- | --- |"]
    lines += [f"| {site} | {st} |" for site, st in summary["sources"].items()]
    if report_text:
        lines += ["", "<details><summary>Message sent</summary>", "", "```", report_text, "```", "</details>"]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def watch_window(cfg):
    """(start, end) of the watch day in minutes after midnight, Dubai time."""
    w = cfg["watch"]
    to_min = lambda hhmm: int(hhmm.split(":")[0]) * 60 + int(hhmm.split(":")[1])  # noqa: E731
    return to_min(w.get("start", "06:00")), to_min(w.get("end", "23:30"))


def in_watch_hours(cfg) -> bool:
    """Between start and end (Dubai), with slack for scheduler start-up delays (GitHub often starts
    5-20 minutes late), but never past midnight."""
    start, end = watch_window(cfg)
    now = now_dubai()
    minutes = now.hour * 60 + now.minute
    return start - 5 <= minutes <= min(end + 25, 24 * 60 - 1)


def run(scheduled=False, dry_run=False, no_ai=False, only_sources=None, force=False, mode="daily"):
    """mode="daily": every source, full report. mode="watch": only the official Toyota/Honda pages,
    an instant alert for the watch-list models (RAV4 2026+, Camry, Accord hybrids)."""
    cfg = load_config()
    secrets = load_secrets()
    db = DB()
    watch = mode == "watch"

    if watch:
        if not cfg.get("watch", {}).get("enabled", True):
            return 0
        if scheduled and not force and not in_watch_hours(cfg):
            log.info("Outside watch hours; skipping.")
            return 0
        only_sources = set(cfg["watch"]["sources"])
        no_ai = no_ai or not cfg["watch"].get("ai_verdicts", False)
    elif scheduled and not force:
        last = db.last_successful_run()
        if last and last >= current_window_start(cfg):
            log.info("Already ran successfully at %s for this window; skipping.", last)
            return 0

    run_id = db.start_run(scheduled, mode)
    log.info("=== Run %d started (mode=%s, scheduled=%s, dry_run=%s) ===", run_id, mode, scheduled, dry_run)
    try:
        if not wait_for_network():
            raise RuntimeError("No network connection after 5 minutes")
        if not dry_run:
            try:  # apply any "Not interested" taps before deciding what's new
                telegram_sync.sync(db, secrets)
            except Exception as e:
                log.warning("Telegram sync failed (continuing): %s", e)
        now_iso = now_dubai().isoformat(timespec="seconds")
        max_details = cfg["http"].get("max_detail_pages_per_run", 40)
        all_new, all_drops, status = [], [], {}

        for name, src_cfg in cfg["sources"].items():
            if only_sources and name not in only_sources:
                continue
            if not src_cfg.get("enabled", True):
                continue
            if name not in SOURCES:
                status[name] = "not implemented yet"
                continue
            src = SOURCES[name](cfg)
            if watch and src_cfg.get("watch_pages"):
                src.src_cfg = {**src_cfg, "pages": src_cfg["watch_pages"]}  # just the watch-list model pages
            try:
                new, drops, scanned = process_source(src, db, cfg, now_iso, max_details)
                if watch:  # older RAV4s etc. stay unreported here and go into the daily report instead
                    new = [l for l in new if watch_match(l, cfg)]
                    drops = [l for l in drops if watch_match(l, cfg)]
                all_new += new
                all_drops += drops
                status[name] = f"✅ {scanned} listings scanned, {len(new)} new"
                if src.warnings:
                    status[name] += f" (⚠️ {'; '.join(src.warnings)})"
            except SourceBlocked as e:
                log.warning("%s blocked us: %s — skipping (not bypassing)", name, e)
                status[name] = "⛔ blocked by the site (bot protection) — skipped"
            except Exception as e:
                log.error("%s failed: %s\n%s", name, e, traceback.format_exc())
                status[name] = f"❌ error: {type(e).__name__}: {str(e)[:120]}"

        all_new, dupes = dedupe(all_new, db)
        for l in all_new + all_drops:
            score(l, db, cfg)

        ai_status = "disabled"
        if not no_ai:
            ranked = sorted(all_new, key=lambda l: sort_key(l, cfg))
            top_n = cfg.get("ai_summary", {}).get("max_listings", 12)
            chosen = ranked[:top_n] + [l for l in ranked[top_n:] if l.is_priority]
            ai_status = add_verdicts(chosen, cfg)
            log.info("AI verdicts: %s", ai_status)

        date_str = now_dubai().strftime("%a %d %b %Y" + (", %H:%M" if watch else ""))
        headline = "⚡ Toyota / Honda Pre-Owned watch" if watch else None
        numbering = {}
        text = report.build(all_new, all_drops, status, ai_status, cfg, date_str, numbering=numbering,
                            headline=headline)
        report_path = None
        if not watch or all_new or all_drops:  # don't litter reports/ with 18 empty watch reports a day
            report_path = REPORT_DIR / f"{now_dubai():%Y-%m-%d_%H%M}{'_watch' if watch else ''}" \
                                       f"{'_dry' if dry_run else ''}.txt"
            full_text = report.build(all_new, all_drops, status, ai_status, cfg, date_str, full=True,
                                     headline=headline)
            report_path.write_text(report.to_plain(full_text), encoding="utf-8")
            log.info("Report saved to %s", report_path)

        everything_failed = status and not any(s.startswith("✅") for s in status.values())
        if watch and everything_failed:
            # Hourly: alert about a broken watch at most once a day, not 18 times.
            today = now_dubai().date().isoformat()
            everything_failed = db.get_kv("watch_failure_alert") != today
            if everything_failed and not dry_run:
                db.set_kv("watch_failure_alert", today)
        if dry_run:
            sent = "dry run, not sent"
        elif all_new or all_drops or everything_failed:
            sent = notify(text, cfg, secrets, numbering)
            if sent.startswith("sent"):
                for l in all_new + dupes + all_drops:
                    db.mark_notified(l)
                db.commit()
                if numbering:
                    db.set_kv("last_numbering", json.dumps(numbering))  # for typed "remove 3" replies
        else:
            sent = "nothing new, no message sent"
        log.info("Notification: %s", sent)

        summary = {"mode": mode, "new": len(all_new), "price_drops": len(all_drops), "sources": status,
                   "ai": ai_status, "notification": sent, "report": str(report_path) if report_path else None}
        write_github_summary(summary, report.to_plain(text) if (all_new or all_drops) else "")
        db.finish_run(run_id, "dry" if dry_run else "ok", summary)  # dry runs don't count for --scheduled
        log.info("=== Run %d finished OK: %s ===", run_id, summary)
        return 0
    except Exception as e:
        log.error("Run %d failed: %s\n%s", run_id, e, traceback.format_exc())
        db.finish_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
        gh_annotation("error", f"Car Watch {mode} run failed", f"{type(e).__name__}: {e}")
        today = now_dubai().date().isoformat()
        if watch and db.get_kv("watch_failure_alert") == today:
            return 1  # already alerted about the watch today
        if watch:
            db.set_kv("watch_failure_alert", today)
        if not dry_run and secrets.get("TELEGRAM_BOT_TOKEN"):
            try:
                send_telegram(f"❌ Car Watch run failed: {escape(type(e).__name__)}: {escape(str(e))}\n"
                              "See logs/run.log", secrets)
            except Exception:
                log.exception("Could not send failure alert")
        return 1
