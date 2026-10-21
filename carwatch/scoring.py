from statistics import mean

from .db import DB
from .models import Listing


def score(l: Listing, db: DB, cfg: dict):
    d = cfg["deal_score"]
    prices = db.comparables(l, d["comparable_window_days"], cfg["criteria"]["specs"])
    l.deal_n = len(prices)
    if len(prices) >= d["min_comparables"] and l.price:
        avg = mean(prices)
        l.deal_avg = round(avg)
        l.deal_pct = round((avg - l.price) / avg * 100, 1)


def deal_label(l: Listing, cfg: dict) -> str:
    if l.deal_pct is None:
        return ""
    d = cfg["deal_score"]
    if l.deal_pct >= d["great_deal_pct"]:
        return "🔥 great deal"
    if l.deal_pct >= d["good_deal_pct"]:
        return "💰 deal"
    return ""


def sort_key(l: Listing, cfg: dict):
    """Deal score first (best first); unscored listings after, by price then km."""
    priority = 0 if (cfg.get("priority_first") and l.is_priority) else 1
    scored = 0 if l.deal_pct is not None else 1
    return (priority, scored, -(l.deal_pct or 0), l.price or 10**9, l.km or 10**9)
