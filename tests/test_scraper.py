"""Regression tests for bugs we've actually hit. Run: python -m unittest -v"""

import unittest
from unittest import mock

from scraper import ats
from scraper import classify as C
from scraper.scrape import apply_misses, build_listing


class InternshipDetection(unittest.TestCase):
    # Real titles friends applied to (J&J, Regeneron) — must all be caught.
    TITLES = [
        "Immunology Medical Affairs Clinical Project Scientist Internship",
        "Oncology Preclinical Safety & TS Intern",
        "Oncology CPP Scientist Intern",
        "2027 GD (U.S.) Summer Intern",
        "Med Affairs Autoantibody Summer Intern",
        "Development Engineer (GTO/SC) Summer Intern",
        "2027 Summer Intern Process Validation & Technology Transfer (IOPS)",
        "2027 Summer Intern R&D Program, Portfolio & Business Operations",
        "Praktikum Maschinenbau – 6 Monate | Start Frühjahr 2027",
        "Software Engineering Co-Op",
    ]

    def test_screenshot_titles_detected(self):
        for t in self.TITLES:
            self.assertTrue(C.looks_like_internship(t), t)

    def test_not_internships(self):
        for t in ["Internal Medicine Specialist", "International Assignee", "Postdoctoral Scientist", "Internship Program Manager"]:
            self.assertFalse(C.looks_like_internship(t), t)

    def test_categories(self):
        self.assertEqual(C.categorize("Oncology Global Medical Safety Intern"), "Clinical, Regulatory & Medical Affairs")
        self.assertEqual(C.categorize("Oncology Preclinical Safety & TS Intern"), "Research & Lab Science")
        self.assertEqual(C.categorize("Industrial Engineering Co-op"), "Engineering, Manufacturing & Quality")  # not 'trial'
        self.assertEqual(C.categorize("Commercial Excellence Support Intern"), "Business, Commercial & Operations")  # not 'cell'

    def test_terms(self):
        self.assertEqual(C.detect_terms("Summer Intern 2027, Pathobiology"), ["Summer 2027"])
        self.assertEqual(C.detect_terms("Spring 2027 Co-Op, Protein Sciences"), ["Spring 2027"])
        self.assertTrue(C.is_stale(C.detect_terms("2026 Summer Intern")))
        # a stray year in the description must not invent a season
        self.assertEqual(C.detect_terms("Lab Intern", "fall within scope. Founded 2024."), ["Summer 2027"])


class Locations(unittest.TestCase):
    def test_workday_site_names_are_not_foreign(self):
        # Bug: 'Street, City' was treated as 'City, Country' and Zoetis's Kalamazoo roles got closed.
        self.assertFalse(C.location_clearly_non_us("Downtown Portage Street, Kalamazoo"))
        self.assertTrue(C.is_us(["Downtown Portage Street, Kalamazoo"], "United States"))
        self.assertEqual(C.states_of(["Downtown Portage Street, Kalamazoo"]), ["Michigan"])

    def test_us_filter(self):
        self.assertTrue(C.is_us(["Boston, MA"]))
        self.assertTrue(C.is_us(["Remote"]))
        self.assertTrue(C.is_us(["Ponce, Puerto Rico"]))
        self.assertFalse(C.is_us(["Prague, Czech Republic"]))
        self.assertFalse(C.is_us(["Shanghai, China"]))
        self.assertFalse(C.is_us(["Prague"], "Czechia"))

    def test_formats(self):
        self.assertEqual(C.clean_location("United States - Massachusetts - Cambridge"), "Cambridge, MA")
        self.assertEqual(C.clean_location("Princeton - NJ - US"), "Princeton, NJ")
        self.assertEqual(C.clean_location("Belgium-Wavre"), "Wavre, Belgium")
        self.assertEqual(C.clean_locations(["Cambridge, MA", "Clinical Development"]), ["Cambridge, MA"])

    def test_remote_with_foreign_title_dropped(self):
        raw = {"key": "x", "title": "Intern, GI, Qingdao", "url": "u", "locations": ["Remote"], "posted": None, "description": ""}
        self.assertIsNone(build_listing({"name": "Takeda"}, raw, 1_800_000_000))

    def test_remote_puerto_rico_title_kept(self):
        raw = {"key": "x", "title": "Quality Intern - Puerto Rico", "url": "u", "locations": ["Remote"], "posted": None, "description": ""}
        self.assertIsNotNone(build_listing({"name": "Co"}, raw, 1_800_000_000))

    def test_attrax_country_beats_state_lookalike(self):
        # "Madrid, MD" is Spain (from the tile's CSS class), not Maryland.
        self.assertFalse(C.is_us(C.clean_locations(["Madrid, MD"]), "Non-US"))


class WorkdayPagination(unittest.TestCase):
    def test_total_only_on_first_page(self):
        """Bug: Workday reports `total` only on page 1 (0 afterwards) — we stopped after 2 pages."""
        jobs = [{"title": f"Summer 2027 Intern {i}", "externalPath": f"/job/x/{i}", "locationsText": "Boston, MA"} for i in range(55)]

        def fake_post(url, body, quiet=False):
            off, lim = body["offset"], body["limit"]
            total = 55 if off == 0 else 0
            page = jobs[off:off + lim] if body["searchText"] in ("", "summer intern") else []
            return {"total": total, "jobPostings": page, "facets": []}

        def fake_get(url, quiet=False):
            i = url.rsplit("/", 1)[1]
            return {"jobPostingInfo": {"jobReqId": f"R{i}", "externalUrl": f"https://x/{i}", "location": "Boston, MA",
                                       "country": {"descriptor": "United States of America"}}}

        with mock.patch.object(ats, "post_json", fake_post), mock.patch.object(ats, "get_json", fake_get):
            out = ats.workday({"name": "X", "tenant": "x", "host": "wd1", "site": "S"})
        self.assertEqual(len(out), 55)

    def test_failed_detail_marks_company_failed(self):
        def fake_post(url, body, quiet=False):
            return {"total": 1, "jobPostings": [{"title": "Intern", "externalPath": "/job/a"}] if body["offset"] == 0 else [], "facets": []}

        with mock.patch.object(ats, "post_json", fake_post), mock.patch.object(ats, "get_json", lambda *a, **k: None):
            self.assertIsNone(ats.workday({"name": "X", "tenant": "x", "host": "wd1", "site": "S"}))


class AttraxPagination(unittest.TestCase):
    def test_keyword_pagination_not_cut_by_earlier_keywords(self):
        """Codex review: page 1 of keyword B holding only keyword-A jobs must not stop B's pagination."""
        def tile(i):
            return (f'<div class="attrax-vacancy-tile  attrax-vacancy-tile--united-states" data-jobid="{i}">'
                    f'<a class="attrax-vacancy-tile__title" href="/en/job/x-jid-{i}">Intern {i}</a></div>')
        pages = {("a", 1): [1, 2], ("a", 2): [], ("b", 1): [1, 2], ("b", 2): [3], ("b", 3): []}

        def fake_get_text(url, quiet=False, headers=None):
            kw = url.split("q=")[1].split("&")[0]
            pg = int(url.split("page=")[1])
            return "".join(tile(i) for i in pages.get((kw, pg), []))

        with mock.patch.object(ats, "get_text", fake_get_text):
            out = ats.attrax({"name": "X", "host": "h", "keywords": ["a", "b"]})
        self.assertEqual(sorted(j["key"] for j in out), ["ax:h:1", "ax:h:2", "ax:h:3"])


class ClosureGuard(unittest.TestCase):
    def rows(self, n):
        return [{"source": "bot", "company_name": "Co", "active": True, "ats_key": f"k{i}", "misses": 1} for i in range(n)]

    def test_mass_disappearance_is_ignored(self):
        rows, flags = self.rows(10), []
        closed = apply_misses(rows, "Co", seen_keys={"k0", "k1"}, now=1, suspicious=flags)
        self.assertEqual(closed, 0)
        self.assertTrue(all(r["active"] and r["misses"] == 1 for r in rows))
        self.assertEqual(len(flags), 1)

    def test_persistent_mass_closure_eventually_counts(self):
        """Codex review: the guard must not keep genuinely-closed jobs open forever."""
        rows = self.rows(10)
        for run in range(3):
            apply_misses(rows, "Co", seen_keys={"k0", "k1"}, now=1, suspicious=[])
        self.assertEqual(sum(not r["active"] for r in rows), 8)

    def test_normal_closures_still_happen(self):
        rows, flags = self.rows(10), []
        closed = apply_misses(rows, "Co", seen_keys={f"k{i}" for i in range(8)}, now=1, suspicious=flags)
        self.assertEqual(closed, 2)
        self.assertEqual(flags, [])


if __name__ == "__main__":
    unittest.main()
