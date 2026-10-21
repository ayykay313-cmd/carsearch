"""Cars24 UAE: server-rendered pages embed each car (status, fuel, specs badge, hub city, price, km).

Pages: /buy-used-hybrid-cars-dubai/ plus a few model pages (config) to catch hybrids filed under
"Petrol"; only cars whose fuel or title says hybrid/HEV pass the criteria. robots.txt only disallows
the gf/sf/search query parameters, which we don't use. Status RESERVED or COMING_SOON = not available.
"""
import logging
import re

from ..models import Listing
from .base import Source
from .nextdata import next_payload, payload_rows, resolve

log = logging.getLogger(__name__)

BASE = "https://www.cars24.ae"
NAMES = {"bmw": "BMW", "gmc": "GMC", "mg": "MG", "byd": "BYD", "rav4": "RAV4", "cr-v": "CR-V",
         "suv": "SUV", "mercedes-benz": "Mercedes Benz"}


def pretty(s: str) -> str:
    s = (s or "").strip()
    return NAMES.get(s.lower(), s.title() if s.isupper() or s.islower() else s)


class Cars24(Source):
    name = "cars24"

    def search(self) -> list:
        listings, seen = [], set()
        for path in self.src_cfg.get("paths", []):
            r = self.http.get(f"{BASE}/{path.strip('/')}/")
            if f"/{path.strip('/')}" not in r.url:
                log.warning("cars24: %s redirected to %s; check the path in config.yaml", path, r.url)
                continue
            items = [l for l in self.parse(r.text) if l.source_id not in seen]
            seen.update(l.source_id for l in items)
            listings += items
            total = re.search(r"<h1[^>]*>\s*(\d+)\s", r.text)
            if total and int(total.group(1)) > len(items) + 1:
                self.warnings.append(f"{path}: only the first {len(items)} of {total.group(1)} cars are on the page")
            log.info("cars24 %s: %d listings", path, len(items))
        return listings

    def parse(self, html: str) -> list:
        rows = payload_rows(next_payload(html))
        out, ids = [], set()
        for raw in rows.values():
            if not (isinstance(raw, dict) and "appointmentId" in raw) or raw["appointmentId"] in ids:
                continue
            ids.add(raw["appointmentId"])
            c = resolve(raw, rows)
            badges = [b.get("text", "") for b in c.get("carInfoBadges") or [] if isinstance(b, dict)]
            alt = ((c.get("listingImage") or {}).get("altText") or "").split(" - ")  # year - car - BODY - fuel - gear
            make, model = pretty(c.get("make")), pretty(c.get("model"))
            variant = (c.get("variant") or c.get("subtitle") or "").strip()
            out.append(Listing(
                source=self.name,
                source_id=str(c["appointmentId"]),
                url=BASE + "/" + (c.get("cdpRelativeUrl") or "").lstrip("/"),
                title=f"{c.get('year')} {make} {model} {variant}".strip(),
                make=make, model=model, trim=variant,
                year=c.get("year"),
                km=(c.get("odometer") or {}).get("value"),
                price=(c.get("listingPrice") or {}).get("value"),
                specs="GCC" if "GCC" in badges else next((b for b in badges if "spec" in b.lower()), ""),
                body=pretty(alt[2]) if len(alt) > 2 else "",
                fuel=c.get("fuelType") or "",
                city=(c.get("location") or {}).get("hubName") or "",
                seller_type="Cars24" + (" (C2C)" if c.get("businessVertical") == "C2C" else ""),
                description="",  # no detail page needed: fuel, specs and status are on the card
                reserved=c.get("status") != "AVAILABLE",  # RESERVED, COMING_SOON, ...
            ))
        return out

    def fetch_details(self, listing: Listing):
        pass
