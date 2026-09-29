# HubSpot → Reevo Import Cleaner

![tests](https://github.com/Shmoles/reevo-import-cleaner/actions/workflows/tests.yml/badge.svg)

Give it a customer's HubSpot export and it gives back a file that's ready to upload to Reevo.

<!-- VIDEO WALKTHROUGH: add once the website tool is live -->

## How to use it

**1. Set up (one time)**

Install [Python](https://www.python.org/downloads/). Then download this repo (green **Code** button → **Download ZIP**), unzip it, open a terminal in the folder, and run:

```bash
pip install -r requirements.txt
```

On a Mac, use `pip3` and `python3` in place of `pip` and `python`.

**2. Clean a file**

```bash
python engine/reevo_clean.py "path/to/hubspot_export.csv"
```

CSV and Excel files both work, with the columns in any order.

**3. Get your results**

A new `output` folder appears with three files:

| File | What to do with it |
|---|---|
| `..._reevo_import.csv` | **Upload this to Reevo.** |
| `..._rejected.csv` | Rows that couldn't be imported, with the reason. Send back to the customer to fix. |
| `..._review.csv` | Rows that were imported but that the tool changed or guessed at. Skim before uploading. |

Want to see an example first? [`sample/output/`](sample/output/) has the results from the sample file.

## Optional: set a record owner

HubSpot exports often don't include an owner. To assign every contact and account to one Reevo user:

```bash
python engine/reevo_clean.py "path/to/hubspot_export.csv" --default-owner rep@company.com
```

## More detail

- [Cleaning rules](docs/cleaning_rules.md): exactly what the tool changes, and why
- [Open questions](docs/open_questions.md): what we'd confirm with the customer and with Reevo
