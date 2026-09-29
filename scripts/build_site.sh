#!/usr/bin/env bash
# Assemble the static website into _site/: the page, its worker, the SAME engine file the CLI uses, and the sample.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf _site && mkdir -p _site
cp web/index.html web/worker.js _site/
cp engine/reevo_clean.py _site/reevo_clean.py
cp sample/raw_data.csv _site/sample.csv
touch _site/.nojekyll
echo "Built _site/ ($(ls _site | wc -l | tr -d ' ') files)"
