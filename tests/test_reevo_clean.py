"""
Edge-case tests for engine/reevo_clean.py.   Run from the repo root:  python tests/test_reevo_clean.py
Uses sample/raw_data.csv (the sample export) as the baseline and mutates it.
Set BENCH_ROWS=0 to skip the large-file benchmark.
"""
import io
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import os

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import reevo_clean as rc  # noqa: E402

ENGINE = ROOT / "engine" / "reevo_clean.py"
RAW = ROOT / "sample" / "raw_data.csv"
TEMPLATE = ROOT / "sample" / "reevo_template.csv"
BENCH_ROWS = int(os.environ.get("BENCH_ROWS", "100000"))
TMP = Path(tempfile.mkdtemp())
PASS = []


def run(df_or_path, **kw):
    """Run the cleaner in-process on a DataFrame (header as first row) or a file path."""
    df = rc.read_any(df_or_path) if isinstance(df_or_path, Path) else df_or_path
    out, rej, rev, warn, dups = rc.process(df, **kw)
    return pd.DataFrame(out, columns=rc.OUTPUT_COLUMNS), rej, rev, warn, dups


def as_raw(df):
    """DataFrame with real headers -> header-less frame like read_any returns."""
    return pd.concat([pd.DataFrame([df.columns.tolist()]), pd.DataFrame(df.values)], ignore_index=True)


def ok(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"FAIL: {name} {detail}")
    PASS.append(name)
    print(f"  ok  {name}")


src = pd.read_csv(RAW, dtype=str, keep_default_na=False)
base, base_rej, *_ = run(as_raw(src))

# 1. Column order doesn't matter
cols = src.columns.tolist()
random.seed(7)
random.shuffle(cols)
shuf, *_ = run(as_raw(src[cols]))
ok("shuffled columns -> identical output", shuf.equals(base))

# 2. Header spelling variations (case / underscores / spaces)
variant = src.rename(columns={"First Name": "first_name", "Last Name": "LAST NAME", "Email": " email ",
                              "Company Name": "company-name", "Job Title": "JobTitle"})
var_out, *_ = run(as_raw(variant))
ok("header case/spacing variants -> identical output", var_out.equals(base))

# 3. Fewer columns (only 12 of 73) and some optional ones missing entirely
few = src[["First Name", "Last Name", "Email", "Mobile", "Company Name", "Website", "Job Title",
           "Direct", "Office", "Profile ID", "Country", "Headcount"]]
few_out, _, _, few_warn, _ = run(as_raw(few))
ok("12-column file runs", len(few_out) > 0)
ok("missing LinkedIn columns -> blank + warning",
   few_out["contact_linkedin_url"].eq("").all() and any("contact_linkedin_url" in w for w in few_warn))

# 4. Encodings / delimiters / BOM
p = TMP / "semicolon_cp1252.csv"
src.to_csv(p, sep=";", index=False, encoding="cp1252", errors="replace")
enc_out, *_ = run(p)
ok("semicolon-delimited cp1252 file -> identical output", enc_out.equals(base))
p = TMP / "bom.csv"
src.to_csv(p, index=False, encoding="utf-8-sig")
bom_out, *_ = run(p)
ok("UTF-8 BOM file -> identical output", bom_out.equals(base))
p = TMP / "tab.tsv"
src.to_csv(p, sep="\t", index=False)
ok("tab-delimited file -> identical output", run(p)[0].equals(base))

# 5. Excel input
p = TMP / "raw.xlsx"
src.to_excel(p, index=False)
ok("xlsx input -> identical output", run(p)[0].equals(base))

# 6. Messy values
messy = pd.DataFrame([
    # first,      last,       email,                         mobile,                 direct,          company,        website,                    person li,                                   company li
    ["JOHN",      "O'BRIEN",  " MAILTO:John.OBrien@Acme.COM ", "(555) 123-4567 x12", "",              "Acme",         "https://www.Acme.com/about", "linkedin.com/in/johnobrien/?trk=abc",     "http://linkedin.com/company/acme/"],
    ["jane",      "doe",      "n/a",                          "555.987.6543",         "",              "Acme Inc",     "acme.com",                  "https://www.linkedin.com/company/acme",   ""],
    ["Mary Ann",  "Smith-Jones", "mary@gmail.com",            "",                     "",              "Solo LLC",     "",                          "",                                         ""],
    ["Pat",       "Lee",      "pat@lee.io; pat2@lee.io",      "",                     "+44 20 7946 0958", "Lee Co",     "lee.io",                    "",                                         ""],
    ["Sam",       "Roe",      "not-an-email",                 "12345",                "",              "Roe Corp",     "roe.com",                   "",                                         ""],
    ["",          "",         "",                             "",                     "",              "",             "",                          "",                                         ""],
    ["Kim",       "Ng",       "kim@acme.com",                 "",                     "",              "ACME",         "acme.com",                  "",                                         ""],
    ["Kim",       "Ng",       "kim@acme.com",                 "",                     "",              "ACME",         "acme.com",                  "",                                         ""],
    ["Tom & Ann", "Fox",      "tf@fox.com",                   "",                     "",              "Fox",          "fox.com",                   "",                                         ""],
    ["Al",        "Bo",       "al@bo.com",                    "",                     "",              "Bo",           "bo.com",                    "",                                         ""],
], columns=["First Name", "Last Name", "Email", "Mobile", "Direct", "Company Name", "Website",
            "Personal Linkedin URL", "Company Linkedin URL"])
m_out, m_rej, m_rev, _, m_dups = run(as_raw(messy), default_owner="Owner@Customer.com")
john = m_out.iloc[0]
ok("ALL CAPS name fixed, apostrophe kept", (john.contact_first_name, john.contact_last_name) == ("John", "O'Brien"))
ok("mailto/case/whitespace email cleaned", john.contact_primary_email == "john.obrien@acme.com")
ok("(555) 123-4567 x12 -> +15551234567", john.contact_primary_phone_number == "+15551234567")
ok("website URL -> bare domain", john.account_domain_name == "acme.com")
ok("LinkedIn URL normalised (query/trailing slash stripped)", john.contact_linkedin_url == "https://www.linkedin.com/in/johnobrien")
ok("default owner applied + lowercased", john.contact_owner_id == "owner@customer.com" and john.account_owner_id == "owner@customer.com")
jane = m_out[m_out.contact_first_name == "Jane"].iloc[0]
ok("'n/a' email treated as blank; phone keeps row alive", jane.contact_primary_email == "" and jane.contact_primary_phone_number == "+15559876543")
ok("company LinkedIn URL in person field is rejected", jane.contact_linkedin_url == "")
acme_names = set(m_out[m_out.account_domain_name == "acme.com"].account_name)
ok("one account name per domain (standardised)", len(acme_names) == 1, acme_names)
ok("gmail address is NOT used as company domain -> rejected", any(
    r["reject_reason"].startswith("missing company domain") for r in m_rej if r["First Name"] == "Mary Ann"))
ok("mixed-case name left alone", rc.fix_case("DeVito") == "DeVito" and rc.fix_case("mary-kate") == "Mary-Kate")
pat = m_out[m_out.contact_first_name == "Pat"].iloc[0]
ok("multiple emails -> first valid kept", pat.contact_primary_email == "pat@lee.io")
ok("international +44 number kept as E.164", pat.contact_primary_phone_number == "+442079460958")
ok("invalid email + invalid phone -> rejected", any(r["First Name"] == "Sam" for r in m_rej))
ok("fully blank row ignored (not counted anywhere)", len(m_out) + len(m_rej) + m_dups == 9)
ok("exact duplicate removed", m_dups == 1)
ok("'Tom & Ann' rejected as multi-person", any(r["First Name"] == "Tom & Ann" for r in m_rej))

# 7. Duplicate header: two 'Email' columns, first one blank
dup = src[["First Name", "Last Name", "Email", "Company Name", "Website"]].copy()
dup.insert(2, "Email ", "")
dup_out, *_ = run(as_raw(dup))
ok("duplicate 'Email' header -> falls through to the populated one",
   (dup_out.contact_primary_email != "").sum() == (base.contact_primary_email != "").sum())

# 8. Hard failures are clean messages, not stack traces
p = TMP / "no_first.csv"
src.drop(columns=["First Name"]).to_csv(p, index=False)
r = subprocess.run([sys.executable, str(ENGINE), str(p), "--out", str(TMP)], capture_output=True, text=True)
ok("missing required column -> clear error", r.returncode != 0 and "contact_first_name" in r.stderr and "Traceback" not in r.stderr)
p = TMP / "tmpl.csv"
p.write_text(TEMPLATE.read_text().replace("contact_last_name", "contact_surname"))
r = subprocess.run([sys.executable, str(ENGINE), str(RAW), "--template", str(p), "--out", str(TMP)],
                   capture_output=True, text=True)
ok("changed Reevo template -> clear error", r.returncode != 0 and "template headers changed" in r.stderr)

# 9. Output file shape: exactly 11 columns, one header row, no helper rows / column A
p_out = TMP / "shape"
r = subprocess.run([sys.executable, str(ENGINE), str(RAW), "--out", str(p_out)], check=True, capture_output=True, text=True)
ok("CLI still warns about >50 columns when it only reads the needed ones", "File has 73 columns" in r.stdout)
f = pd.read_csv(p_out / "raw_data_reevo_import.csv", dtype=str, keep_default_na=False)
ok("import file has exactly the 11 Reevo headers, in order", f.columns.tolist() == rc.OUTPUT_COLUMNS)
ok("every input row is accounted for (import + rejected)", len(base) + len(base_rej) == len(src))

# 10. Web app paths: stdlib parser (no pandas) and manual column-mapping override
web_rows = rc.parse_text(RAW.read_text(encoding="utf-8-sig"))
web_out, *_ = run(web_rows)
ok("browser parser (stdlib only) -> identical output", web_out.equals(base))
semi = src.to_csv(index=False, sep=";")
ok("browser parser detects ';' delimiter", run(rc.parse_text(semi))[0].equals(base))
ovr, *_ = run(web_rows, mapping={"contact_linkedin_url": ["linkedinURL"], "contact_account_role_title": []})
ok("mapping override switches source column",
   ovr.contact_linkedin_url.iloc[0] == "https://www.linkedin.com/in/alexander-nelson")
ok("mapping override can blank a field", ovr.contact_account_role_title.eq("").all())

# 11. Runtime at scale
if BENCH_ROWS:
    big = pd.concat([src] * (BENCH_ROWS // len(src) + 1), ignore_index=True).iloc[:BENCH_ROWS].copy()
    # make every contact unique so caching can't flatter the benchmark (companies still repeat, as in real life)
    n = pd.Series(range(len(big))).astype(str)
    big["First Name"] = big["First Name"] + n
    big["Email"] = big["Email"].where(big["Email"] == "", n + "." + big["Email"])
    big["Mobile"] = big["Mobile"].where(big["Mobile"] == "", "+1 212-" + n.str.zfill(7).str[-7:])
    big["Personal Linkedin URL"] = big["Personal Linkedin URL"].where(big["Personal Linkedin URL"] == "",
                                                                      big["Personal Linkedin URL"] + n)
    p = TMP / "big.csv"
    big.to_csv(p, index=False)
    mb = p.stat().st_size / 1e6
    t = time.perf_counter()
    subprocess.run([sys.executable, str(ENGINE), str(p), "--out", str(TMP / "big")], check=True, capture_output=True)
    secs = time.perf_counter() - t
    ok(f"{BENCH_ROWS:,} unique rows x {len(src.columns)} cols ({mb:.0f} MB) end-to-end in {secs:.1f}s", secs < 120)

print(f"\nAll {len(PASS)} checks passed.")
