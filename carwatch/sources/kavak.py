"""Kavak UAE: server-rendered catalogue pages embed each car card as JSON (status, year, km, price).

robots.txt asks for Crawl-delay: 20, so this source waits 20-25 s between requests (config).
Kavak's own hybrid filter misses cars filed under another fuel type (e.g. an "Accord 2.0 HEV"), so we
scan /ae/preowned/hybrid plus the year pages from min_year on, and keep cars whose trim says
hybrid/HEV. Cards with car_status "Booked" are not available.

Assumptions: Kavak shows no city (it delivers UAE-wide from its hubs), so cars count as Dubai; the
image path starts with the market ("gcc/..."), which is used as the regional specs.
"""
import logging
import re

from ..config import now_dubai
from ..models import Listing
from .base import Source
from .nextdata import json_objects, next_payload

log = logging.getLogger(__name__)

BASE = "https://www.kavak.com/ae/preowned"
NAMES = {"bmw": "BMW", "gmc": "GMC", "mg": "MG", "byd": "BYD", "suv": "SUV", "cr-v": "CR-V"}


def pretty(s: str) -> str:
    s = (s or "").replace("_", " ").strip()
    return NAMES.get(s.lower(), s.title() if s.islower() else s)


class Kavak(Source):
    name = "kavak"

    def paths(self) -> list:
        years = range(self.cfg["criteria"]["min_year"], now_dubai().year + 2)
        return ["hybrid"] + [str(y) for y in years]

    def search(self) -> list:
        listings, seen = [], set()
        for path in self.paths():
            for page in range(self.src_cfg.get("max_pages", 8)):
                url = f"{BASE}/{path}" + (f"?page={page}" if page else "")
                items = [l for l in self.parse(self.http.get(url).text) if l.source_id not in seen]
                if not items:
                    break
                seen.update(l.source_id for l in items)
                listings += items
                log.info("kavak %s page %d: %d listings", path, page, len(items))
        return listings

    def parse(self, html: str) -> list:
        out, ids = [], set()
        for c in json_objects(next_payload(html), r'\{"id":"\d+","url":"https://www\.kavak\.com/ae/'):
            a = c.get("analytics") or {}
            if not a or c["id"] in ids:
                continue
            ids.add(c["id"])
            # subtitle: "2024 • 33,679 km • 2.5 VXR HYBRID 4WD E-CVT • Automatic"
            parts = [p.strip() for p in (c.get("subtitle") or "").split("•")]
            km = int(re.sub(r"\D", "", parts[1])) if len(parts) > 1 and re.search(r"\d", parts[1]) else None
            trim = parts[2] if len(parts) > 2 else ""
            slug = c.get("url", "").rstrip("/").rsplit("/", 1)[-1]  # make-model-trim-body-year
            body = slug.rsplit("-", 2)[-2] if slug.count("-") >= 2 else ""
            make, model = pretty(a.get("car_make")), a.get("car_model") or ""
            out.append(Listing(
                source=self.name,
                source_id=str(c["id"]),
                url=c.get("url", ""),
                title=f"{a.get('car_year')} {make} {model} {trim}".strip(),
                make=make, model=model, trim=trim,
                year=a.get("car_year"),
                km=km,
                price=int(float(a["car_price"])) if a.get("car_price") else None,
                specs="GCC" if (c.get("image") or "").startswith("gcc/") else "",
                body=pretty(body),
                fuel="",  # Kavak's card has no fuel type; the trim must say hybrid/HEV
                city="Dubai",
                seller_type="Kavak (delivers UAE-wide)",
                description="",
                reserved=(a.get("car_status") or "").lower() in ("booked", "reserved", "sold", "sold out")
                         or bool(a.get("coming_soon"))
                         or any(b.get("key") in ("booked", "reserved", "sold") for b in c.get("badges") or []),
            ))
        return out

    def fetch_details(self, listing: Listing):
        pass
