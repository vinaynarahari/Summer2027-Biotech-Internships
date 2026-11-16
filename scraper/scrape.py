"""Poll every company's job board, add new internships, close vanished ones.

    python -m scraper.scrape                 # all companies
    python -m scraper.scrape --only Moderna  # one company (debugging)
    python -m scraper.scrape --dry-run       # print, don't write
"""

from __future__ import annotations

import argparse
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import classify as C
from .ats import fetch, now_utc
from .store import COMPANIES, load_listings, save_listings

MISSES_BEFORE_CLOSE = 2  # consecutive successful runs where the job is gone


def build_listing(company: dict, raw: dict, now: int) -> dict | None:
    title = raw["title"].strip()
    if not C.looks_like_internship(title, raw.get("is_intern_hint", False)):
        return None
    locations = C.clean_locations(raw.get("locations", []))
    if not C.is_us(locations, raw.get("country") or ""):
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

    added, reopened, closed, failed = [], 0, 0, []
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
        prefix_company = c["name"]
        for l in listings:
            if (
                l.get("source") == "bot"
                and l.get("company_name") == prefix_company
                and l.get("active")
                and l.get("ats_key") not in seen_keys
            ):
                l["misses"] = l.get("misses", 0) + 1
                if l["misses"] >= MISSES_BEFORE_CLOSE:
                    l["active"] = False
                    l["date_updated"] = now
                    closed += 1

    print(f"companies: {len(companies)}  failed: {len(failed)}  new: {len(added)}  closed: {closed}  reopened: {reopened}")
    if failed:
        print("  unreachable:", ", ".join(sorted(failed)))
    for l in sorted(added, key=lambda x: x["company_name"]):
        print(f"  + {l['company_name']}: {l['title']}  [{l['category']} | {', '.join(l['terms'])} | {l['locations'][0]}]")

    if not a.dry_run:
        save_listings(listings)


if __name__ == "__main__":
    main()
