"""Criteria checks, red flags, mislabel, plug-in and mild hybrid detection."""
import re

from .db import norm
from .models import Listing

NEGATIONS = re.compile(r"\b(no|zero|never|without|nil|0)\s+(major\s+|previous\s+|prior\s+|any\s+)?$")
NEGATED_AFTER = re.compile(r"^\s*-?\s*(free|history\s*:?\s*(none|no))\b")


def kw_regex(keyword: str) -> re.Pattern:
    """Whole-word match, allowing a plural 's'. Keywords that start with a digit (e.g. '300h')
    may follow letters, so 'ES300h' matches '300h'."""
    left = "" if keyword[:1].isdigit() else r"(?<![a-z])"
    return re.compile(left + re.escape(keyword.lower()) + r"s?(?![a-z])")


def find_keywords(text: str, keywords: list, skip_negated: bool = False) -> list:
    text = (text or "").lower()
    found = []
    for kw in keywords:
        for m in kw_regex(kw).finditer(text):
            if skip_negated and (NEGATIONS.search(text[max(0, m.start() - 25):m.start()])
                                 or NEGATED_AFTER.match(text[m.end():m.end() + 20])):
                continue
            found.append(kw)
            break
    return found


def in_list(value: str, options: list) -> bool:
    return norm(value) in {norm(o) for o in options}


def check_criteria(l: Listing, cfg: dict) -> list:
    """Hard criteria. Returns reasons for rejection (empty list = match)."""
    c = cfg["criteria"]
    reasons = []
    # Always required, on every site
    if l.reserved:
        reasons.append("reserved/sold")
    if l.is_mild:
        reasons.append("mild hybrid")
    if not ("hybrid" in norm(l.fuel) or l.hybrid_confirmed):
        reasons.append(f"not a hybrid (fuel {l.fuel or '?'}, no hybrid/HEV in text)")
    if l.fuel and in_list(l.fuel, c.get("excluded_fuels", [])):
        reasons.append(f"fuel {l.fuel}")
    if l.is_phev and not c.get("include_plugin_hybrids", True):
        reasons.append("plug-in hybrid")
    if l.priority_source:
        # Official Toyota / Honda pre-owned: the source already limited it to the wanted models;
        # price, km, year, specs, body and location are deliberately not checked.
        l.location_tier = "priority"
        return reasons

    if l.year is None or l.year < c["min_year"]:
        reasons.append(f"year {l.year}")
    if l.km is None or l.km < c.get("min_km", 0) or l.km >= c["max_km"]:
        reasons.append(f"km {l.km}")
    if not l.price or l.price >= c["max_price_aed"]:
        reasons.append(f"price {l.price}")
    if not in_list(l.specs, c["specs"]):
        reasons.append(f"specs {l.specs}")
    # An empty body means the site only shows it on the detail page; the runner checks again after.
    if l.body and not in_list(l.body, c["body_types"]):
        reasons.append(f"body {l.body}")
    if any(norm(l.make).startswith(norm(m)) for m in c["excluded_makes"]):
        reasons.append(f"excluded make {l.make}")
    if in_list(l.city, c["locations"]["primary"]):
        l.location_tier = "primary"
    elif in_list(l.city, c["locations"]["secondary"]):
        l.location_tier = "secondary"
    else:
        reasons.append(f"location {l.city}")
    return reasons


def annotate(l: Listing, cfg: dict):
    """Soft checks: PHEV, mild hybrid, mislabel, reserved wording, red flags, priority/Japanese markers."""
    chk = cfg["checks"]
    name_text = f"{l.title} {l.trim} {l.model}"
    all_text = f"{name_text} {l.description or ''}"

    l.is_phev = bool(find_keywords(all_text, chk["phev_keywords"]))
    l.hybrid_confirmed = bool(find_keywords(all_text, chk["hybrid_keywords"]))
    l.is_mild = not l.is_phev and bool(
        find_keywords(all_text, chk.get("mild_hybrid_keywords", []))
        or any(norm(f"{l.make} {l.model}").startswith(norm(m)) for m in chk.get("mild_hybrid_models", [])))
    if find_keywords(l.title, chk.get("reserved_keywords", [])):
        l.reserved = True

    l.warnings = []
    if not l.hybrid_confirmed:
        if l.description is None:
            l.warnings.append("'hybrid' not in title/trim (description not checked yet)")
        else:
            l.warnings.append("possible mislabel: 'hybrid'/'HEV' not in title, trim or description")
    elif not find_keywords(name_text, chk["hybrid_keywords"]):
        petrol_words = find_keywords(name_text, chk.get("non_hybrid_title_keywords", []))
        if petrol_words:
            l.warnings.append(f"possible mislabel: title says '{petrol_words[0]}', not hybrid/HEV")
    l.red_flags = find_keywords(f"{l.title} {l.description or ''}", chk["red_flags"], skip_negated=True)

    l.is_japanese = in_list(l.make, cfg["criteria"]["japanese_makes"])
    l.is_priority = False
    for entry in cfg.get("priority_models", []):
        make, _, model = entry.partition(" ")
        if norm(l.make) == norm(make) and norm(l.model).startswith(norm(model)):
            l.is_priority = True
            break


def is_flagged(l: Listing) -> bool:
    return bool(l.red_flags) or any(w.startswith("possible mislabel") for w in l.warnings)
