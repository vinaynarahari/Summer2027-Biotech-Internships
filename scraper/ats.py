"""Fetchers for public ATS job-board APIs.

Each fetcher returns a list of RawJob dicts (or None if the board could not be
reached, so the caller knows not to mark that company's listings as closed):

    {key, title, url, locations[], posted (unix|None), description (plain text),
     is_intern_hint (bool: the ATS itself says this is an internship)}
"""

from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .classify import location_clearly_non_us, looks_like_internship
from .http import get_json, post_json

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", s)).strip()


def iso_to_unix(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Greenhouse
# --------------------------------------------------------------------------- #
def greenhouse(c: dict) -> list[dict] | None:
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    if d is None or "jobs" not in d:
        return None
    out = []
    for j in d["jobs"]:
        locs = [j.get("location", {}).get("name") or ""]
        offices = [o.get("location") or o.get("name") for o in j.get("offices", []) if o]
        if len(offices) > 1:
            locs = offices
        out.append(
            {
                "key": f"gh:{c['slug']}:{j['id']}",
                "title": j["title"],
                "url": j["absolute_url"],
                "locations": [l for l in locs if l],
                "posted": iso_to_unix(j.get("first_published") or j.get("updated_at")),
                "description": strip_html(j.get("content")),
                "is_intern_hint": False,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #
def lever(c: dict) -> list[dict] | None:
    d = get_json(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    if not isinstance(d, list):
        return None
    out = []
    for j in d:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location")]
        if j.get("workplaceType") == "remote":
            locs = list(locs) + ["Remote"]
        desc = " ".join(
            [j.get("descriptionPlain") or strip_html(j.get("description")), j.get("additionalPlain") or ""]
            + [strip_html(x.get("content")) for x in j.get("lists", [])]
        )
        out.append(
            {
                "key": f"lv:{c['slug']}:{j['id']}",
                "title": j["text"],
                "url": j["hostedUrl"],
                "locations": [l for l in locs if l],
                "posted": int(j["createdAt"] / 1000) if j.get("createdAt") else None,
                "description": desc,
                "is_intern_hint": "intern" in (cat.get("commitment") or "").lower(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Ashby
# --------------------------------------------------------------------------- #
def ashby(c: dict) -> list[dict] | None:
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}?includeCompensation=false")
    if not isinstance(d, dict) or "jobs" not in d:
        return None
    out = []
    for j in d["jobs"]:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        if j.get("isRemote"):
            locs.append("Remote")
        out.append(
            {
                "key": f"ab:{c['slug']}:{j['id']}",
                "title": j["title"],
                "url": j["jobUrl"],
                "locations": [l for l in locs if l],
                "posted": iso_to_unix(j.get("publishedAt")),
                "description": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                "is_intern_hint": (j.get("employmentType") or "").lower() == "intern",
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Workday (public "cxs" JSON API used by every *.myworkdayjobs.com site)
# --------------------------------------------------------------------------- #
WD_SEARCHES = ["summer intern", "internship", "intern", "co-op"]
WD_PAGE = 20
WD_MAX_PAGES = 30
WD_INTERN_FACET_RE = re.compile(r"\binterns?\b|co-?op|student|college|campus|early career|university|trainee|placement", re.I)
WD_COUNTRY_PARAMS = re.compile(r"country", re.I)


def _wd_posted(text: str | None) -> int | None:
    if not text:
        return None
    now = time.time()
    t = text.lower()
    if "today" in t:
        return int(now)
    if "yesterday" in t:
        return int(now - 86400)
    m = re.search(r"(\d+)\+?\s+days?", t)
    if m:
        return int(now - int(m.group(1)) * 86400)
    return None


def _wd_walk_facets(facets, out=None):
    """Flatten Workday's (sometimes nested) facet tree into (param, value) pairs."""
    out = [] if out is None else out
    for f in facets or []:
        param = f.get("facetParameter")
        for v in f.get("values") or []:
            if "values" in v and v.get("facetParameter"):
                _wd_walk_facets([v], out)
            elif param and v.get("id"):
                out.append((param, v))
    return out


class _Partial(Exception):
    pass


def workday(c: dict) -> list[dict] | None:
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"

    def page(facets: dict, text: str, offset: int):
        return post_json(f"{api}/jobs", {"appliedFacets": facets, "limit": WD_PAGE, "offset": offset, "searchText": text})

    first = page({}, "", 0)
    if not isinstance(first, dict) or "jobPostings" not in first:
        return None

    # 1. Restrict to the U.S. when the board exposes a country facet (most do).
    pairs = _wd_walk_facets(first.get("facets"))
    us = {}
    for param, v in pairs:
        if WD_COUNTRY_PARAMS.search(param) and v.get("descriptor") in ("United States of America", "United States", "USA"):
            us = {param: [v["id"]]}
            break
    # 2. Use the board's own intern / co-op worker-type tags when it has them.
    intern_facets: dict[str, list[str]] = {}
    for param, v in pairs:
        if WD_COUNTRY_PARAMS.search(param) or "location" in param.lower():
            continue
        if WD_INTERN_FACET_RE.search(v.get("descriptor", "")):
            intern_facets.setdefault(param, []).append(v["id"])

    found: dict[str, dict] = {}

    def collect(facets: dict, text: str, stop_when_dry: bool):
        dry = 0
        total = None
        for i in range(WD_MAX_PAGES):
            d = page(facets, text, i * WD_PAGE)
            if not isinstance(d, dict) or "jobPostings" not in d:
                raise _Partial
            if total is None:
                total = d.get("total") or 0  # Workday only reports total on the first page
            posts = d["jobPostings"]
            hits = 0
            for p in posts:
                if "externalPath" not in p or not looks_like_internship(p.get("title", "")):
                    continue
                hits += 1
                # skip roles whose (single) listed location is clearly outside the U.S.
                lt = p.get("locationsText") or ""
                if not us and lt and not re.match(r"^\d+ Locations$", lt) and location_clearly_non_us(lt):
                    continue
                found.setdefault(p["externalPath"], p)
            dry = 0 if hits else dry + 1
            if len(posts) < WD_PAGE or (i + 1) * WD_PAGE >= total or (stop_when_dry and dry >= 3):
                break

    try:
        for param, ids in intern_facets.items():
            collect({**us, param: ids}, "", stop_when_dry=False)
        for term in WD_SEARCHES:
            # Workday text search is fuzzy (can match every job): stop once results go dry
            collect(us, term, stop_when_dry=True)
    except _Partial:
        return None  # partial fetch: report failure so live roles aren't wrongly closed

    def detail(item):
        path, p = item
        info = (get_json(f"{api}{path}", quiet=True) or {}).get("jobPostingInfo")
        if not info:
            raise _Partial  # keys/urls come from the detail call; don't guess
        locs = [info.get("location") or p.get("locationsText")] + list(info.get("additionalLocations") or [])
        country = (info.get("country") or {}).get("descriptor", "")
        if (country and country != "United States of America" and locs[0] and country not in locs[0]
                and "-" not in locs[0]):
            locs[0] = f"{locs[0]}, {country}"
        return {
            "key": f"wd:{c['tenant']}:{info.get('jobReqId') or path}",
            "title": p["title"],
            "url": info.get("externalUrl") or f"{base}/{c['site']}{path}",
            "locations": [l for l in locs if l and not re.match(r"^\d+ Locations$", l)],
            "posted": iso_to_unix(info.get("startDate")) or _wd_posted(p.get("postedOn")),
            "description": strip_html(info.get("jobDescription")),
            "is_intern_hint": False,
            "country": "United States" if country == "United States of America" else country,
        }

    try:
        with ThreadPoolExecutor(6) as ex:
            return list(ex.map(detail, found.items()))
    except _Partial:
        return None


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday}


def fetch(company: dict) -> list[dict] | None:
    fn = FETCHERS.get(company["ats"])
    if not fn:
        return None
    try:
        return fn(company)
    except Exception as e:  # noqa: BLE001 - one bad board must not kill the run
        print(f"  ! {company['name']}: {type(e).__name__}: {e}")
        return None


def now_utc() -> int:
    return int(datetime.now(timezone.utc).timestamp())
