"""CarSwitch: a Next.js site; each page embeds its data in the self.__next_f payload.

We page through /uae/used-cars/electric-hybrid-cars-for-sale-in-uae (about 70 cars). robots.txt
disallows the make/year/city/fuel query filters, so filtering happens locally. Search cards lack body
and fuel type, so the detail page is fetched for the few cars that pass the other checks (it also
tells hybrids apart from EVs). CarSwitch shows no seller description, so red flags only see the title.
"""
import json
import logging
import re

from bs4 import BeautifulSoup

from ..models import Listing
from .base import Source
from .nextdata import json_objects, next_payload

log = logging.getLogger(__name__)

BASE = "https://carswitch.com"
CATEGORY = "/uae/used-cars/electric-hybrid-cars-for-sale-in-uae"
EV_ONLY_MAKES = {"tesla", "polestar", "lucid", "rivian"}  # never hybrids: skip without a detail page
NAMES = {"bmw": "BMW", "gmc": "GMC", "mg": "MG", "byd": "BYD", "abudhabi": "Abu Dhabi",
         "suv": "SUV", "gcc": "GCC", "rav4": "RAV4", "cr-v": "CR-V", "hr-v": "HR-V"}


def pretty(slug: str) -> str:
    slug = (slug or "").strip()
    return NAMES.get(slug.lower(), slug.replace("-", " ").title())




class CarSwitch(Source):
    name = "carswitch"

    def search(self) -> list:
        listings, seen = [], set()
        for page in range(1, self.src_cfg.get("max_pages", 6) + 1):
            url = BASE + CATEGORY + (f"?page={page}" if page > 1 else "")
            payload = next_payload(self.http.get(url).text)
            page_items = [l for l in self.parse_search(payload) if l.source_id not in seen]
            if not page_items:
                break
            seen.update(l.source_id for l in page_items)
            listings += page_items
            log.info("carswitch page %d: %d listings", page, len(page_items))
            total = re.search(r'"total":(\d+)', payload)
            if total and len(seen) >= int(total.group(1)):
                break
        return listings

    def parse_search(self, payload: str) -> list:
        out, ids = [], set()
        for c in json_objects(payload, r'\{"id":"\d+","make"'):
            if c.get("carType") not in (None, "used") or "mileage" not in c or c["id"] in ids:
                continue  # the payload repeats each car
            ids.add(c["id"])
            make, model = pretty(c.get("make")), pretty(c.get("model"))
            out.append(Listing(
                source=self.name,
                source_id=str(c["id"]),
                url=f"{BASE}/{c.get('city')}/used-car/{c.get('make')}/{c.get('model')}/{c.get('year')}/{c['id']}",
                title=f"{c.get('year')} {make} {model} {c.get('variant') or ''}".strip(),
                make=make,
                model=model,
                trim=(c.get("variant") or "").strip(),
                year=c.get("year"),
                km=c.get("mileage"),
                price=c.get("price"),
                specs=pretty(c.get("regionalSpecs")),
                fuel="Electric" if (c.get("make") or "").lower() in EV_ONLY_MAKES else "",
                city=pretty(c.get("city")),
                seller_type=pretty((c.get("listingType") or "").replace("_", "-")),
                reserved=c.get("status", "published") != "published",
            ))
        return out

    def fetch_details(self, listing: Listing):
        html = self.http.get(listing.url).text
        for car in json_objects(next_payload(html), r'\{"id":\d+,"uuid"'):
            if str(car.get("id")) == listing.source_id:
                listing.body = pretty(car.get("bodyType"))
                listing.fuel = pretty((car.get("fuelType") or "").replace("_", "-"))
                listing.trim = (car.get("variant") or listing.trim).strip()
                listing.reserved = listing.reserved or car.get("status", "published") != "published"
                break
        meta = BeautifulSoup(html, "lxml").find("meta", attrs={"name": "description"})
        listing.description = meta["content"] if meta and meta.has_attr("content") else ""
