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
from .classify import CATEGORY_EMOJI, DISPLAY_ORDER, SUMMER_YEAR, is_off_season, states_of
from .store import COMPANIES, ROOT, load_listings

REPO = os.environ.get("GITHUB_REPOSITORY", "OWNER/Biotech-Internships")
OWNER, NAME = REPO.split("/", 1)
SITE_URL = os.environ.get("SITE_URL", f"https://{OWNER.lower()}.github.io/{NAME}/")
REPO_URL = f"https://github.com/{REPO}"
TOP_ANCHOR = f"#summer-{SUMMER_YEAR}-biotech--life-science-internships-us"
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


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


NO_RESTRICTION = ("Other", "Offers Sponsorship")


def write_filters(active: list[dict], top: set[str], now: int, stamp: str) -> str:
    """Pre-filtered pages under filters/ (GitHub can't run JS, so filters are pages).
    Returns the markdown nav block for the README."""
    fdir = ROOT / "filters"
    if fdir.exists():
        shutil.rmtree(fdir)
    (fdir / "state").mkdir(parents=True)
    (fdir / "category").mkdir()
    (fdir / "company").mkdir()

    def page(path, title, rows, blurb=""):
        rows = sort_rows(rows)
        depth = "../" * (len(path.relative_to(ROOT).parts) - 1)
        md = (
            f"# {title}\n\n"
            f"[← All internships]({depth}README.md) · [All filters]({depth}filters/README.md) · "
            f"🔎 [Search & combine filters on the website]({SITE_URL})\n\n"
            f"**{len(rows)}** open role{'s' if len(rows) != 1 else ''} · updated {stamp}"
            + (f"\n\n{blurb}" if blurb else "")
            + "\n\n"
            + (table(rows, top, now, show_term=True) if rows else "_Nothing open right now — check back soon._")
            + "\n"
        )
        path.write_text(md)

    week = [l for l in active if now - l["date_posted"] < 7 * 86400]
    unrestricted = [l for l in active if l.get("sponsorship", "Other") in NO_RESTRICTION]
    tops = [l for l in active if l["company_name"] in top]
    page(fdir / "new-this-week.md", "🆕 Posted in the last 7 days", week)
    page(fdir / "top-companies.md", "🔥 Top companies", tops)
    page(fdir / "no-visa-restrictions.md", "🌐 No 🛂 / 🇺🇸 restriction stated", unrestricted,
         "_Roles whose posting doesn't say \"no sponsorship\" or \"U.S. citizens only\". Always double-check the posting._")

    by_state: dict[str, list] = {}
    for l in active:
        for st in l["_states"]:
            by_state.setdefault(st, []).append(l)
    states = sorted(by_state.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for st, rows in states:
        page(fdir / "state" / f"{slugify(st)}.md", f"📍 Internships in {st}", rows)

    by_cat = {c: [l for l in active if l["category"] == c] for c in DISPLAY_ORDER}
    for c, rows in by_cat.items():
        page(fdir / "category" / f"{slugify(c)}.md", f"{CATEGORY_EMOJI[c]} {c}", rows,
             "_Includes co-op / off-season roles (see the Term column)._")

    by_co: dict[str, list] = {}
    for l in active:
        by_co.setdefault(l["company_name"], []).append(l)
    for co, rows in by_co.items():
        page(fdir / "company" / f"{slugify(co)}.md", f"🏢 {co}", rows)

    def link(text, path, n):
        return f"[{text}]({path}) ({n})"

    state_links = " · ".join(link(st, f"filters/state/{slugify(st)}.md", len(r)) for st, r in states)
    cat_links = " · ".join(link(f"{CATEGORY_EMOJI[c]} {c}", f"filters/category/{slugify(c)}.md", len(r)) for c, r in by_cat.items())
    quick = " · ".join([
        link("🆕 New this week", "filters/new-this-week.md", len(week)),
        link("🔥 Top companies", "filters/top-companies.md", len(tops)),
        link("🌐 No visa restriction stated", "filters/no-visa-restrictions.md", len(unrestricted)),
        link("🌍 Co-op / off-season", "README-Off-Season.md", sum(1 for l in active if is_off_season(l["terms"]))),
    ])
    co_links = " · ".join(link(co, f"filters/company/{slugify(co)}.md", len(r))
                          for co, r in sorted(by_co.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())))

    # filters index page
    idx = (
        "# Filters\n\n[← All internships](../README.md) · 🔎 "
        f"[Search & combine filters on the website]({SITE_URL})\n\n"
        f"Every page below is regenerated with the list (updated {stamp}).\n\n"
        f"## Quick filters\n\n{quick.replace('filters/', '').replace('README-Off', '../README-Off')}\n\n"
        f"## By state\n\n{state_links.replace('filters/', '')}\n\n"
        f"## By category\n\n{cat_links.replace('filters/', '')}\n\n"
        f"## By company\n\n{co_links.replace('filters/', '')}\n"
    )
    (fdir / "README.md").write_text(idx)

    top_states = " · ".join(link(st, f"filters/state/{slugify(st)}.md", len(r)) for st, r in states[:12])
    return (
        "### 🔎 Filter\n\n"
        f"**Quick:** {quick}\n\n"
        f"**State:** {top_states} · [all states & companies →](filters/README.md)\n\n"
        f"**Want to combine filters (state + category + keyword)?** Use the [website]({SITE_URL}).\n\n"
    )


def main() -> None:
    now = now_utc()
    listings = visible(load_listings())
    companies = json.loads(COMPANIES.read_text())["companies"]
    top = {c["name"] for c in companies if c.get("top")}

    for l in listings:
        l["_states"] = states_of(l["locations"])
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
    filters_nav = write_filters(active, top, now, stamp)
    # Same numbers the website shows in its header (open roles / companies / last update)
    header = (
        f"> **{len(active)} open roles** at **{n_co} companies**: {total} for Summer {SUMMER_YEAR} (below) + "
        f"{len(off)} co-op / off-season ([Off-Season README](./README-Off-Season.md)) · "
        f"last updated **{stamp}**\n\n"
        + filters_nav
        + f"---\n\n### Browse {total} Summer {SUMMER_YEAR} Internship Roles by Category\n\n{nav}\n\n---\n\n"
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
                               "sponsorship", "degrees", "date_posted", "date_updated")}
        | {"top": l["company_name"] in top, "states": l["_states"]}
        for l in listings
        if l.get("active") or now - l.get("date_updated", 0) < 30 * 86400
    ]
    (site / "listings.json").write_text(json.dumps({"updated": now, "listings": slim}, ensure_ascii=False))
    write_feed(active, now)
    shutil.copy(COMPANIES, site / "companies.json")
    print(f"README: {len(active)} open = {total} summer + {len(off)} off-season; {len(inactive)} inactive")


if __name__ == "__main__":
    main()
