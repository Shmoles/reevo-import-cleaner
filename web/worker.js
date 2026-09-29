/* Runs the Python cleaning engine (engine/reevo_clean.py) inside the browser with Pyodide.
   The customer's file is read and cleaned here, in this tab. Nothing is uploaded anywhere. */

const DEFAULT_PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const DEFAULT_XLSX = "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js";

let py = null;
let xlsxLoaded = false;
let xlsxUrl = DEFAULT_XLSX;

const GLUE = `
import json, time
import reevo_clean as rc

ROWS = []
FILE_STEM = "import"

def load_text(text, stem):
    global ROWS, FILE_STEM
    ROWS, FILE_STEM = rc.parse_text(text), stem
    return _describe()

def load_rows(rows, stem):
    global ROWS, FILE_STEM
    ROWS = [["" if c is None else str(c) for c in r] for r in rows]
    FILE_STEM = stem
    return _describe()

def _describe():
    headers = [rc.tidy(h) for h in ROWS[0]] if ROWS else []
    return json.dumps({"headers": headers, "rows": max(len(ROWS) - 1, 0)})

def run(owner, mapping_json, preview):
    t0 = time.perf_counter()
    mapping = json.loads(mapping_json) or None
    try:
        out, rejected, review, warnings, dups = rc.process(ROWS, owner, mapping)
    except SystemExit as e:
        return json.dumps({"error": str(e), "mapping": rc.column_report(ROWS[0], mapping),
                           "auto_mapping": rc.column_report(ROWS[0], None)})
    review_all = rc.with_file_level(review, warnings)
    reasons = {}
    for r in rejected:
        for reason in r["reject_reason"].split("; "):
            k = reason.split(":")[0]
            reasons[k] = reasons.get(k, 0) + 1
    auto = rc.column_report(ROWS[0], None)
    fn = (auto["contact_first_name"] or [None])[0]
    ln = (auto["contact_last_name"] or [None])[0]
    ms = (time.perf_counter() - t0) * 1000
    return json.dumps({
        "stem": FILE_STEM,
        "columns": rc.OUTPUT_COLUMNS,
        "mapping": rc.column_report(ROWS[0], mapping),
        "auto_mapping": auto,
        "counts": {"read": len(out) + len(rejected) + dups, "ready": len(out),
                   "rejected": len(rejected), "duplicates": dups, "review": len(review)},
        "reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
        "warnings": warnings,
        "ready_preview": out[:preview],
        "rejected_preview": [{"source_row": r["source_row"], "reject_reason": r["reject_reason"],
                              "name": " ".join(x for x in (r.get(fn, ""), r.get(ln, "")) if x and x.strip())}
                             for r in rejected[:preview]],
        "review_preview": review_all[:preview],
        "review_index": [[r["source_row"], r["field"], r["issue"]] for r in review],
        "csv": {
            "import": rc.to_csv_text(out, rc.OUTPUT_COLUMNS),
            "rejected": rc.to_csv_text(rejected, None if rejected else ["source_row", "reject_reason"]),
            "review": rc.to_csv_text(review_all, rc.REVIEW_COLUMNS),
        },
        "ms": ms,
    })
`;

async function init({ pyodideBase, xlsx }) {
  const t0 = performance.now();
  if (xlsx) xlsxUrl = xlsx;
  importScripts((pyodideBase || DEFAULT_PYODIDE) + "pyodide.js");
  py = await loadPyodide({ indexURL: pyodideBase || DEFAULT_PYODIDE });
  const src = await (await fetch("reevo_clean.py", { cache: "no-cache" })).text();
  py.FS.writeFile("/home/pyodide/reevo_clean.py", src);
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
  py.runPython(GLUE);
  return { loadMs: Math.round(performance.now() - t0) };
}

function decodeText(buffer) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return new TextDecoder("windows-1252").decode(buffer);
  }
}

function load({ name, buffer }) {
  const stem = name.replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_") || "import";
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["xlsx", "xlsm", "xls"].includes(ext)) {
    if (!xlsxLoaded) { importScripts(xlsxUrl); xlsxLoaded = true; }
    const wb = XLSX.read(buffer, { type: "array" });
    const ws = wb.Sheets[wb.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json(ws, { header: 1, raw: false, defval: "" });
    const pyRows = py.toPy(rows);
    try { return JSON.parse(py.globals.get("load_rows")(pyRows, stem)); } finally { pyRows.destroy(); }
  }
  return JSON.parse(py.globals.get("load_text")(decodeText(buffer), stem));
}

function run({ owner, mapping, preview }) {
  return JSON.parse(py.globals.get("run")(owner || "", JSON.stringify(mapping || {}), preview || 200));
}

self.onmessage = async (e) => {
  const { id, type, payload } = e.data;
  try {
    let result;
    if (type === "init") result = await init(payload);
    else if (type === "load") result = load(payload);
    else if (type === "run") result = run(payload);
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String((err && err.message) || err) });
  }
};
