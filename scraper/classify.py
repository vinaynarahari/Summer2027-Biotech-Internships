"""Rules that turn a raw job into a listing: is it an internship, which
category, which term, sponsorship, degree, and clean locations."""

from __future__ import annotations

import re

# Season the main README targets. Bump these each cycle (e.g. in ~Aug 2027).
SUMMER_YEAR = 2027
OFF_SEASON_TERMS = ["Fall 2026", "Winter 2027", "Spring 2027", "Fall 2027"]

# --------------------------------------------------------------------------- #
# Internship detection
# --------------------------------------------------------------------------- #
INTERN_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?op|summer student|student worker|"
    r"working student|werkstudent(in)?|praktikum|praktikant(in)?|pflichtpraktikum|stagiaire|"
    r"tirocinio|tirocinante|pr[aá]cticas|becari[oa]|estagi[aá]ri[oa]|est[aá]gio|"
    r"industrial placement|placement student|year in industry|undergraduate researcher|"
    r"summer research (program|scholar|fellow))\b",
    re.I,
)
NOT_INTERN_RE = re.compile(
    r"\b(internal medicine|intern(al|ship)? (program )?(manager|director|coordinator|lead)|"
    r"postdoc|post-doc|postdoctoral|residency|resident physician|"
    r"internship coordinator|university relations (manager|partner|lead))\b",
    re.I,
)


def looks_like_internship(title: str, hint: bool = False) -> bool:
    if NOT_INTERN_RE.search(title):
        return False
    return bool(INTERN_RE.search(title)) or hint


# --------------------------------------------------------------------------- #
# Categories (order matters: first match wins)
# --------------------------------------------------------------------------- #
CATEGORIES = [
    (
        "Computational Biology, Data & AI",
        "🧬",
        r"bioinformatic|computational|comp ?bio|data scien|data engineer|data analy|machine learning|"
        r"\bml\b|\bai\b|artificial intelligence|deep learning|software|developer|programming|"
        r"statistic|biostat|cheminformatic|genomic data|digital|informatics|\bit\b|cyber|cloud|"
        r"automation engineer|full[- ]stack|backend|frontend|modeling|modelling|quantitative|analytics|"
        r"genai|gen ai|\bllm|agentic|business intelligence",
    ),
    (
        "Business, Commercial & Operations",
        "💼",
        r"marketing|finance|financial|accounting|\bsales\b|human resources|\bhr\b|talent|legal|"
        r"communications|\bcomms\b|business (process|development)|strategy|strategic|\besg\b|"
        r"commercial|controller|\bfp&a\b|treasury|\btax\b|audit|procurement|brand|investor|"
        r"project manag|program manag|corporate affairs|public affairs|policy|administrative",
    ),
    (
        "Clinical, Regulatory & Medical Affairs",
        "🩺",
        r"clinical|regulatory|medical affairs|pharmacovigilance|drug safety|patient safety|heor|"
        r"health economics|outcomes research|epidemiolog|real[- ]world|medical writing|"
        r"medical information|\btrials?\b|public health|market access|compliance|pharmacy|patient",
    ),
    (
        "Engineering, Manufacturing & Quality",
        "🏭",
        r"engineer|manufactur|process dev|bioprocess|upstream|downstream|purification|"
        r"quality|\bqa\b|\bqc\b|validation|msat|cmc|supply chain|operations|facilities|"
        r"packaging|production|device|mechanical|electrical|chemical eng|biomedical eng|"
        r"\behs\b|environmental health|technical operations|logistics|procurement|sourcing|"
        r"maschinenbau|ingenieur|fertigung|produktion|medizintechnik|qualit[äa]t|calibration|maintenance|reliability",
    ),
    (
        "Research & Lab Science",
        "🔬",
        r"research|scien|\blab\b|laborator|biolog|chemi|biochem|discovery|assay|\bcells?\b|cellular|cell (line|therap|culture|biology)|protein|"
        r"molecular|pharmacolog|toxicolog|immunolog|oncolog|neuro|formulation|analytical|"
        r"genetic|genom|microbio|virolog|vaccine|antibod|translational|r&d|in vivo|in vitro|veterinar|animal|medicine|"
        r"bioanalytic|biologics|gene therap|crispr|sequencing|pathology|histolog|drug metabolism|dmpk",
    ),
    (
        "Business, Commercial & Operations",
        "💼",
        r".",  # fallback: marketing, finance, strategy, BD, HR, legal, comms, sales...
    ),
]
_CAT_RES = [(name, emoji, re.compile(rx, re.I)) for name, emoji, rx in CATEGORIES]
CATEGORY_EMOJI = {name: emoji for name, emoji, _ in CATEGORIES}
CATEGORY_ORDER = list(dict.fromkeys(name for name, _, _ in CATEGORIES))
# README / site display order
DISPLAY_ORDER = [
    "Research & Lab Science",
    "Computational Biology, Data & AI",
    "Engineering, Manufacturing & Quality",
    "Clinical, Regulatory & Medical Affairs",
    "Business, Commercial & Operations",
]


def categorize(title: str) -> str:
    for name, _, rx in _CAT_RES:
        if rx.search(title):
            return name
    return CATEGORY_ORDER[-1]


# --------------------------------------------------------------------------- #
# Terms
# --------------------------------------------------------------------------- #
SEASON_RE = re.compile(r"\b(summer|fall|autumn|winter|spring|sommer|herbst|fr[uü]hjahr|fr[uü]hling|été|printemps|automne|hiver)\s*(?:term\s*)?(?:'|’)?(20\d\d|\d\d)?\b", re.I)
YEAR_RE = re.compile(r"\b(20\d\d)\b")
SEASON_ALIASES = {
    "autumn": "fall", "herbst": "fall", "automne": "fall", "sommer": "summer", "ete": "summer",
    "été": "summer", "frühjahr": "spring", "fruhjahr": "spring", "frühling": "spring",
    "fruhling": "spring", "printemps": "spring", "hiver": "winter",
}
OFF_CYCLE_RE = re.compile(r"\b(\d{1,2})[- ]?months?\b|placement|year in industry|\bco-?op\b|working student|werkstudent", re.I)


def detect_terms(title: str, description: str = "") -> list[str]:
    """Return terms like ['Summer 2027'] or ['Fall 2026']. Title wins over description."""
    for i, text in enumerate((title, description[:1500])):
        terms = []
        for season, yr in SEASON_RE.findall(text):
            if i == 1 and not yr:
                continue  # in descriptions only trust "Summer 2027"-style mentions
            season = SEASON_ALIASES.get(season.lower(), season.lower()).capitalize()
            if yr:
                yr = int(yr) if len(yr) == 4 else 2000 + int(yr)
            else:
                ys = [int(y) for y in YEAR_RE.findall(text) if 2024 <= int(y) <= 2030]
                yr = ys[0] if ys else (SUMMER_YEAR if season == "Summer" else None)
            if yr:
                terms.append(f"{season} {yr}")
        if terms:
            return list(dict.fromkeys(terms))
    m = OFF_CYCLE_RE.search(title)
    if m and not (m.group(1) and int(m.group(1)) <= 4):
        return ["Co-op / Off-Cycle"]
    return [f"Summer {SUMMER_YEAR}"]


def is_stale(terms: list[str]) -> bool:
    """A listing for a season that has already passed (e.g. 'Summer 2025')."""
    for t in terms:
        m = re.match(r"(\w+) (\d{4})", t)
        if m and int(m.group(2)) >= SUMMER_YEAR - (1 if m.group(1) in ("Fall", "Winter") else 0):
            return False
        if not m:
            return False
    return True


def is_off_season(terms: list[str]) -> bool:
    return not any(t == f"Summer {SUMMER_YEAR}" for t in terms)


# --------------------------------------------------------------------------- #
# Sponsorship / degree
# --------------------------------------------------------------------------- #
CITIZEN_RE = re.compile(
    r"(u\.?s\.? citizenship (is )?required|must be an? u\.?s\.? citizen|requires? u\.?s\.? citizenship|"
    r"\bitar\b|security clearance)",
    re.I,
)
NO_SPONSOR_RE = re.compile(
    r"(will not|won't|unable to|cannot|can't|not able to|does not|do not|is not able to) (provide |offer )?"
    r"(visa |immigration )?sponsor|no (visa )?sponsorship|without (the need for )?(current or future )?"
    r"(visa |employment |immigration )?sponsorship|not eligible for (visa )?sponsorship|"
    r"sponsorship (is )?not (available|provided|offered)",
    re.I,
)


def detect_sponsorship(description: str) -> str:
    if CITIZEN_RE.search(description):
        return "U.S. Citizenship is Required"
    if NO_SPONSOR_RE.search(description):
        return "Does Not Offer Sponsorship"
    return "Other"


def detect_degrees(title: str) -> list[str]:
    out = []
    if re.search(r"\bph\.?d\b|doctoral|graduate research", title, re.I):
        out.append("PhD")
    if re.search(r"\bmaster'?s?\b|\bms\b|\bm\.s\.|\bmsc\b", title, re.I):
        out.append("Master's")
    if re.search(r"\bmba\b", title, re.I):
        out.append("MBA")
    if re.search(r"pharm\.?d", title, re.I):
        out.append("PharmD")
    return out


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
US_NAMES = {"united states", "united states of america", "usa", "us", "u.s."}


def clean_location(loc: str) -> str:
    loc = re.sub(r"\s+", " ", loc).strip(" ,-")
    if not loc:
        return ""
    m = re.match(r"^USA?[ -]([A-Z]{2})[ -]Remote$", loc)
    if m:
        return f"Remote in {m.group(1)}"
    if re.fullmatch(r"(remote|virtual|anywhere)", loc, re.I):
        return "Remote"
    m = re.match(r"^([A-Z][A-Za-z ]+)-([A-Z][^,-]+)$", loc)  # "Belgium-Wavre"
    if m and m.group(1).strip().lower() not in US_NAMES:
        return f"{m.group(2).strip()}, {m.group(1).strip()}"
    m = re.match(r"^USA? - ([^-]+?) - ([A-Z]{2})$", loc)  # "US - Gaithersburg - MD"
    if m:
        return f"{m.group(1)}, {m.group(2)}"
    m = re.match(r"^([A-Za-z .'-]+?),? ([A-Z]{2})$", loc)  # "Birmingham AL"
    if m and m.group(2) in US_STATES.values():
        return f"{m.group(1)}, {m.group(2)}"
    # Workday style: "United States - Massachusetts - Cambridge" / "USA - CA - South San Francisco"
    parts = [p.strip() for p in re.split(r"\s+-\s+", loc) if p.strip()]
    if len(parts) >= 3 and parts[0].lower() in US_NAMES:
        st = US_STATES.get(parts[1].lower(), parts[1] if len(parts[1]) == 2 else None)
        if st:
            return f"{parts[-1]}, {st}"
    if len(parts) >= 2 and parts[0].lower() not in US_NAMES and len(parts[0]) > 3 and parts[0][0].isupper():
        # "Germany - Berlin" -> "Berlin, Germany"
        if len(parts) == 2 and not any(ch.isdigit() for ch in loc):
            return f"{parts[1]}, {parts[0]}"
    # "Boston, Massachusetts, United States" -> "Boston, MA"
    bits = [b.strip() for b in loc.split(",")]
    if bits and bits[-1].lower() in US_NAMES:
        bits = bits[:-1]
    if len(bits) >= 2 and bits[-1].lower() in US_STATES:
        bits[-1] = US_STATES[bits[-1].lower()]
    return ", ".join(b for b in bits if b)


def clean_locations(locs: list[str]) -> list[str]:
    out = []
    for l in locs:
        c = clean_location(l)
        if c and c not in out:
            out.append(c)
    return out or ["Unknown"]
