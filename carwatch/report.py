"""Builds the daily message (Telegram HTML) and a plain-text copy for reports/."""
import re
from html import escape

from .filters import is_flagged
from .scoring import deal_label, sort_key

SOURCE_LABELS = {
    "dubicars": "DubiCars", "dubizzle": "Dubizzle", "yallamotor": "YallaMotor", "carswitch": "CarSwitch",
    "kavak": "Kavak", "cars24": "Cars24", "honda_preowned": "Honda Pre-Owned",
    "toyota_preowned": "Toyota Pre-Owned",
}
SITE_DOMAINS = {"dubicars.com": "DubiCars", "carswitch.com": "CarSwitch", "toyota.ae": "Toyota Pre-Owned",
                "honda.ae": "Honda Pre-Owned", "kavak.com": "Kavak", "cars24.ae": "Cars24"}


def money(v) -> str:
    return f"AED {v:,}" if v else "AED ?"


def listing_block(l, cfg, price_drop=False, number=None) -> str:
    marks = ("⭐" if l.is_priority else "") + ("🇯🇵" if l.is_japanese else "") + ("🔌" if l.is_phev else "")
    name = escape(f"{l.year} {l.make} {l.model}")
    label = deal_label(l, cfg)
    num = f"<b>#{number}</b> " if number else ""  # the 👎 #N buttons refer to this number
    lines = [f"{num}{marks}{' ' if marks else ''}<b>{name}</b>{'  ' + label if label else ''}"]
    if price_drop and l.previous_price:
        drop = l.previous_price - l.price
        lines.append(f"📉 <b>{money(l.price)}</b> (was {money(l.previous_price)}, −{drop:,}) · {l.km:,} km")
    else:
        lines.append(f"<b>{money(l.price)}</b> · {l.km:,} km · {l.year}")
    lines.append(f"Trim: {escape(l.trim or '—')} · 📍 {escape(l.city)} · {escape(l.seller_type or '')} · "
                 f"{SOURCE_LABELS.get(l.source, l.source)}")
    if l.deal_pct is not None:
        direction = "below" if l.deal_pct >= 0 else "above"
        lines.append(f"📊 {abs(l.deal_pct):.0f}% {direction} avg {money(l.deal_avg)} ({l.deal_n} similar)")
    if l.red_flags:
        lines.append(f"🚩 Red flags: {escape(', '.join(l.red_flags))}")
    for w in l.warnings:
        lines.append(f"⚠️ {escape(w)}")
    if l.verdict:
        lines.append(f"🤖 {escape(l.verdict)}")
    links = f'🔗 <a href="{escape(l.url, quote=True)}">View listing</a>'
    for url in l.also_on:
        site = next((v for k, v in SITE_DOMAINS.items() if k in url.lower()), "other site")
        links += f' · <a href="{escape(url, quote=True)}">also on {site}</a>'
    lines.append(links)
    return "\n".join(lines)


def section(title: str, items: list, cfg: dict, numbering: dict, price_drop=False, limit=None) -> str:
    """numbering maps #N -> listing key. Every listing gets a number (even past the limit) so the
    Telegram message and the saved full report use the same numbers."""
    if not items:
        return ""
    items = sorted(items, key=lambda l: sort_key(l, cfg))
    numbers = []
    for l in items:
        numbering[len(numbering) + 1] = l.key
        numbers.append(len(numbering))
    body = "\n\n".join(listing_block(l, cfg, price_drop, n) for l, n in zip(items[:limit], numbers))
    more = f"\n\n…and {len(items) - limit} more (full list in the reports/ folder)" if len(items) > limit else ""
    return f"<b>{title} ({len(items)})</b>\n\n{body}{more}"


def split_sections(new: list):
    official = [l for l in new if l.priority_source]  # always on top, flags shown inline
    new = [l for l in new if not l.priority_source]
    flagged = [l for l in new if is_flagged(l)]
    clean = [l for l in new if not is_flagged(l)]
    return {
        "official": official,
        "dubai": [l for l in clean if not l.is_phev and l.location_tier == "primary"],
        "phev": [l for l in clean if l.is_phev],
        "secondary": [l for l in clean if not l.is_phev and l.location_tier == "secondary"],
        "flagged": flagged,
    }


def build(new: list, drops: list, source_status: dict, ai_status: str, cfg: dict, date_str: str,
          full=False, numbering: dict = None, headline: str = None) -> str:
    """full=True lists every listing (for the saved report); otherwise sections are capped for Telegram.
    Pass a dict as numbering to get back the #N -> listing key map."""
    numbering = {} if numbering is None else numbering
    limit = 10**6 if full else cfg["notify"].get("max_listings_per_section", 25)
    s = split_sections(new)
    secondary_names = " / ".join(cfg["criteria"]["locations"]["secondary"])
    primary_names = " / ".join(cfg["criteria"]["locations"]["primary"])
    header = (f"<b>{escape(headline or '🚗 Car Watch')} — {date_str}</b>\n"
              f"{len(new)} new match{'es' if len(new) != 1 else ''}")
    if drops:
        header += f" · {len(drops)} price drop{'s' if len(drops) != 1 else ''}"
    parts = [header]
    if not new and not drops:
        parts.append("No new matches today.")
    parts += [
        section("🏆 Toyota / Honda Pre-Owned (official, any price/km)", s["official"], cfg, numbering, limit=limit),
        section(f"📍 {primary_names}", s["dubai"], cfg, numbering, limit=limit),
        section("🔌 Plug-in hybrids", s["phev"], cfg, numbering, limit=limit),
        section(f"📍 {secondary_names} (lower priority)", s["secondary"], cfg, numbering, limit=limit),
        section("⚠️ Flagged — check carefully", s["flagged"], cfg, numbering, limit=limit),
        section("📉 Price drops", drops, cfg, numbering, price_drop=True, limit=limit),
    ]
    status_lines = []
    for src, st in source_status.items():
        status_lines.append(f"{SOURCE_LABELS.get(src, src)}: {escape(st)}")
    footer = "<b>Sources</b>\n" + "\n".join(status_lines) + f"\nAI verdicts: {escape(ai_status)}"
    footer += "\n⭐ priority model · 🇯🇵 Japanese · 🔌 plug-in hybrid"
    if numbering:
        footer += "\nTap 👎 #N to remove a car from consideration (tap again to undo), or type: remove 3"
    parts.append(footer)
    return "\n\n".join(p for p in parts if p)


def to_plain(html_text: str) -> str:
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2: \1", html_text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x27;", "'")
