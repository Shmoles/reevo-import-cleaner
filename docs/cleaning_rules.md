# Cleaning rules

How the cleaner decides what goes into the import file. Everything here is set in the `CONFIG` block at the top of [`engine/reevo_clean.py`](../engine/reevo_clean.py).

## What it does

**Columns**
- Columns are matched by **name, not position**, so column order doesn't matter.
- Matching ignores case, spaces and punctuation, so `First Name`, `first_name` and `FIRSTNAME` all match.
- Extra columns are ignored.
- If a required source column is missing entirely (first name, last name, company, or both email and phone), the run stops with a clear error.
- If an optional column is missing, that field is left blank and a warning is printed.

**Field → source (first valid value wins)**

| Reevo field | Source columns, in priority order |
|---|---|
| contact_first_name / last_name | First Name / Last Name |
| contact_primary_email | Email |
| contact_primary_phone_number | Mobile → Direct → Office (HQ is excluded because it's the company switchboard) |
| contact_linkedin_url | Personal Linkedin URL → linkedinURL |
| contact_account_role_title | Job Title |
| account_name | Company Name → Company Alias |
| account_domain_name | Website → otherwise the domain of the contact's work email (never gmail/yahoo/etc.) |
| account_linkedin_url | Company Linkedin URL |
| *_owner_id | Owner column if present → `--default-owner` |

**Cleaning**
- **All text:** trimmed, repeated spaces collapsed, invisible characters removed. Placeholder values (`n/a`, `null`, `-`, `unknown`…) are treated as blank.
- **Names:**
  - `ALL CAPS` / `lowercase` names are fixed (`O'BRIEN` → `O'Brien`). Mixed case is left alone (`DeVito`).
  - A full name in First Name with Last Name blank is split (`Bruce Wayne` → `Bruce` / `Wayne`).
  - Multi-person names (`Martha / John`, `Tom & Ann`) are rejected.
- **Email:** lowercased, `mailto:` removed. If a cell holds several emails, the first valid one is kept. Invalid emails are removed.
- **Phone:** converted to E.164 (`+14407288779`). Numbers without a country code are assumed US/CA. Extensions are dropped and noted in the review file. Unparseable numbers are removed.
- **LinkedIn:** reduced to `https://www.linkedin.com/in/<slug>` or `/company/<slug>`, with tracking parameters and trailing slashes stripped. A company URL in the person field (or the reverse) is removed.
- **Domain:** reduced to the bare domain (`https://www.Acme.com/about` → `acme.com`).
- **Account consistency:** every contact with the same domain gets the same company name and company LinkedIn (the most common value). This way one Reevo account isn't written with conflicting values.

**Rejected if**
- first name missing
- last name missing
- no valid email **and** no valid phone
- company name missing
- company domain missing

**Also**
- Exact duplicate rows are removed. Contacts that share an email are flagged in the review file. Deeper de-duplication by unique ID is covered in Part 2.

## Changing the rules

All rules live in the `CONFIG` block at the top of `engine/reevo_clean.py`:
- `SOURCES`: column names and their priority order
- `FREE_EMAIL_DOMAINS`
- `NULL_TOKENS`
- `DEFAULT_COUNTRY_CODE`

The rest of the code doesn't need to change.

## Tests

```bash
python tests/test_reevo_clean.py              # full suite, including the 100k-row benchmark
BENCH_ROWS=0 python tests/test_reevo_clean.py # skip the benchmark
```

The suite runs automatically on every push through GitHub Actions (badge on the main README).

36 checks, including:
- shuffled columns
- renamed or oddly-cased headers
- 12-column vs 73-column files
- semicolon / tab / cp1252 / BOM / .xlsx input
- duplicate header names
- messy emails, phones, URLs and names
- blank rows and duplicate rows
- clear errors for a missing required column or a changed template
- the browser path: the web parser and manual column-mapping overrides give the same results
- a runtime benchmark: 100,000 unique contacts × 73 columns (390 MB) in about 8s
