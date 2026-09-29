#!/usr/bin/env python3
"""
reevo_clean.py — HubSpot export  ->  Reevo Contact + Account import file.

Usage
-----
    python reevo_clean.py <raw_export.csv|.xlsx> [--out DIR] [--default-owner EMAIL]
                          [--template reevo_import.csv]

Outputs (written to --out, default: ./output/)
    <name>_reevo_import.csv   Upload-ready file. One header row, 11 Reevo columns, nothing else.
    <name>_rejected.csv       Rows that cannot be imported, with the reason, plus all original data.
    <name>_review.csv         Rows that WERE imported but where the script changed or guessed something.

Design rules
    * Columns are matched by NAME, never by position, so column order does not matter.
    * Header matching ignores case, spaces and punctuation ("First Name" == "first_name" == "FIRSTNAME").
    * Extra columns are ignored. Missing optional columns are left blank. Missing columns that
      make every row un-importable stop the run with a clear error.
    * Nothing is dropped silently: every row lands in exactly one of import / rejected.

All business rules live in the CONFIG section below so they can be changed without touching the logic.
Requires: Python 3.9+, pandas (and openpyxl for .xlsx input) for the CLI.
The cleaning core (process) is standard-library only, which is what lets the web app run it in the browser.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

# pandas is only needed for reading files from disk (CLI). The web app passes rows in directly,
# so the core engine runs on the Python standard library alone.

# =====================================================================================
# CONFIG
# =====================================================================================

# Final Reevo import headers, in template order (row 4 of the template, minus column A).
OUTPUT_COLUMNS = [
    "contact_owner_id",
    "contact_first_name",
    "contact_last_name",
    "contact_primary_email",
    "contact_primary_phone_number",
    "contact_linkedin_url",
    "contact_account_role_title",
    "account_owner_id",
    "account_name",
    "account_domain_name",
    "account_linkedin_url",
]

# Where each Reevo field comes from. Lists are in PRIORITY order: the first source column
# that has a valid value for a row wins. The first name in each list is the header seen in the
# standard export; the rest are common HubSpot variants, so a renamed export still works.
SOURCES = {
    "contact_owner_id":             ["Contact Owner Email", "Contact Owner", "HubSpot Owner", "Owner Email", "Owner"],
    "contact_first_name":           ["First Name", "Firstname"],
    "contact_last_name":            ["Last Name", "Lastname"],
    "contact_primary_email":        ["Email", "Email Address", "Work Email"],
    # Mobile > Direct > Office. "HQ" is deliberately excluded: it is the company switchboard,
    # not a way to reach this person.
    "contact_primary_phone_number": ["Mobile", "Mobile Phone Number", "Direct", "Direct Phone",
                                     "Phone Number", "Phone", "Office"],
    "contact_linkedin_url":         ["Personal Linkedin URL", "linkedinURL", "LinkedIn URL", "LinkedIn Profile"],
    "contact_account_role_title":   ["Job Title", "Title"],
    "account_owner_id":             ["Company Owner Email", "Company Owner", "Account Owner"],
    "account_name":                 ["Company Name", "Company", "Associated Company", "Company Alias"],
    "account_domain_name":          ["Website", "Company Domain Name", "Domain", "Company Website", "Website URL"],
    "account_linkedin_url":         ["Company Linkedin URL", "LinkedIn Company Page", "Company LinkedIn"],
}

# Source column used only to label rows in the rejected/review files (never exported).
RECORD_ID_SOURCES = ["Profile ID", "Record ID", "Contact ID", "Hubspot ID"]

# Phone numbers without a country code are assumed to be in this country.
DEFAULT_COUNTRY_CODE = "1"

# If the contact's email is at one of these, the email domain is NOT used as the company domain.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "gmx.com",
    "mail.com", "zoho.com", "yandex.com", "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
}

# Cell values treated as empty.
NULL_TOKENS = {"", "n/a", "na", "none", "null", "nil", "-", "--", "?", "unknown", "#n/a", "nan", "tbd", "0"}

# A first/last name containing one of these probably holds more than one person ("Martha / John").
MULTI_PERSON = re.compile(r"\s*(/|&|\+|;|\band\b)\s*", re.I)

# The brief says raw exports have at most 50 columns. Larger files still run, with a warning.
EXPECTED_MAX_COLUMNS = 50

# =====================================================================================
# Field cleaners. Each returns (clean_value, note). note is "" when nothing needs review.
# =====================================================================================

EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}$")
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
WS_RE = re.compile(r"\s+")
EXT_RE = re.compile(r"\s*(?:ext\.?|extension|x|#)\s*\d+\s*$", re.I)


def blank(v) -> bool:
    return v is None or str(v).strip().lower() in NULL_TOKENS


@lru_cache(maxsize=500_000)
def tidy(v) -> str:
    """Trim, collapse inner whitespace, strip zero-width / non-breaking characters."""
    if v is None:
        return ""
    s = str(v).replace(" ", " ").replace("​", "").replace("﻿", "")
    s = WS_RE.sub(" ", s).strip()
    return "" if s.lower() in NULL_TOKENS else s


@lru_cache(maxsize=500_000)
def clean_email(v):
    s = tidy(v).lower()
    if not s:
        return "", ""
    s = s.removeprefix("mailto:")
    parts = [p.strip(" <>\"'") for p in re.split(r"[;,\s]+", s) if p.strip()]
    valid = [p for p in parts if EMAIL_RE.match(p)]
    if not valid:
        return "", f"invalid email removed: {v!r}"
    note = f"multiple emails, kept first: {v!r}" if len(valid) > 1 else ""
    return valid[0], note


@lru_cache(maxsize=500_000)
def clean_phone(v):
    """Normalise to E.164 (+15551234567). Extensions are dropped (and noted)."""
    s = tidy(v)
    if not s:
        return "", ""
    note = ""
    if EXT_RE.search(s):
        note = f"extension dropped from {s!r}"
        s = EXT_RE.sub("", s)
    plus = s.lstrip().startswith("+") or s.lstrip().startswith("00")
    digits = re.sub(r"\D", "", s)
    if s.lstrip().startswith("00"):
        digits = digits[2:]
    if plus:
        out = digits
    elif len(digits) == 10 and DEFAULT_COUNTRY_CODE == "1":
        out = "1" + digits
    elif len(digits) == 11 and digits.startswith(DEFAULT_COUNTRY_CODE):
        out = digits
    else:
        return "", f"unparseable phone removed: {v!r}"
    if not 8 <= len(out) <= 15:
        return "", f"unparseable phone removed: {v!r}"
    return "+" + out, note


@lru_cache(maxsize=500_000)
def clean_url_path(v, kind):
    """LinkedIn URL -> https://www.linkedin.com/<kind>/<slug>. kind is 'in' or 'company'."""
    s = tidy(v)
    if not s:
        return "", ""
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    parts = urlsplit(s)
    host = parts.netloc.lower()
    if not host.endswith("linkedin.com"):
        return "", f"not a LinkedIn URL, removed: {v!r}"
    segs = [p for p in parts.path.split("/") if p]
    allowed = {"in": {"in", "pub"}, "company": {"company", "school", "showcase"}}[kind]
    if len(segs) < 2 or segs[0].lower() not in allowed:
        return "", f"wrong LinkedIn URL type for this field, removed: {v!r}"
    return f"https://www.linkedin.com/{segs[0].lower()}/{segs[1]}", ""


@lru_cache(maxsize=500_000)
def clean_domain(v):
    s = tidy(v).lower()
    if not s:
        return "", ""
    if "@" in s:                       # someone put an email in the website field
        s = s.split("@", 1)[1]
    if not re.match(r"^[a-z]+://", s):
        s = "http://" + s
    host = urlsplit(s).hostname or ""
    host = host.removeprefix("www.").strip(".")
    if not DOMAIN_RE.match(host):
        return "", f"invalid domain removed: {v!r}"
    return host, ""


def clean_owner(v):
    e, note = clean_email(v)
    return e, ("owner is not a valid email, left blank: " + repr(v)) if note and not e else note


def fix_case(name: str) -> str:
    """Only fix names that are ALL CAPS or all lower; leave mixed case (McDonald, DeVito) alone."""
    if len(name) > 1 and (name.isupper() or name.islower()):
        # capitalise after start, space, hyphen or apostrophe:  O'BRIEN -> O'Brien, mary-kate -> Mary-Kate
        return re.sub(r"(^|[\s\-'’])(\w)", lambda m: m.group(1) + m.group(2).upper(), name.lower())
    return name


# =====================================================================================
# Reading the file
# =====================================================================================

def norm_header(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def _detect_encoding(path: Path) -> str:
    head = path.open("rb").read(2_000_000)
    for enc in ("utf-8-sig", "cp1252"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


LAST_READ_WIDTH = 0   # number of non-blank headers in the last file read_any() opened


def read_any(path: Path, only_needed: bool = True) -> list[list[str]]:
    """Read csv/tsv/txt/xlsx/xls into a list of rows (all text). Row 0 is the header row.
    With only_needed=True, only the columns the tool maps are parsed (big speed-up on wide exports)."""
    import pandas as pd

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, header=None, dtype=str, keep_default_na=False)
    else:
        enc = _detect_encoding(path)
        with path.open(encoding=enc, newline="") as fh:
            first = fh.readline()
        sep = detect_delimiter(first)
        header = next(csv.reader([first], delimiter=sep), [])
        global LAST_READ_WIDTH
        LAST_READ_WIDTH = len([h for h in header if h.strip()])
        usecols = None
        if only_needed:
            found, rid = locate_columns([tidy(h) for h in header])
            usecols = sorted({i for idxs in found.values() for i in idxs} | ({rid} if rid is not None else set()))
            usecols = usecols or None
        df = pd.read_csv(path, sep=sep, header=None, dtype=str, keep_default_na=False, encoding=enc,
                         usecols=usecols, skip_blank_lines=False, engine="c")
    df = df.fillna("")
    return df.values.tolist()


def detect_delimiter(header_line: str) -> str:
    """Whichever candidate appears most in the header line."""
    return max([",", ";", "\t", "|"], key=header_line.count) if header_line else ","


def parse_text(text: str) -> list[list[str]]:
    """Standard-library CSV/TSV parser (used by the web app, where pandas isn't loaded)."""
    text = text.lstrip("\ufeff")
    first = text.split("\n", 1)[0]
    return [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=detect_delimiter(first))]


def locate_columns(headers: list[str], mapping: dict | None = None):
    """Map each Reevo field to the list of source column INDEXES (priority order) present in the file.
    mapping (optional) overrides the automatic match: {reevo_field: [exact header names, in priority order]};
    an empty list means "don't fill this field"."""
    by_norm = defaultdict(list)                      # normalised header -> [col idx, ...] (duplicates kept)
    for i, h in enumerate(headers):
        if norm_header(h):
            by_norm[norm_header(h)].append(i)
    found = {}
    for field, names in SOURCES.items():
        if mapping and field in mapping:
            found[field] = [headers.index(n) for n in mapping[field] if n in headers]
            continue
        idxs = []
        for n in names:
            idxs += [i for i in by_norm.get(norm_header(n), []) if i not in idxs]
        found[field] = idxs
    rid = next((by_norm[norm_header(n)][0] for n in RECORD_ID_SOURCES if norm_header(n) in by_norm), None)
    return found, rid


def check_template(template: Path):
    """Confirm the Reevo template headers still match OUTPUT_COLUMNS (catches template changes)."""
    for row in read_any(template, only_needed=False):
        if row and str(row[0]).strip().lower().startswith("import header"):
            tmpl = [tidy(x) for x in row[1:] if tidy(x)]
            if tmpl != OUTPUT_COLUMNS:
                sys.exit(f"ERROR: template headers changed.\n  template: {tmpl}\n  script:   {OUTPUT_COLUMNS}\n"
                         "Update OUTPUT_COLUMNS / SOURCES in reevo_clean.py.")
            return
    sys.exit("ERROR: could not find the 'Import Header:' row in the template.")


# =====================================================================================
# Core
# =====================================================================================

CLEANERS = {
    "contact_owner_id": clean_owner,
    "contact_primary_email": clean_email,
    "contact_primary_phone_number": clean_phone,
    "contact_linkedin_url": lambda v: clean_url_path(v, "in"),
    "account_owner_id": clean_owner,
    "account_domain_name": clean_domain,
    "account_linkedin_url": lambda v: clean_url_path(v, "company"),
}


def pick(row, idxs, headers, cleaner, field, notes):
    """Return the first valid value across the priority-ordered source columns."""
    chosen, chosen_src, others = "", "", []
    for i in idxs:
        raw = row[i] if i < len(row) else ""
        val, note = cleaner(raw) if cleaner else (tidy(raw), "")
        if note and val == "" and not blank(raw):
            notes.append((field, note))
        if val:
            if not chosen:
                chosen, chosen_src = val, headers[i]
                if note:
                    notes.append((field, note))
            elif val.lower() != chosen.lower():
                others.append(f"{headers[i]}={val}")
    return chosen, chosen_src, others


def _has_data(row) -> bool:
    return any(c and c.strip() and c.strip().lower() not in NULL_TOKENS for c in row)


def column_report(headers: list[str], mapping: dict | None = None) -> dict:
    """{reevo_field: [source header names used, in priority order]} — shown in the web app."""
    found, _ = locate_columns([tidy(h) for h in headers], mapping)
    clean = [tidy(h) for h in headers]
    return {f: [clean[i] for i in found[f]] for f in OUTPUT_COLUMNS}


def process(table, default_owner: str = "", mapping: dict | None = None, source_columns: int | None = None):
    """table: list of rows (row 0 = headers), or a header-less DataFrame. Returns cleaned output + reports.
    mapping: optional column-mapping override, see locate_columns().
    source_columns: column count of the original file, when the reader only loaded the columns it needs."""
    if hasattr(table, "astype") and hasattr(table, "values"):          # pandas DataFrame
        table = table.astype(str).values.tolist()
    if not table:
        raise SystemExit("ERROR: file is empty.")
    headers = [tidy(h) for h in table[0]]
    # keep 1-based spreadsheet row numbers; skip fully blank rows (short-circuits on the first non-blank cell)
    numbered = [(n, r) for n, r in enumerate(table[1:], start=2) if _has_data(r)]
    src_rownums = [n for n, _ in numbered]
    rows = [r for _, r in numbered]

    found, rid_idx = locate_columns(headers, mapping)
    run_warnings = []

    # ---- column-level checks: stop early if the file can't possibly work ----
    fatal = [f for f in ("contact_first_name", "contact_last_name", "account_name") if not found[f]]
    if not found["contact_primary_email"] and not found["contact_primary_phone_number"]:
        fatal.append("contact_primary_email AND contact_primary_phone_number")
    if fatal:
        raise SystemExit("ERROR: no source column found for: " + ", ".join(fatal) +
                         "\nHeaders in file: " + ", ".join(h for h in headers if h))
    if not found["account_domain_name"]:
        run_warnings.append("No Website/Domain column; account domains will be derived from work emails only.")
    for f in OUTPUT_COLUMNS:
        if not found[f] and f not in ("account_domain_name",):
            if f.endswith("owner_id") and default_owner:
                continue
            run_warnings.append(f"No source column for {f}; it will be blank.")
    n_cols = source_columns or len([h for h in headers if h])
    if n_cols > EXPECTED_MAX_COLUMNS:
        run_warnings.append(f"File has {n_cols} columns (brief expects <= {EXPECTED_MAX_COLUMNS}). "
                            "Processed anyway; extra columns ignored.")

    owner_default, owner_note = clean_owner(default_owner) if default_owner else ("", "")
    if default_owner and not owner_default:
        raise SystemExit(f"ERROR: --default-owner {default_owner!r} is not a valid email.")

    good, rejected, review = [], [], []

    for row, rownum in zip(rows, src_rownums):
        notes: list[tuple[str, str]] = []
        rec = {}
        srcs = {}
        for field in OUTPUT_COLUMNS:
            val, src, others = pick(row, found[field], headers, CLEANERS.get(field), field, notes)
            rec[field], srcs[field] = val, src
            if others and field in ("contact_linkedin_url", "contact_primary_email", "account_domain_name",
                                    "account_linkedin_url"):
                notes.append((field, f"conflicting values; kept {src}={val}, ignored {'; '.join(others)}"))

        # ---- owners ----
        for f in ("contact_owner_id", "account_owner_id"):
            if not rec[f] and owner_default:
                rec[f] = owner_default

        # ---- phone: flag when we fell back to a shared office line ----
        if srcs["contact_primary_phone_number"] and norm_header(srcs["contact_primary_phone_number"]) == "office":
            notes.append(("contact_primary_phone_number", "only phone is the Office line (may be a switchboard)"))

        # ---- names ----
        first, last = rec["contact_first_name"], rec["contact_last_name"]
        reject_reasons = []
        if MULTI_PERSON.search(first) or MULTI_PERSON.search(last):
            reject_reasons.append(f"name looks like more than one person: {first!r} {last!r}")
        else:
            if first and not last and " " in first:
                first, last = first.split(" ", 1)
                notes.append(("contact_last_name", f"split full name from First Name: {rec['contact_first_name']!r}"))
            elif last and not first and " " in last:
                first, last = last.split(" ", 1)
                notes.append(("contact_first_name", f"split full name from Last Name: {rec['contact_last_name']!r}"))
            first, last = fix_case(first), fix_case(last)
        rec["contact_first_name"], rec["contact_last_name"] = first, last

        # ---- title ----
        rec["contact_account_role_title"] = tidy(rec["contact_account_role_title"])

        # ---- account domain: derive from work email if missing ----
        if not rec["account_domain_name"] and rec["contact_primary_email"]:
            d = rec["contact_primary_email"].split("@", 1)[1]
            if d not in FREE_EMAIL_DOMAINS:
                rec["account_domain_name"] = d
                notes.append(("account_domain_name", f"no Website; derived '{d}' from contact email"))
        elif rec["account_domain_name"] and rec["contact_primary_email"]:
            d = rec["contact_primary_email"].split("@", 1)[1]
            if d not in FREE_EMAIL_DOMAINS and not (d == rec["account_domain_name"] or
                                                    d.endswith("." + rec["account_domain_name"]) or
                                                    rec["account_domain_name"].endswith("." + d)):
                notes.append(("contact_primary_email",
                              f"email domain '{d}' differs from company domain '{rec['account_domain_name']}'"))

        # ---- required fields ----
        if not first:
            reject_reasons.append("missing first name")
        if not last:
            reject_reasons.append("missing last name")
        if not rec["contact_primary_email"] and not rec["contact_primary_phone_number"]:
            reject_reasons.append("no valid email or phone")
        if not rec["account_name"]:
            reject_reasons.append("missing company name")
        if not rec["account_domain_name"]:
            reject_reasons.append("missing company domain (no Website and no work email)")

        rid = row[rid_idx] if rid_idx is not None and rid_idx < len(row) else ""
        label = {"source_row": rownum, "record_id": tidy(rid), "name": f"{first} {last}".strip()}

        if reject_reasons:
            rej = {"source_row": rownum, "reject_reason": "; ".join(dict.fromkeys(reject_reasons))}
            rej.update({h if h else f"col_{i+1}": (row[i] if i < len(row) else "") for i, h in enumerate(headers)})
            rejected.append(rej)
        else:
            good.append((rownum, label, rec))
            for field, note in notes:
                review.append({**label, "field": field, "issue": note})

    # ---- account consistency: one name / linkedin / owner per domain ----
    by_domain = defaultdict(list)
    for _, label, rec in good:
        by_domain[rec["account_domain_name"]].append((label, rec))
    for dom, items in by_domain.items():
        for f in ("account_name", "account_linkedin_url", "account_owner_id"):
            counts = Counter(r[f] for _, r in items if r[f])
            if len(counts) > 1:
                winner = counts.most_common(1)[0][0]
                for label, r in items:
                    if r[f] and r[f] != winner:
                        review.append({**label, "field": f,
                                       "issue": f"standardised '{r[f]}' -> '{winner}' (most common for {dom})"})
                    r[f] = winner
            elif len(counts) == 1:                      # fill blanks from siblings with the same domain
                only = next(iter(counts))
                for label, r in items:
                    if not r[f]:
                        r[f] = only

    # ---- exact duplicate rows ----
    seen, out = set(), []
    dup_count = 0
    for rownum, label, rec in good:
        key = tuple(rec[c].lower() for c in OUTPUT_COLUMNS)
        if key in seen:
            dup_count += 1
            review.append({**label, "field": "(row)", "issue": "exact duplicate of an earlier row; removed"})
            continue
        seen.add(key)
        rec["_source_row"] = rownum          # not exported (write_csv only writes OUTPUT_COLUMNS); used by the web app
        out.append(rec)

    # ---- same email on multiple contacts (dedupe rules are Part 2; flag only) ----
    emails = Counter(r["contact_primary_email"] for r in out if r["contact_primary_email"])
    for rownum, label, rec in good:
        e = rec["contact_primary_email"]
        if e and emails[e] > 1:
            review.append({**label, "field": "contact_primary_email", "issue": f"email shared by {emails[e]} rows"})

    # ---- collapse systemic conflicts (e.g. two LinkedIn columns that disagree on most rows) into
    #      ONE file-level warning so they don't bury the row-level notes that actually need a human ----
    conflict_counts = Counter(r["field"] for r in review if r["issue"].startswith("conflicting values"))
    systemic = {f for f, n in conflict_counts.items() if n >= max(5, 0.3 * max(len(out), 1))}
    for f in systemic:
        srcs = [headers[i] for i in found[f]]
        run_warnings.append(f"{f}: source columns {srcs} disagree on {conflict_counts[f]} rows; "
                            f"used '{srcs[0]}' and fell back to the others only when it was blank. "
                            "Confirm with the customer which column is authoritative.")
    review = [r for r in review if not (r["field"] in systemic and r["issue"].startswith("conflicting values"))]

    return out, rejected, review, run_warnings, dup_count


def to_csv_text(rows: list[dict], columns: list[str] | None = None) -> str:
    cols = columns or (list(dict.fromkeys(k for r in rows for k in r)) if rows else ["(none)"])
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


REVIEW_COLUMNS = ["source_row", "record_id", "name", "field", "issue"]


def with_file_level(review: list[dict], warnings: list[str]) -> list[dict]:
    """Prepend file-level warnings to the review rows (source_row = ALL)."""
    return [{"source_row": "ALL", "record_id": "", "name": "", "field": "(file)", "issue": w}
            for w in warnings] + review


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None):
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(to_csv_text(rows, columns))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Clean a HubSpot export into a Reevo import file.")
    ap.add_argument("input", type=Path, help="raw export (.csv, .tsv, .txt, .xlsx, .xls)")
    ap.add_argument("--out", type=Path, default=Path("output"), help="output folder (default ./output)")
    ap.add_argument("--default-owner", default="", help="Reevo user email to use when no owner is in the data")
    ap.add_argument("--template", type=Path, help="Reevo template csv; verifies headers haven't changed")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    if a.template:
        check_template(a.template)
    if not a.input.exists():
        raise SystemExit(f"ERROR: file not found: {a.input}")
    global LAST_READ_WIDTH
    LAST_READ_WIDTH = 0
    table = read_any(a.input)
    out, rejected, review, warnings, dups = process(table, a.default_owner, source_columns=LAST_READ_WIDTH or None)

    a.out.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", a.input.stem)
    p_imp = a.out / f"{stem}_reevo_import.csv"
    p_rej = a.out / f"{stem}_rejected.csv"
    p_rev = a.out / f"{stem}_review.csv"
    write_csv(p_imp, out, OUTPUT_COLUMNS)
    write_csv(p_rej, rejected, None if rejected else ["source_row", "reject_reason"])
    write_csv(p_rev, with_file_level(review, warnings), REVIEW_COLUMNS)

    total = len(out) + len(rejected) + dups
    print(f"\nReevo import cleaner  —  {a.input.name}")
    print(f"  rows read        {total}")
    print(f"  ready to import  {len(out)}   -> {p_imp}")
    print(f"  rejected         {len(rejected)}   -> {p_rej}")
    print(f"  duplicates       {dups}")
    print(f"  review notes     {len(review)}   -> {p_rev}")
    if rejected:
        print("  reject reasons:")
        for reason, n in Counter(r.split(":")[0] for x in rejected for r in x["reject_reason"].split("; ")).most_common():
            print(f"    {n:>6}  {reason}")
    for w in warnings:
        print(f"  WARNING: {w}")
    print(f"  done in {time.perf_counter() - t0:.2f}s\n")


if __name__ == "__main__":
    main()
