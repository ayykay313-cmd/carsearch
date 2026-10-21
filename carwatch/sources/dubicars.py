"""DubiCars: server-rendered search pages carry each card's data as JSON attributes.

We use the /uae/used/hybrid-cars path (plus priority-model pages from config) with the price-to (pt)
and km-to (kt) filters, which robots.txt allows. Year/location filters (yf, l) are disallowed there,
so those are applied locally. Sold/inactive cards (flags.is_item_sold / is_item_active) are skipped.
"""
import json
import logging
import re

from bs4 import BeautifulSoup

from ..models import Listing
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://www.dubicars.com"


def _json_attr(el, attr):
    try:
        return json.loads(el[attr]) if el and el.has_attr(attr) else {}
    except (ValueError, TypeError):
        return {}


def _int(v):
    if v is None:
        return None
    digits = re.sub(r"[^\d]", "", str(v))
    return int(digits) if digits else None


class DubiCars(Source):
    name = "dubicars"

    def search_url(self, path: str, page: int) -> str:
        c = self.cfg["criteria"]
        url = f"{BASE}/{path}?pt={c['max_price_aed']}&kt={c['max_km']}"
        return url + (f"&page={page}" if page > 1 else "")

    def search(self) -> list:
        listings, seen = [], set()
        # The hybrid category first, then priority-model pages for hybrids filed under another fuel type.
        for path in ["uae/used/hybrid-cars"] + self.src_cfg.get("extra_paths", []):
            for page in range(1, self.src_cfg.get("max_pages", 20) + 1):
                r = self.http.get(self.search_url(path, page))
                if f"/{path}" not in r.url:  # unknown model slugs redirect to the all-cars page
                    log.warning("dubicars: %s redirected to %s; check the path in config.yaml", path, r.url)
                    break
                page_items = [l for l in self.parse_search(r.text) if l.source_id not in seen]
                if not page_items:
                    break
                seen.update(l.source_id for l in page_items)
                listings += page_items
                log.info("dubicars %s page %d: %d listings", path, page, len(page_items))
                if f"page={page + 1}" not in r.text:
                    break
        return listings

    def parse_search(self, html: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        out = []
        for li in soup.select("li.serp-list-item"):
            ga = _json_attr(li, "data-ga4-detail")
            mp = _json_attr(li, "data-mixpanel-detail")
            if not ga or not mp:
                continue  # ad slot or banner
            link = li.select_one('a[href$=".html"]')
            price = mp.get("item_discounted_price") or mp.get("item_local_price") or ga.get("price")
            flags = _json_attr(li, "data-fpa-preview").get("flags") or {}
            out.append(Listing(
                source=self.name,
                source_id=str(ga.get("item_id") or mp.get("item_id")),
                url=link["href"] if link else "",
                title=li.get("data-item-title") or f"{ga.get('car_make')} {ga.get('car_model')}",
                make=ga.get("car_make") or "",
                model=ga.get("car_model") or "",
                trim=ga.get("car_trim") or "",
                year=_int(ga.get("car_year")),
                km=_int(mp.get("item_mileage")),
                price=_int(price),
                specs=ga.get("regional_specs") or "",
                body=ga.get("body_type") or "",
                fuel=ga.get("fuel_type") or "",
                city=ga.get("city") or "",
                seller_type=ga.get("seller_type") or "",
                reserved=bool(flags.get("is_item_sold")) or flags.get("is_item_active") is False,
            ))
        return out

    def fetch_details(self, listing: Listing):
        soup = BeautifulSoup(self.http.get(listing.url).text, "lxml")
        # #description-list holds the seller's text; the AI-generated blurb lives elsewhere and is skipped.
        el = soup.select_one("#description-list") or soup.select_one("#card-description")
        text = el.get_text(" ", strip=True) if el else ""
        listing.description = re.sub(r"^Description\s+", "", text)
        h1 = soup.select_one("h1")
        if h1 and h1.get_text(strip=True):
            listing.title = h1.get_text(" ", strip=True)
