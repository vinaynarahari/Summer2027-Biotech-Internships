"""Discover which ATS (Greenhouse / Lever / Ashby / Workday) a company uses.

Usage:
    python -m scraper.discover "Company Name" ["Another Co" ...]
    python -m scraper.discover --file names.txt --merge

Tries common slug variants against each public job-board API and prints the
hits as companies.json entries. With --merge, appends new hits to companies.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .http import get_json, post_json

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "companies.json"

WD_HOSTS = ["wd1", "wd3", "wd5", "wd12", "wd103", "wd501"]
WD_SITES = ["External", "Careers", "careers", "external", "{t}", "{t}careers", "{T}Careers", "{t}_careers", "Search"]


def slug_variants(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    words = base.split()
    stop = {"inc", "the", "co", "corp", "llc", "ltd"}
    words = [w for w in words if w not in stop]
    out = ["".join(words), "-".join(words)]
    if len(words) > 1:
        out.append(words[0])
    return list(dict.fromkeys(v for v in out if v))


def try_greenhouse(slug: str):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}", quiet=True)
    if d and d.get("name"):
        return {"ats": "greenhouse", "slug": slug, "board_name": d["name"]}


def try_lever(slug: str):
    d = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1", quiet=True)
    if isinstance(d, list):
        return {"ats": "lever", "slug": slug}


def try_ashby(slug: str):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", quiet=True)
    if isinstance(d, dict) and "jobs" in d:
        return {"ats": "ashby", "slug": slug}


def try_workday(tenant: str):
    for host in WD_HOSTS:
        for site in WD_SITES:
            site = site.replace("{t}", tenant).replace("{T}", tenant.capitalize())
            url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
            d = post_json(url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, quiet=True)
            if isinstance(d, dict) and "jobPostings" in d:
                return {"ats": "workday", "tenant": tenant, "host": host, "site": site}


def discover(name: str, workday: bool = False) -> dict | None:
    for slug in slug_variants(name):
        for fn in (try_greenhouse, try_lever, try_ashby):
            hit = fn(slug)
            if hit:
                return {"name": name, **hit}
    if workday:
        for slug in slug_variants(name)[:1]:
            hit = try_workday(slug)
            if hit:
                return {"name": name, **hit}
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--workday", action="store_true", help="also brute-force Workday tenants (slow)")
    a = ap.parse_args()
    names = list(a.names)
    if a.file:
        names += [l.strip() for l in Path(a.file).read_text().splitlines() if l.strip() and not l.startswith("#")]

    with ThreadPoolExecutor(16) as ex:
        hits = list(ex.map(lambda n: discover(n, a.workday), names))

    found = [h for h in hits if h]
    missing = [n for n, h in zip(names, hits) if not h]
    print(json.dumps(found, indent=2))
    if missing:
        print(f"\n# not found ({len(missing)}): " + ", ".join(missing), file=sys.stderr)

    if a.merge and found:
        data = json.loads(COMPANIES.read_text())
        have = {c["name"].lower() for c in data["companies"]}
        for h in found:
            if h["name"].lower() not in have:
                h.pop("board_name", None)
                data["companies"].append(h)
        data["companies"].sort(key=lambda c: c["name"].lower())
        COMPANIES.write_text(json.dumps(data, indent=2) + "\n")
        print(f"# merged into {COMPANIES}", file=sys.stderr)


if __name__ == "__main__":
    main()
