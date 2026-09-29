# HubSpot → Reevo Import Cleaner

![tests](https://github.com/Shmoles/reevo-import-cleaner/actions/workflows/tests.yml/badge.svg)

Give it a customer's HubSpot export and it gives back a file that's ready to upload to Reevo.

**Open the tool: https://shmoles.github.io/reevo-import-cleaner/**

<!-- VIDEO WALKTHROUGH: add here -->

## How to use it

1. **Open the tool** at the link above. It loads with a sample file so you can see what it does.
2. **Drop in the customer's HubSpot export** (.csv or .xlsx). Columns can be in any order.
3. **Optional:** enter a default record owner email if the export doesn't have owners.
4. **Download the import file** and upload it to Reevo.

You also get two more files to download:

| File | What to do with it |
|---|---|
| Rejected rows | Rows that couldn't be imported, with the reason. Send back to the customer to fix. |
| Review notes | Rows that were imported but that the tool changed or guessed at. Skim before uploading. |

The file is processed inside your browser tab. It is never uploaded anywhere.

**If a column isn't recognized**, the tool tells you which Reevo field is missing. Pick the right column from the dropdown in **Column mapping** and the results update right away.

## Command-line version

The website runs the same engine as [`engine/reevo_clean.py`](engine/reevo_clean.py), which can also be run directly. This is useful for scripting or very large files.

```bash
pip install -r requirements.txt
python engine/reevo_clean.py "path/to/hubspot_export.csv" --default-owner rep@company.com
```

The three files are written to an `output` folder. [`sample/output/`](sample/output/) shows the results for the sample file.

## More detail

- [Cleaning rules](docs/cleaning_rules.md): exactly what the tool changes, and why
- [Open questions](docs/open_questions.md): what we'd confirm with the customer and with Reevo
