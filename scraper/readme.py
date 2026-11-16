"""Render README.md / README-Off-Season.md / README-Inactive.md and site data
from .github/scripts/listings.json.

    python -m scraper.readme
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime

from .ats import now_utc
from .classify import CATEGORY_EMOJI, DISPLAY_ORDER, SUMMER_YEAR, is_off_season
from .store import COMPANIES, ROOT, load_listings

REPO = os.environ.get("GITHUB_REPOSITORY", "OWNER/Biotech-Internships")
OWNER, NAME = REPO.split("/", 1)
SITE_URL = os.environ.get("SITE_URL", f"https://{OWNER.lower()}.github.io/{NAME}/")
REPO_URL = f"https://github.com/{REPO}"
TOP_ANCHOR = f"#summer-{SUMMER_YEAR}-biotech--life-science-internships"
APPLY_IMG = "https://img.shields.io/badge/Apply-0f766e?style=for-the-badge"

START, END = "<!-- TABLE_START -->", "<!-- TABLE_END -->"


def anchor(text: str) -> str:
    a = re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")  # GitHub keeps the emoji's '-'
    return "#" + a


def age(ts: int, now: int) -> str:
    d = max(0, (now - ts) // 86400)
    if d < 1:
        return "0d"
    if d < 30:
        return f"{d}d"
    return f"{d // 30}mo"


def company_flags(top: set[str], l: dict) -> str:
    return " 🔥" if l["company_name"] in top else ""


def role_flags(l: dict) -> str:
    f = ""
    if l.get("sponsorship") == "Does Not Offer Sponsorship":
        f += " 🛂"
    elif l.get("sponsorship") == "U.S. Citizenship is Required":
        f += " 🇺🇸"
    if l.get("degrees"):
        f += " 🎓"
    return f


def locations_cell(locs: list[str]) -> str:
    locs = [html.escape(x) for x in locs]
    if len(locs) <= 3:
        return "<br>".join(locs)
    return f"<details><summary><strong>{len(locs)} locations</strong></summary>{'<br>'.join(locs)}</details>"


def table(rows: list[dict], top: set[str], now: int, show_term: bool = False, show_apply: bool = True) -> str:
    head = ["Company", "Role", "Location"] + (["Term"] if show_term else []) + (["Application"] if show_apply else []) + ["Age"]
    out = ["<table>", "<thead>", "<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>", "</thead>", "<tbody>"]
    prev = None
    for l in rows:
        name = html.escape(l["company_name"])
        if l.get("company_url"):
            name = f'<a href="{html.escape(l["company_url"])}">{name}</a>'
        company = "↳" if l["company_name"] == prev else f"<strong>{name}</strong>{company_flags(top, l)}"
        prev = l["company_name"]
        cells = [company, html.escape(l["title"]) + role_flags(l), locations_cell(l["locations"])]
        if show_term:
            cells.append(", ".join(l["terms"]))
        if show_apply:
            cells.append(
                f'<div align="center"><a href="{html.escape(l["url"])}"><img src="{APPLY_IMG}" width="84" alt="Apply"></a></div>'
                if l.get("active")
                else "🔒"
            )
        cells.append(age(l["date_posted"], now))
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    out += ["</tbody>", "</table>"]
    return "\n".join(out)


def sort_rows(rows: list[dict]) -> list[dict]:
    # newest first, but keep a company's roles from the same day grouped (for the ↳ arrows)
    return sorted(rows, key=lambda l: (-(l["date_posted"] // 86400), l["company_name"].lower(), l["title"].lower()))


def visible(listings):
    return [l for l in listings if l.get("is_visible", True)]


def sections(rows, top, now, show_term=False):
    by_cat = {c: [] for c in DISPLAY_ORDER}
    for l in rows:
        by_cat.setdefault(l["category"], []).append(l)
    parts = []
    for cat in DISPLAY_ORDER:
        items = sort_rows(by_cat[cat])
        parts.append(f"## {CATEGORY_EMOJI[cat]} {cat} Internship Roles\n\n[Back to top]({TOP_ANCHOR})\n")
        parts.append(table(items, top, now, show_term) if items else "_No open roles right now — check back soon._")
        parts.append("")
    return "\n".join(parts), {c: len(by_cat[c]) for c in DISPLAY_ORDER}


def inject(path, body: str) -> None:
    text = path.read_text()
    text = re.sub(r"https://[\w.-]+\.github\.io/[\w.-]+/", SITE_URL, text)
    a, b = text.index(START) + len(START), text.index(END)
    path.write_text(text[:a] + "\n" + body + "\n" + text[b:])


def write_feed(rows: list[dict], now: int) -> None:
    items = []
    for l in sorted(rows, key=lambda x: -x["date_posted"])[:150]:
        pub = format_datetime(datetime.fromtimestamp(l["date_posted"], timezone.utc))
        desc = f"{l['category']} · {', '.join(l['locations'][:3])} · {', '.join(l['terms'])}"
        items.append(
            "<item>"
            f"<title>{html.escape(l['company_name'] + ' — ' + l['title'])}</title>"
            f"<link>{html.escape(l['url'])}</link>"
            f"<guid isPermaLink=\"false\">{l['id']}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<category>{html.escape(l['category'])}</category>"
            f"<description>{html.escape(desc)}</description>"
            "</item>"
        )
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>Biotech Internships — new roles</title><link>{SITE_URL}</link>"
        "<description>Newly posted biotech, pharma &amp; life-science internships</description>"
        f"<lastBuildDate>{format_datetime(datetime.fromtimestamp(now, timezone.utc))}</lastBuildDate>"
        + "".join(items)
        + "</channel></rss>\n"
    )
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "feed.xml").write_text(feed)


def main() -> None:
    now = now_utc()
    listings = visible(load_listings())
    companies = json.loads(COMPANIES.read_text())["companies"]
    top = {c["name"] for c in companies if c.get("top")}

    active = [l for l in listings if l.get("active")]
    summer = [l for l in active if not is_off_season(l["terms"])]
    off = [l for l in active if is_off_season(l["terms"])]
    inactive = sorted([l for l in listings if not l.get("active")], key=lambda l: -l.get("date_updated", 0))[:1500]

    body, counts = sections(summer, top, now)
    total = sum(counts.values())
    nav = "\n\n".join(
        f"{CATEGORY_EMOJI[c]} **[{c}]({anchor(CATEGORY_EMOJI[c] + ' ' + c + ' Internship Roles')})** ({counts[c]})"
        for c in DISPLAY_ORDER
    )
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    n_co = len({l["company_name"] for l in active})
    header = (
        f"### Browse {total} Summer {SUMMER_YEAR} Internship Roles by Category\n\n{nav}\n\n"
        f"> 🌍 Plus **{len(off)}** co-op / off-cycle roles in [Off-Season](./README-Off-Season.md) · "
        f"**{n_co}** companies hiring · last updated **{stamp}**\n\n---\n\n"
    )
    inject(ROOT / "README.md", header + body)

    off_body, off_counts = sections(off, top, now, show_term=True)
    inject(ROOT / "README-Off-Season.md", f"### {sum(off_counts.values())} co-op / off-cycle roles · updated {stamp}\n\n" + off_body)

    inject(
        ROOT / "README-Inactive.md",
        f"### {len(inactive)} most recently closed roles · updated {stamp}\n\n"
        + table(sort_rows(inactive), top, now, show_term=True),
    )

    # site data + RSS
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    slim = [
        {k: l.get(k) for k in ("id", "company_name", "title", "category", "locations", "terms", "url", "active",
                               "sponsorship", "degrees", "date_posted", "date_updated", "country")}
        | {"top": l["company_name"] in top}
        for l in listings
        if l.get("active") or now - l.get("date_updated", 0) < 30 * 86400
    ]
    (site / "listings.json").write_text(json.dumps({"updated": now, "listings": slim}, ensure_ascii=False))
    write_feed(active, now)
    shutil.copy(COMPANIES, site / "companies.json")
    print(f"README: {total} summer, {len(off)} off-season, {len(inactive)} inactive")


if __name__ == "__main__":
    main()
