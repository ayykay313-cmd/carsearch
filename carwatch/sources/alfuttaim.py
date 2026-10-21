"""Official Al-Futtaim pre-owned sites: toyota.ae and honda.ae (top priority).

Their model pages load the cars with JavaScript from a token-protected API, so we render the public
model pages in headless Chromium (Playwright) and read the visible cards, exactly as a visitor sees
them. A card with a "Reserved" badge is skipped. robots.txt disallows */details/* pages, so we never
open them (the link is only included in your message).

These listings skip the price/km/year/location criteria: any available hybrid of the configured models
is reported.
"""
import logging
import random
import re
import time

from ..db import norm
from ..http import BLOCK_MARKERS, USER_AGENT
from ..models import Listing, SourceBlocked
from .base import Source

log = logging.getLogger(__name__)

# Each card = the largest element that contains exactly one details link.
CARDS_JS = """() => {
  const sel = 'a[href*="/pre-owned/details/"]';
  const hrefs = [...new Set([...document.querySelectorAll(sel)].map(a => a.getAttribute('href')))];
  return hrefs.map(h => {
    let c = document.querySelector(`a[href="${h}"]`);
    while (c.parentElement) {
      const n = new Set([...c.parentElement.querySelectorAll(sel)].map(a => a.getAttribute('href'))).size;
      if (n > 1) break;
      c = c.parentElement;
    }
    return {href: h, text: c.innerText.replace(/\\s+/g, ' ').trim()};
  });
}"""
MATCHES_JS = """() => { const m = document.body.innerText.match(/(\\d+)\\s+matches/); return m ? +m[1] : -1; }"""

CARD_RE = re.compile(
    r"(?P<name>\S.*?)\s+(?P<year>(?:19|20)\d{2})\s+(?P<km>[\d,]+)\s*km\b"
    r"(?:\s+(?P<engine>\d+(?:\.\d+)?\s*L))?\s+(?:AED\s*)?(?P<price>\d{1,3}(?:,\d{3})+|\d{4,})", re.I)
RESERVED_RE = re.compile(r"\b(reserved|sold|booked)\b", re.I)


class AlFuttaimPreowned(Source):
    name = ""
    label = ""

    def search(self) -> list:
        try:
            from playwright.sync_api import Error as PWError, sync_playwright
        except ImportError:
            raise RuntimeError("Playwright not installed: run  pip install playwright && playwright install chromium")
        listings, seen, failed = [], set(), []
        h = self.cfg.get("http", {})
        pages = self.src_cfg.get("pages", [])
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT, locale="en-US",
                                        viewport={"width": 1366, "height": 900})
                for i, url in enumerate(pages):
                    if i:
                        time.sleep(random.uniform(h.get("min_delay_seconds", 4), h.get("max_delay_seconds", 8)))
                    cards = "failed"
                    for attempt in range(2):  # the results API sometimes never answers; reload once
                        try:
                            cards = self.load_cards(page, url)
                        except PWError as e:
                            log.warning("%s: could not load %s: %s", self.name, url, str(e)[:200])
                        if cards != "failed":
                            break
                        log.info("%s: %s showed no results yet; retrying", self.name, url)
                    if cards == "failed":
                        failed.append(url)
                        continue
                    for l in self.parse_cards(cards or [], url):
                        if l.source_id not in seen:
                            seen.add(l.source_id)
                            listings.append(l)
            finally:
                browser.close()
        if failed:
            # Never report "no cars" for a page that didn't load: that's how a car gets missed.
            self.warnings.append(f"{len(failed)} page(s) didn't load")
            if len(failed) == len(pages):
                raise RuntimeError(f"no {self.label} page loaded its results: {', '.join(failed)}")
        return listings

    def load_cards(self, page, url):
        """Cards on the page, None for a 404, or "failed" if the results never appeared."""
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else 0
        if status == 404:
            log.info("%s: %s does not exist (404), skipping", self.name, url)
            return None
        if status in (401, 403, 429):
            raise SourceBlocked(f"HTTP {status} from {url}")
        # "0 matches" shows while results are still loading (and "undefined matches" if the results API
        # never answers), so wait for cards, or a zero that holds for 20 seconds.
        start, cards, expected, stable = time.monotonic(), [], -1, 0
        while time.monotonic() - start < 45:
            page.wait_for_timeout(1500)
            head = page.content()[:5000]
            if any(m in head for m in BLOCK_MARKERS):
                raise SourceBlocked(f"bot-protection page at {url}")
            expected = page.evaluate(MATCHES_JS)
            previous = len(cards)
            cards = page.evaluate(CARDS_JS)
            if cards and len(cards) >= expected:
                break
            if expected == 0 and time.monotonic() - start > 20:
                break
            if cards:  # more matches than cards: scroll to load the rest, stop once it stops growing
                stable = stable + 1 if len(cards) == previous else 0
                if stable >= 3:
                    break
                page.mouse.wheel(0, 4000)
        if expected < 0 and not cards:
            return "failed"
        log.info("%s: %s -> %d matches, %d cards read", self.name, url, max(expected, 0), len(cards))
        return cards

    def parse_cards(self, cards: list, page_url: str) -> list:
        base = re.match(r"https?://[^/]+", page_url).group(0)
        out = []
        for c in cards:
            reserved = bool(RESERVED_RE.search(c["text"]))
            m = CARD_RE.search(RESERVED_RE.sub(" ", c["text"]))
            if not m:
                log.warning("%s: could not parse card %r", self.name, c["text"][:120])
                continue
            words = m.group("name").split()
            make = model = None
            for entry in self.src_cfg.get("models", []):
                k = len(entry.split())
                # badges ("New arrival", ...) may come before the name, so search for make+model
                at = next((i for i in range(len(words)) if norm(" ".join(words[i:i + k])) == norm(entry)), None)
                if at is not None:
                    make, _, model = entry.partition(" ")
                    trim = " ".join(words[at + k:])
                    break
            if not make:
                continue  # not one of the wanted models
            vin = c["href"].rstrip("/").rsplit("/", 1)[-1].upper()
            out.append(Listing(
                source=self.name, source_id=vin, url=base + c["href"],
                title=f"{m.group('year')} {make} {model} {trim}".strip(),
                make=make, model=model, trim=trim, year=int(m.group("year")),
                km=int(m.group("km").replace(",", "")), price=int(m.group("price").replace(",", "")),
                specs="GCC", city="Al-Futtaim showroom", seller_type="Official dealer",
                description="",  # detail pages are off-limits (robots.txt); title carries hybrid/HEV
                reserved=reserved, priority_source=True,
            ))
        return out

    def fetch_details(self, listing: Listing):
        pass  # robots.txt disallows */details/*


class ToyotaPreowned(AlFuttaimPreowned):
    name = "toyota_preowned"
    label = "Toyota Pre-Owned"


class HondaPreowned(AlFuttaimPreowned):
    name = "honda_preowned"
    label = "Honda Pre-Owned"
