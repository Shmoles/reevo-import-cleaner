# HubSpot → Reevo Import Cleaner

![tests](https://github.com/Shmoles/reevo-import-cleaner/actions/workflows/tests.yml/badge.svg)

Turns a raw HubSpot Contact/Account export into an upload-ready Reevo import file. Rows it can't fix go into a separate file with the reason, so nothing disappears without a trace.

## Run it

```bash
pip install -r requirements.txt      # one time
python engine/reevo_clean.py sample/raw_data.csv --template sample/reevo_template.csv --default-owner rep@customer.com
```

The output of running it on the sample file is committed in [`sample/output/`](sample/output/), so you can see the results without running anything.

| Option | What it does |
|---|---|
| `raw_export.csv` | The customer's export. `.csv`, `.tsv`, `.txt`, `.xlsx`, `.xls` all work. Comma, semicolon, tab or pipe delimited. Any encoding. |
| `--default-owner` | Reevo user email used for `contact_owner_id` / `account_owner_id` when the export has no owner column. Optional. |
| `--template` | Path to the Reevo template. The run stops if Reevo has changed the import headers. Optional, but recommended. |
| `--out` | Output folder (default `./output`). |

## What you get

| File | What it is | What to do with it |
|---|---|---|
| `*_reevo_import.csv` | One header row with the 11 Reevo import headers, then the data. Helper rows 1–3 and column A from the template are **not** included. | Upload to Reevo. |
| `*_rejected.csv` | Rows that can't be imported, with `reject_reason` and the original values. | Send to the customer to fix, then re-run on the corrected rows. |
| `*_review.csv` | Rows that were imported but where the tool guessed or changed something. `source_row = ALL` lines are file-level warnings. | Skim before uploading. |

The console prints a summary: counts, reject reasons, warnings and runtime.

## Repo layout

```
engine/reevo_clean.py      the cleaner: CLI + all rules (one source of truth)
tests/test_reevo_clean.py  31 edge-case checks + runtime benchmark
sample/                    sample HubSpot export, Reevo template, and the tool's output
docs/                      Part 2: unique identifier strategy (coming)
```

## Rules applied

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

The suite runs automatically on every push through GitHub Actions (badge at the top).

31 checks, including:
- shuffled columns
- renamed or oddly-cased headers
- 12-column vs 73-column files
- semicolon / tab / cp1252 / BOM / .xlsx input
- duplicate header names
- messy emails, phones, URLs and names
- blank rows and duplicate rows
- clear errors for a missing required column or a changed template
- a runtime benchmark: 100,000 unique contacts × 73 columns (390 MB) in about 7s

## Questions for the customer (sample file)

1. **Owners:** the export has no owner column. Who should own these contacts and accounts in Reevo? One rep, or a mapping by territory or account?
2. **LinkedIn:** `Personal Linkedin URL` and `linkedinURL` disagree on every row where both are filled (49 of 49) (`/in/annaking` vs `/in/anna-king`). Which is authoritative? The tool currently prefers `Personal Linkedin URL`.
3. **Phone:** is Mobile the right primary number, or should it be Direct? The Holistic Industries "Office" number is the same for every contact. Is it a switchboard?
4. **Out-of-place records:** five rows (Apple, ASML, Applied Materials, including "Bruce Wayne" and "Martha / John") don't match the rest of this cannabis-operator list and have no website. Are they real prospects or test data?
5. **Email Quality:** 4 emails are marked "Email" rather than "Verified Email" (all at TerrAscend). Import them, or only verified ones?
6. **Column count:** the export has 73 columns, though we were told to expect ≤ 50. Is this the standard export view?
7. **Rejected rows:** 5 rows have no email/phone and/or no domain. Can the customer supply these, or should the rows be left out?

**Questions for Reevo internally**
- Phone format: is E.164 correct?
- Do extensions have a field?
- Is a contact with no account allowed, or is the Account required on every row?
