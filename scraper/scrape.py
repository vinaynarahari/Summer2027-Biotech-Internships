"""Poll every company's job board, add new internships, close vanished ones.

    python -m scraper.scrape                 # all companies
    python -m scraper.scrape --only Moderna  # one company (debugging)
    python -m scraper.scrape --dry-run       # print, don't write
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import classify as C
from .ats import fetch, now_utc
from .store import COMPANIES, load_listings, save_listings

MISSES_BEFORE_CLOSE = 2  # consecutive successful runs where the job is gone


MASS_MISS_MIN = 5       # guard only kicks in when this many roles vanish at once
MASS_MISS_SHARE = 0.6   # ...and they are at least this share of the company's open roles
GUARD_RUNS = 2          # after this many consecutive guarded runs (~4h), accept it as real


def apply_misses(listings: list[dict], company: str, seen_keys: set, now: int, suspicious: list) -> int:
    """Count a 'miss' for each open bot listing not seen this run; close after N misses.

    Guard: if most of a company's open roles vanish in one run, that is far more
    likely a scraper/ATS glitch (pagination, rate limit, site change) than a real
    mass closure, so record nothing and flag it instead.
    """
    open_rows = [l for l in listings if l.get("source") == "bot" and l.get("company_name") == company and l.get("active")]
    missing = [l for l in open_rows if l.get("ats_key") not in seen_keys]
    if len(missing) >= MASS_MISS_MIN and len(missing) >= MASS_MISS_SHARE * len(open_rows):
        for l in missing:
            l["guarded"] = l.get("guarded", 0) + 1
        # A real mass closure persists: after GUARD_RUNS guarded runs in a row, let misses count.
        if min(l["guarded"] for l in missing) <= GUARD_RUNS:
            suspicious.append(f"{company} ({len(missing)}/{len(open_rows)})")
            return 0
    closed = 0
    for l in missing:
        l["misses"] = l.get("misses", 0) + 1
        if l["misses"] >= MISSES_BEFORE_CLOSE:
            l["active"] = False
            l["date_updated"] = now
            closed += 1
    return closed


def build_listing(company: dict, raw: dict, now: int) -> dict | None:
    title = raw["title"].strip()
    if not C.looks_like_internship(title, raw.get("is_intern_hint", False)):
        return None
    locations = C.clean_locations(raw.get("locations", []))
    if not C.is_us(locations, raw.get("country") or ""):
        return None
    if all(re.fullmatch(r"remote|unknown", l, re.I) for l in locations) and C.title_says_foreign(title):
        return None
    terms = C.detect_terms(title, raw.get("description", ""))
    if C.is_stale(terms):
        return None
    return {
        "id": str(uuid.uuid4()),
        "source": "bot",
        "ats_key": raw["key"],
        "company_name": company["name"],
        "company_url": company.get("url", ""),
        "title": title,
        "category": C.categorize(title),
        "locations": locations,
        "country": raw.get("country") or "",
        "terms": terms,
        "url": raw["url"],
        "active": True,
        "is_visible": True,
        "sponsorship": C.detect_sponsorship(raw.get("description", "")),
        "degrees": C.detect_degrees(title),
        "date_posted": min(raw.get("posted") or now, now),
        "date_updated": now,
        "last_seen": now,
        "misses": 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="company name substring")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    companies = json.loads(COMPANIES.read_text())["companies"]
    if a.only:
        companies = [c for c in companies if a.only.lower() in c["name"].lower()]

    listings = [
        l for l in load_listings()
        if l.get("source") != "bot" or l.get("locked") or C.is_us(l["locations"], l.get("country", ""))
    ]
    by_key = {l["ats_key"]: l for l in listings if l.get("ats_key")}
    by_url = {l["url"]: l for l in listings}
    now = now_utc()

    results: dict[str, list[dict] | None] = {}
    with ThreadPoolExecutor(12) as ex:
        futs = {ex.submit(fetch, c): c for c in companies}
        for f in as_completed(futs):
            c = futs[f]
            results[c["name"]] = f.result()

    added, reopened, closed, failed, suspicious = [], 0, 0, [], []
    for c in companies:
        raws = results.get(c["name"])
        if raws is None:
            failed.append(c["name"])
            continue
        seen_keys = set()
        for raw in raws:
            new = build_listing(c, raw, now)
            if not new:
                continue
            seen_keys.add(raw["key"])
            old = by_key.get(raw["key"]) or by_url.get(raw["url"])
            if old:
                if old.get("source") == "bot" and not old.get("locked"):
                    # re-apply classifier rules so improvements reach old rows
                    old["category"], old["degrees"] = new["category"], new["degrees"]
                    old["locations"], old["country"] = new["locations"], new["country"]
                old["last_seen"] = now
                old["misses"] = 0
                old.pop("guarded", None)
                if not old.get("active") and old.get("source") == "bot" and not old.get("manually_closed"):
                    old["active"] = True
                    old["date_updated"] = now
                    reopened += 1
                old.setdefault("ats_key", raw["key"])
                continue
            listings.append(new)
            by_key[new["ats_key"]] = new
            by_url[new["url"]] = new
            added.append(new)

        # close bot listings for this company that disappeared from the board
        closed += apply_misses(listings, c["name"], seen_keys, now, suspicious)

    print(f"companies: {len(companies)}  failed: {len(failed)}  new: {len(added)}  closed: {closed}  reopened: {reopened}")
    if failed:
        print("  unreachable:", ", ".join(sorted(failed)))
    if suspicious:
        print("  ⚠ mass-disappearance guard (not counting misses):", ", ".join(suspicious))
    for l in sorted(added, key=lambda x: x["company_name"]):
        print(f"  + {l['company_name']}: {l['title']}  [{l['category']} | {', '.join(l['terms'])} | {l['locations'][0]}]")

    if not a.dry_run:
        save_listings(listings)


if __name__ == "__main__":
    main()
