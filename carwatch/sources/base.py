from ..http import PoliteSession
from ..models import Listing


class Source:
    name = ""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.src_cfg = cfg["sources"].get(self.name, {})
        # a site can ask for a longer delay (e.g. Kavak's robots.txt Crawl-delay: 20)
        self.http = PoliteSession({**cfg.get("http", {}), **self.src_cfg.get("http", {})})
        self.warnings = []  # partial problems shown next to the source in the report

    def search(self) -> list:
        """Return listings from search results (cheap: few requests). Description may be None."""
        raise NotImplementedError

    def fetch_details(self, listing: Listing):
        """Fill listing.description from the detail page (one request)."""
        raise NotImplementedError
