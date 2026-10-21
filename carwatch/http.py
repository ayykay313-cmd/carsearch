import logging
import random
import time

import requests

from .models import SourceBlocked

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# Text that shows up on bot-protection / challenge pages instead of real content.
BLOCK_MARKERS = (
    "Pardon Our Interruption",      # Imperva
    "<title>Just a moment...",      # Cloudflare challenge
    "cf-chl-",                      # Cloudflare
    "captcha-delivery.com",         # DataDome
    "Access Denied",                # Akamai
    "px-captcha",                   # PerimeterX
)


class PoliteSession:
    """One requests session per site, with a random delay between requests."""

    def __init__(self, http_cfg: dict):
        self.min_delay = http_cfg.get("min_delay_seconds", 4)
        self.max_delay = http_cfg.get("max_delay_seconds", 8)
        self.timeout = http_cfg.get("timeout_seconds", 30)
        self.request_count = 0
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def get(self, url: str, **kwargs) -> requests.Response:
        wait = self._last + random.uniform(self.min_delay, self.max_delay) - time.monotonic()
        if self._last and wait > 0:
            time.sleep(wait)
        log.debug("GET %s", url)
        try:
            r = self.s.get(url, timeout=self.timeout, **kwargs)
        finally:
            self._last = time.monotonic()
            self.request_count += 1
        if r.status_code in (401, 403, 429) or (r.status_code == 503 and "cf-" in r.text):
            raise SourceBlocked(f"HTTP {r.status_code} from {url}")
        head = r.text[:5000]
        for marker in BLOCK_MARKERS:
            if marker in head:
                raise SourceBlocked(f"bot-protection page ({marker!r}) at {url}")
        r.raise_for_status()
        return r
