from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LISTINGS = ROOT / ".github" / "scripts" / "listings.json"
COMPANIES = ROOT / "companies.json"


def load_listings() -> list[dict]:
    if not LISTINGS.exists():
        return []
    return json.loads(LISTINGS.read_text() or "[]")


def save_listings(listings: list[dict]) -> None:
    listings.sort(key=lambda l: (l.get("date_posted", 0), l.get("company_name", "")), reverse=True)
    LISTINGS.parent.mkdir(parents=True, exist_ok=True)
    LISTINGS.write_text(json.dumps(listings, indent=2, ensure_ascii=False) + "\n")
