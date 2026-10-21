from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    source: str
    source_id: str
    url: str
    title: str
    make: str
    model: str
    trim: str = ""
    year: Optional[int] = None
    km: Optional[int] = None
    price: Optional[int] = None
    specs: str = ""          # regional specs, e.g. "GCC"
    body: str = ""
    fuel: str = ""
    city: str = ""
    seller_type: str = ""
    description: Optional[str] = None  # None = detail page not fetched yet
    reserved: bool = False         # reserved / sold / booked: never reported
    priority_source: bool = False  # official Toyota/Honda pre-owned: skips price/km/year/location criteria

    # Filled in by checks / scoring
    is_phev: bool = False
    is_mild: bool = False
    hybrid_confirmed: bool = False
    red_flags: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    is_priority: bool = False
    is_japanese: bool = False
    location_tier: str = ""   # "primary" | "secondary"
    deal_pct: Optional[float] = None   # % below comparable average (negative = above)
    deal_avg: Optional[int] = None
    deal_n: int = 0
    verdict: str = ""
    previous_price: Optional[int] = None  # set for price-drop alerts
    also_on: list = field(default_factory=list)  # same car listed on other sites (URLs)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


class SourceBlocked(Exception):
    """The site refused us (bot protection, 403, captcha). We skip it rather than work around it."""
