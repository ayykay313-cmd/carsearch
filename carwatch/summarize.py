"""Optional one-line verdicts via headless Claude Code (`claude -p`), using the logged-in subscription."""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .models import Listing

log = logging.getLogger(__name__)

PROMPT = """You are helping a buyer in Dubai pick a used hybrid car (GCC specs, under 30,000 km, under AED 140,000).
For each listing below, write ONE short verdict (max 20 words): is it a good buy, and what to check.
Be concrete (price vs. the comparable average when given, km for the year, trim, any red flags).
Reply with ONLY a JSON object mapping each "id" to its verdict string. No markdown, no extra text.

Listings:
{listings}
"""


def find_claude(command: str):
    found = shutil.which(command)
    if found:
        return found
    # Task Scheduler / launchd / cron often run with a minimal PATH.
    for candidate in (Path.home() / ".local/bin/claude.exe", Path.home() / ".local/bin/claude",
                      Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if candidate.exists():
            return str(candidate)
    return None


def add_verdicts(listings: list, cfg: dict) -> str:
    """Fills listing.verdict in place. Returns a status string; never raises."""
    ai = cfg.get("ai_summary", {})
    if not ai.get("enabled") or not listings:
        return "disabled" if not ai.get("enabled") else "nothing to summarise"
    exe = find_claude(ai.get("command", "claude"))
    if not exe:
        log.warning("claude CLI not found; skipping AI verdicts")
        return "skipped (claude CLI not found)"

    payload = [{
        "id": l.key, "car": f"{l.year} {l.make} {l.model} {l.trim}".strip(), "price_aed": l.price,
        "km": l.km, "city": l.city, "seller": l.seller_type, "plug_in": l.is_phev,
        "comparable_avg_aed": l.deal_avg, "pct_below_avg": l.deal_pct, "comparables": l.deal_n,
        "red_flags": l.red_flags, "warnings": l.warnings,
        "description": (l.description or "")[:600],
    } for l in listings]
    prompt = PROMPT.format(listings=json.dumps(payload, ensure_ascii=False, indent=1))

    # Force subscription auth: never let an API key be picked up. Also drop variables left by a parent
    # Claude Code session (when run from inside one), so the CLI uses its own saved login. The one
    # exception is CLAUDE_CODE_OAUTH_TOKEN, the subscription token from `claude setup-token` that
    # GitHub Actions uses (it is not an API key).
    env = {k: v for k, v in os.environ.items()
           if k == "CLAUDE_CODE_OAUTH_TOKEN"
           or not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_AGENT_SDK", "CLAUDE_PID"))}

    cmd = [exe, "-p", "--tools", "", "--no-session-persistence", "--output-format", "text"]
    if ai.get("model"):
        cmd += ["--model", ai["model"]]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=ai.get("timeout_seconds", 180), creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("claude -p failed: %s", e)
        return f"skipped ({type(e).__name__})"
    if r.returncode != 0:
        log.warning("claude -p exit %s: %s", r.returncode, (r.stderr or r.stdout)[:500])
        return f"skipped (claude exit code {r.returncode})"

    m = re.search(r"\{.*\}", r.stdout, re.S)
    try:
        verdicts = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        verdicts = {}
    if not verdicts:
        log.warning("Could not parse claude output: %s", r.stdout[:500])
        return "skipped (unparseable output)"
    for l in listings:
        l.verdict = str(verdicts.get(l.key, "")).strip()
    return f"ok ({sum(1 for l in listings if l.verdict)} verdicts)"
