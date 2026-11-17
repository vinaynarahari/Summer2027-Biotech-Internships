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

WD_HOSTS = ["wd1", "wd3", "wd5", "wd12", "wd103", "wd501", "wd115", "wd108", "wd10", "wd2", "wd8", "wd101"]
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


def try_smartrecruiters(slug: str):
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1", quiet=True)
    if isinstance(d, dict) and d.get("totalFound", 0) > 0:
        return {"ats": "smartrecruiters", "slug": slug}


def try_workable(slug: str):
    d = post_json(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
                  {"query": "", "location": [], "department": [], "worktype": [], "remote": []}, quiet=True)
    if isinstance(d, dict) and "results" in d:
        return {"ats": "workable", "slug": slug}


BIO_RE = re.compile(
    r"scien|biolog|chemi|assay|clinical|regulatory|\bgmp\b|\bqc\b|quality control|bioinformatic|"
    r"computational bio|genom|protein|antibod|cell|gene|crispr|molecular|pharma|formulation|cmc|"
    r"bioprocess|upstream|downstream|research associate|translational|toxicolog|immunolog|oncolog|"
    r"medical|lab|sequencing|diagnost|vector|mrna|biolog|drug|patient|trial",
    re.I,
)


def sample_titles(hit: dict) -> list[str]:
    from . import ats
    jobs = ats.fetch({"name": "?", **hit}) or []
    return [j["title"] for j in jobs[:60]]


def bio_score(titles: list[str]) -> float:
    return sum(bool(BIO_RE.search(t)) for t in titles) / len(titles) if titles else 0.0


def name_matches(name: str, board_name: str | None) -> bool:
    if not board_name:
        return True
    a = re.sub(r"[^a-z0-9]", "", name.lower())
    b = re.sub(r"[^a-z0-9]", "", board_name.lower())
    return a[:6] in b or b[:6] in a


def slug_variants_all(name: str) -> list[str]:
    base = slug_variants(name)
    words = [w for w in re.sub(r"[^A-Za-z0-9 ]", "", name).split() if w.lower() not in {"inc", "the", "co"}]
    camel = "".join(w[:1].upper() + w[1:] for w in words)
    return list(dict.fromkeys(base + [camel]))


def discover(name: str, workday: bool = False, strict: bool = False) -> dict | None:
    """strict=True: only accept full-name slugs, matching board names and bio-looking job titles."""
    variants = slug_variants_all(name)
    if strict:
        variants = [v for v in variants if len(v) >= 5 and (v.replace("-", "").lower() == re.sub(r"[^a-z0-9]", "", name.lower()) or "-" in v or v[0].isupper())]
    for slug in variants:
        for fn in (try_greenhouse, try_lever, try_ashby, try_workable, try_smartrecruiters):
            hit = fn(slug)
            if not hit:
                continue
            if strict:
                if not name_matches(name, hit.get("board_name")):
                    continue
                titles = sample_titles(hit)
                hit["_bio"] = round(bio_score(titles), 2)
                hit["_n"] = len(titles)
                hit["_sample"] = titles[:4]
                if titles and hit["_bio"] < 0.15:
                    continue
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
    ap.add_argument("--strict", action="store_true", help="verify board name + biotech-looking job titles")
    a = ap.parse_args()
    names = list(a.names)
    if a.file:
        names += [l.strip() for l in Path(a.file).read_text().splitlines() if l.strip() and not l.startswith("#")]

    with ThreadPoolExecutor(16) as ex:
        hits = list(ex.map(lambda n: discover(n, a.workday, a.strict), names))

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
                for k in ("board_name", "_bio", "_n", "_sample"):
                    h.pop(k, None)
                data["companies"].append(h)
        data["companies"].sort(key=lambda c: c["name"].lower())
        COMPANIES.write_text(json.dumps(data, indent=2) + "\n")
        print(f"# merged into {COMPANIES}", file=sys.stderr)


if __name__ == "__main__":
    main()
