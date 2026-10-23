import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "carwatch.db"

# Dubai has no DST, so a fixed offset is exact (and avoids needing tzdata on Windows).
DUBAI_TZ = timezone(timedelta(hours=4), "Asia/Dubai")
RUN_HOUR_DUBAI = 13  # default daily-report time if config.yaml has no daily_time


def daily_time(cfg: dict) -> tuple:
    """(hour, minute) of the daily report, Dubai time, from config.yaml's daily_time ("12:00")."""
    hh, _, mm = str(cfg.get("daily_time", f"{RUN_HOUR_DUBAI}:00")).partition(":")
    return int(hh), int(mm or 0)


def now_dubai() -> datetime:
    return datetime.now(DUBAI_TZ)


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_secrets(path: Path = ROOT / "secrets.env") -> dict:
    """KEY=VALUE lines from secrets.env, overridden by real environment variables."""
    secrets = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                secrets[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets


def ensure_dirs():
    for d in (DATA_DIR, LOG_DIR, REPORT_DIR):
        d.mkdir(exist_ok=True)
