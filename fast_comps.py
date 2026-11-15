"""FAST mode: Codex (ChatGPT plan) searches the web for comps + ARV, like asking ChatGPT. No Claude, no API credit.
Returns STRUCTURED JSON (dict). Everything is UNVERIFIED (web pages, not confirmed sales).
Run: python fast_comps.py "<address>"   -> prints JSON"""
import json, os, subprocess, sys, tempfile

CODEX = os.environ.get("CODEX_BIN") or os.path.expanduser("~/.codex-cli/bin/codex")

PROMPT = """{addr} - find me comps for the property. Identify the subject property first (beds, baths, sqft, year built, lot size, condition if known).
Then give the best 4-6 SOLD comps: sold in the last 12 months, start within 0.2 miles and widen only if needed, similar beds/baths/sqft/style/condition.
For each comp give: address, sold price, sold date, beds, baths, sqft, distance in miles, condition (renovated/good/average/distressed/unknown), the source page URL, and verified=false unless you actually saw the sale price and date on the page.
Then ONE single best estimated ARV (whole dollars, your best judgment using all you know, not a range) and one short line why.
Rules: never invent a comp or a price. Use null for anything you could not confirm. If fewer than 4 good comps exist, return fewer and say so in notes (do not pad). Put caveats in notes."""

def _num(t): return {"type": ["number", "null"]}
def _str(): return {"type": ["string", "null"]}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["subject", "comps", "arv", "notes"],
    "properties": {
        "subject": {"type": "object", "additionalProperties": False,
                    "required": ["address", "beds", "baths", "sqft", "year_built", "lot_sqft", "condition"],
                    "properties": {"address": _str(), "beds": _num(0), "baths": _num(0), "sqft": _num(0),
                                   "year_built": _num(0), "lot_sqft": _num(0), "condition": _str()}},
        "comps": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["address", "sold_price", "sold_date", "beds", "baths", "sqft", "distance_mi", "condition", "url", "verified"],
            "properties": {"address": _str(), "sold_price": _num(0), "sold_date": _str(), "beds": _num(0), "baths": _num(0),
                           "sqft": _num(0), "distance_mi": _num(0), "condition": _str(), "url": _str(), "verified": {"type": "boolean"}}}},
        "arv": {"type": "object", "additionalProperties": False,
                "required": ["value", "reason"],
                "properties": {"value": _num(0), "reason": _str()}},
        "notes": _str(),
    },
}

def run(addr, timeout=150):
    """Returns dict: {"ok":True,"data":{...}} or {"ok":False,"error":"..."}"""
    with tempfile.TemporaryDirectory() as td:
        out, sch = os.path.join(td, "o.json"), os.path.join(td, "schema.json")
        json.dump(SCHEMA, open(sch, "w"))
        try:
            r = subprocess.run([CODEX, "--search", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                                "-C", td, "--output-schema", sch, "-o", out, "-"],
                               input=PROMPT.format(addr=addr), text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout"}
        if not os.path.exists(out):
            return {"ok": False, "error": ((r.stderr or r.stdout) or "no output")[-400:]}
        raw = open(out).read().strip()
        try:
            return {"ok": True, "data": json.loads(raw)}
        except Exception:
            return {"ok": False, "error": "bad JSON: " + raw[:300]}

if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), indent=2))
