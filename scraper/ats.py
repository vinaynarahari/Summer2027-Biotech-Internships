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
from .http import get_json, get_json_browser, get_text, post_json
from urllib.parse import quote

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


# --------------------------------------------------------------------------- #
# SmartRecruiters (public postings API)
# --------------------------------------------------------------------------- #
def smartrecruiters(c: dict) -> list[dict] | None:
    out: dict[str, dict] = {}
    reached = False
    for q in ("intern", "co-op", "internship"):
        for offset in range(0, 1000, 100):
            d = get_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings?limit=100&offset={offset}&q={quote(q)}")
            if not isinstance(d, dict) or "content" not in d:
                if reached:
                    return None
                break
            reached = True
            for j in d["content"]:
                loc = j.get("location") or {}
                parts = [loc.get("city"), loc.get("region"), loc.get("country", "").upper() if loc.get("country") else None]
                locs = [", ".join(x for x in parts if x)]
                if loc.get("remote"):
                    locs.append("Remote")
                country = "United States" if (loc.get("country") or "").lower() == "us" else (loc.get("country") or "").upper()
                out.setdefault(j["id"], {
                    "key": f"sr:{c['slug']}:{j['id']}",
                    "title": j["name"],
                    "url": f"https://jobs.smartrecruiters.com/{c['slug']}/{j['id']}",
                    "locations": [l for l in locs if l],
                    "posted": iso_to_unix(j.get("releasedDate")),
                    "description": "",
                    "is_intern_hint": "intern" in ((j.get("typeOfEmployment") or {}).get("label") or "").lower(),
                    "country": country,
                })
            if offset + 100 >= d.get("totalFound", 0):
                break
    return list(out.values()) if reached else None


# --------------------------------------------------------------------------- #
# Workable (apply.workable.com public API)
# --------------------------------------------------------------------------- #
def workable(c: dict) -> list[dict] | None:
    out, token, reached = [], None, False
    for _ in range(30):
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        d = post_json(f"https://apply.workable.com/api/v3/accounts/{c['slug']}/jobs", body)
        if not isinstance(d, dict) or "results" not in d:
            if reached:
                return None
            break
        reached = True
        for j in d["results"]:
            locs = []
            for l in j.get("locations") or [j.get("location") or {}]:
                locs.append(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x))
            if j.get("remote"):
                locs.append("Remote")
            cc = ((j.get("location") or {}).get("countryCode") or "").upper()
            out.append({
                "key": f"wk:{c['slug']}:{j['shortcode']}",
                "title": j["title"],
                "url": f"https://apply.workable.com/{c['slug']}/j/{j['shortcode']}/",
                "locations": [l for l in locs if l],
                "posted": iso_to_unix(j.get("published")),
                "description": "",
                "is_intern_hint": (j.get("type") or "").lower() == "internship",
                "country": "United States" if cc == "US" else (j.get("location") or {}).get("country", ""),
            })
        token = d.get("nextPage")
        if not token:
            break
    return out if reached else None


# --------------------------------------------------------------------------- #
# Radancy / TalentBrew career sites (e.g. jobs.takeda.com)
# --------------------------------------------------------------------------- #
RADANCY_ITEM = re.compile(
    r'<a[^>]+href="(/job/[^"]+)"[^>]*data-job-id="(\d+)"[^>]*>(.*?)</a>', re.S
)


def radancy(c: dict) -> list[dict] | None:
    host = c["host"]
    found: dict[str, dict] = {}
    reached = False
    for kw in c.get("keywords", ["intern", "internship", "co-op"]):
        for pg in range(1, 30):
            url = (
                f"https://{host}/search-jobs/results?ActiveFacetID=0&CurrentPage={pg}&RecordsPerPage=100"
                f"&Distance=50&RadiusUnitType=0&Keywords={quote(kw)}&Location=&ShowRadius=False&IsPagination=False"
                "&FacetType=0&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters"
                "&SortCriteria=0&SortDirection=0&SearchType=5"
            )
            d = get_json_browser(url, headers={"X-Requested-With": "XMLHttpRequest"})
            if not isinstance(d, dict) or "results" not in d:
                if reached:
                    return None
                break
            reached = True
            html_ = d["results"] or ""
            items = RADANCY_ITEM.findall(html_)
            for href, jid, inner in items:
                title = strip_html((re.search(r'<h2[^>]*>(.*?)</h2>', inner, re.S) or [None, ""])[1])
                loc = strip_html((re.search(r'class="(?:job-)?location"[^>]*>(.*?)</span>', inner, re.S) or [None, ""])[1])
                found.setdefault(jid, {
                    "key": f"rd:{host}:{jid}",
                    "title": title,
                    "url": f"https://{host}{href}",
                    "locations": [loc] if loc else [],
                    "posted": None,
                    "description": "",
                    "is_intern_hint": False,
                })
            total = int((re.search(r'data-total-results="(\d+)"', html_) or [0, 0])[1])
            if not items or pg * 100 >= total:
                break
    return list(found.values()) if reached else None


# --------------------------------------------------------------------------- #
# Attrax career sites (e.g. careers.abbvie.com): server-rendered tiles
# --------------------------------------------------------------------------- #
ATTRAX_TILE = re.compile(r'<div class="attrax-vacancy-tile ([^"]*)" data-jobid="(\d+)">(.*?)(?=<div class="attrax-vacancy-tile |\Z)', re.S)


def attrax(c: dict) -> list[dict] | None:
    host = c["host"]
    found: dict[str, dict] = {}
    reached = False
    for kw in c.get("keywords", ["intern", "internship", "co-op", "2027", "undergraduate", "summer"]):
        for pg in range(1, 25):
            h = get_text(f"https://{host}/en/jobs?q={quote(kw)}&page={pg}")
            if h is None:
                if reached:
                    return None
                break
            reached = True
            tiles = ATTRAX_TILE.findall(h)
            new = 0
            for classes, jid, body in tiles:
                if jid in found:
                    continue
                new += 1
                a = re.search(r'<a[^>]+class="attrax-vacancy-tile__title[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
                if not a:
                    continue
                loc = re.search(r'attrax-vacancy-tile__location-freetext.*?__item-value">\s*(.*?)\s*</p>', body, re.S)
                us = "attrax-vacancy-tile--united-states" in f"attrax-vacancy-tile--x {classes}" or "--united-states" in classes
                found[jid] = {
                    "key": f"ax:{host}:{jid}",
                    "title": strip_html(a.group(2)),
                    "url": f"https://{host}{a.group(1)}",
                    "locations": [strip_html(loc.group(1))] if loc else [],
                    "posted": None,
                    "description": "",
                    "is_intern_hint": False,
                    # the tile's CSS classes carry the country ("Madrid, MD" is Spain, not Maryland)
                    "country": "United States" if us else "Non-US",
                }
            if not new:
                break
    return list(found.values()) if reached else None


FETCHERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday,
    "smartrecruiters": smartrecruiters, "workable": workable, "radancy": radancy, "attrax": attrax,
}


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
