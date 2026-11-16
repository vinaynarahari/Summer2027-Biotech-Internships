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

from .classify import looks_like_internship
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
WD_SEARCHES = ["intern", "internship", "co-op", "praktikum"]
WD_PAGE = 20
WD_MAX_PAGES = 15


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


def workday(c: dict) -> list[dict] | None:
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    found: dict[str, dict] = {}
    reached = False
    for term in WD_SEARCHES:
        dry_pages = 0
        for page in range(WD_MAX_PAGES):
            d = post_json(
                f"{api}/jobs",
                {"appliedFacets": {}, "limit": WD_PAGE, "offset": page * WD_PAGE, "searchText": term},
            )
            if not isinstance(d, dict) or "jobPostings" not in d:
                if reached:
                    # partial fetch: report failure so live roles aren't wrongly closed
                    return None
                break
            reached = True
            posts = d["jobPostings"]
            hits = 0
            for p in posts:
                if "externalPath" not in p:
                    continue
                if looks_like_internship(p.get("title", "")):
                    hits += 1
                    found.setdefault(p["externalPath"], p)
            # Workday search is fuzzy & relevance-ordered: once pages stop
            # containing internship titles, the rest is noise.
            dry_pages = 0 if hits else dry_pages + 1
            if len(posts) < WD_PAGE or dry_pages >= 2:
                break
    if not reached:
        return None

    def detail(item):
        path, p = item
        info = (get_json(f"{api}{path}", quiet=True) or {}).get("jobPostingInfo") or {}
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

    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(detail, found.items()))


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
