# Contributing

Thanks for helping make this the most complete list of biotech internships! There are three ways to help, all via issue forms. **No code needed.**

| I want to… | Use |
|---|---|
| Add a role the bot missed | [New Internship](../../issues/new?template=new_internship.yaml) |
| Fix / close a role | [Edit Internship](../../issues/new?template=edit_internship.yaml) |
| Get a whole company tracked | [Add a Company](../../issues/new?template=add_company.yaml) |

When a maintainer adds the **`approved`** label, a GitHub Action applies your change to `.github/scripts/listings.json` (or `companies.json`), regenerates the READMEs and website, and closes the issue.

## What counts as "biotech"?

Internships at companies or institutes working in biotechnology, pharma, life-science tools, diagnostics, genomics, synthetic biology, bio-manufacturing, ag-bio / food-bio, medtech, or bio-focused AI. **Any function counts** (lab, data, engineering, clinical, business), as long as the employer is in the life-sciences space.

## Rules

- **U.S. only**: the role must be located in the U.S. (or U.S.-remote).
- The posting must be **live** and link directly to the application (not a generic careers page).
- One issue per role. Use `|` to separate multiple locations.
- Don't submit roles for terms that have already passed.

## For maintainers

```sh
python -m scraper.scrape            # poll all boards → listings.json
python -m scraper.readme            # regenerate READMEs + site/ data
python -m scraper.discover "Acme Bio" --merge   # find a company's ATS and add it
python -m scraper.discover "Acme Bio" --workday # also try Workday tenants
```

- `companies.json` entries: `{"name", "ats": greenhouse|lever|ashby|workday, "slug"}`; Workday uses `"tenant", "host", "site"` (from `https://{tenant}.{host}.myworkdayjobs.com/{site}`). `"top": true` adds 🔥.
- Set `"locked": true` on a listing to stop the bot reclassifying it; `"is_visible": false` hides it.
- Each season, bump `SUMMER_YEAR` in `scraper/classify.py` and archive the old README.
