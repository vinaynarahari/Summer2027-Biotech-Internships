"""Apply an approved issue-form submission.

Run by .github/workflows/contribution_approved.yml with the issue event in
$GITHUB_EVENT_PATH. Writes a commit message to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid

from .ats import now_utc
from .classify import categorize, clean_locations, detect_terms
from .discover import discover
from .store import COMPANIES, load_listings, save_listings


def parse_form(body: str) -> dict[str, str]:
    """GitHub issue forms render as '### Label\\n\\nvalue' blocks."""
    out = {}
    for block in re.split(r"^###\s+", body or "", flags=re.M)[1:]:
        label, _, value = block.partition("\n")
        value = value.strip()
        out[label.strip().lower()] = "" if value == "_No response_" else value
    return out


def field(form: dict, *prefixes: str) -> str:
    for p in prefixes:
        for k, v in form.items():
            if k.startswith(p.lower()):
                return v
    return ""


def set_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in kv.items())
    if path:
        with open(path, "a") as f:
            f.write(lines)
    print(lines, end="")


def fail(msg: str):
    set_output(error=msg.replace("\n", " "))
    sys.exit(1)


def new_internship(form: dict, user: str) -> str:
    listings = load_listings()
    url = field(form, "link to internship")
    if not url.startswith("http"):
        fail("The posting link must start with http(s)://")
    if any(l["url"] == url for l in listings):
        fail("This posting is already in the list.")
    title = field(form, "internship title")
    company = field(form, "company name")
    terms = [t.strip() for t in field(form, "term").split(",") if t.strip()] or detect_terms(title)
    degree = field(form, "degree")
    from .classify import is_us
    if not is_us(clean_locations(field(form, "location").split("|"))):
        fail("This list is U.S.-only: please include at least one U.S. location (e.g. 'Boston, MA' or 'Remote').")
    now = now_utc()
    listings.append(
        {
            "id": str(uuid.uuid4()),
            "source": user,
            "company_name": company,
            "company_url": "",
            "title": title,
            "category": field(form, "category") or categorize(title),
            "locations": clean_locations(field(form, "location").split("|")),
            "terms": terms,
            "url": url,
            "active": True,
            "is_visible": True,
            "sponsorship": field(form, "sponsorship") or "Other",
            "degrees": [] if not degree or degree.startswith("Undergrad") else [degree],
            "date_posted": now,
            "date_updated": now,
            "locked": True,
        }
    )
    save_listings(listings)
    return f"Add {company}: {title} (by @{user})"


def edit_internship(form: dict, user: str) -> str:
    listings = load_listings()
    url = field(form, "link to the internship")
    l = next((x for x in listings if x["url"] == url), None)
    if not l:
        fail("Couldn't find a listing with that exact URL.")
    action = field(form, "what should happen")
    now = now_utc()
    if action.startswith("Mark as closed"):
        l["active"], l["manually_closed"] = False, True
    elif action.startswith("Reopen"):
        l["active"], l["manually_closed"] = True, False
    elif action.startswith("Remove"):
        l["is_visible"] = False
    if t := field(form, "corrected title"):
        l["title"] = t
    if loc := field(form, "corrected location"):
        l["locations"] = clean_locations(loc.split("|"))
    if c := field(form, "corrected category"):
        l["category"] = c
    l["locked"] = True
    l["date_updated"] = now
    save_listings(listings)
    return f"Edit {l['company_name']}: {l['title']} — {action} (by @{user})"


ATS_URL_RES = [
    (r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", "greenhouse"),
    (r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([\w.-]+)", "ashby"),
]
WD_RE = re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")


def add_company(form: dict, user: str) -> str:
    name = field(form, "company name")
    url = field(form, "careers")
    data = json.loads(COMPANIES.read_text())
    if any(c["name"].lower() == name.lower() for c in data["companies"]):
        fail(f"{name} is already tracked.")
    entry = None
    if m := WD_RE.search(url):
        entry = {"name": name, "ats": "workday", "tenant": m.group(1), "host": m.group(2), "site": m.group(3)}
    for rx, ats in ATS_URL_RES:
        if not entry and (m := re.search(rx, url)):
            entry = {"name": name, "ats": ats, "slug": m.group(1)}
    if not entry:
        hit = discover(name, workday=False)
        if hit:
            hit.pop("board_name", None)
            entry = hit
    if not entry:
        fail(
            "Couldn't detect a Greenhouse / Lever / Ashby / Workday board from that URL or name. "
            "A maintainer can add it to companies.json by hand."
        )
    data["companies"].append(entry)
    data["companies"].sort(key=lambda c: c["name"].lower())
    COMPANIES.write_text(json.dumps(data, indent=2) + "\n")
    return f"Track {name} ({entry['ats']}) (requested by @{user})"


def main() -> None:
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    issue = event["issue"]
    labels = {l["name"] for l in issue.get("labels", [])}
    form = parse_form(issue.get("body", ""))
    user = issue["user"]["login"]
    if "new_internship" in labels:
        msg = new_internship(form, user)
    elif "edit_internship" in labels:
        msg = edit_internship(form, user)
    elif "add_company" in labels:
        msg = add_company(form, user)
    else:
        fail("Issue has no new_internship / edit_internship / add_company label.")
    set_output(commit_message=msg)


if __name__ == "__main__":
    main()
