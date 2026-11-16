"""FAST mode: Codex (ChatGPT plan) searches the web for comps + ARV, like asking ChatGPT. No Claude, no API credit.
Returns STRUCTURED JSON (dict). Everything is UNVERIFIED (web pages, not confirmed sales).
Run: python fast_comps.py "<address>"   -> prints JSON"""
import json, os, subprocess, sys, tempfile

CODEX = os.environ.get("CODEX_BIN") or os.path.expanduser("~/.codex-cli/bin/codex")

PROMPT = """{addr} - find me comps for the property. Identify the subject property first (beds, baths, sqft, year built, lot size, condition if known).
Then give the best 4-6 SOLD comps: sold in the last 12 months, start within 0.2 miles and widen only if needed, similar beds/baths/sqft/style/condition.
For each comp give: address, sold price, sold date, beds, baths, sqft, distance in miles, condition (renovated/good/average/distressed/unknown), the source page URL, and verified=false unless you actually saw the sale price and date on the page.
Then ONE single best estimated ARV (whole dollars, your best judgment using all you know, not a range) and one short line why.
ALSO REQUIRED, do not skip: search Zillow, Redfin and Realtor.com for homes CURRENTLY FOR SALE near the subject and return up to 5 ACTIVE listings (within about 1 mile; if fewer than 3 are found widen to 2 miles and say so in notes; similar beds/baths/sqft/style, currently on the market, not sold): for each give address, list price, beds, baths, sqft, distance in miles, days on market, listing status (active/pending/coming soon), and the listing page URL. They are only context for the buyer's competition and are NOT used for the ARV.
Rules: never invent a comp or a price. Use null for anything you could not confirm. If fewer than 4 good comps exist, return fewer and say so in notes (do not pad). Put caveats in notes."""

def _num(t): return {"type": ["number", "null"]}
def _str(): return {"type": ["string", "null"]}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["subject", "comps", "active", "arv", "notes"],
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
        "active": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["address", "list_price", "beds", "baths", "sqft", "distance_mi", "days_on_market", "status", "url"],
            "properties": {"address": _str(), "list_price": _num(0), "beds": _num(0), "baths": _num(0), "sqft": _num(0),
                           "distance_mi": _num(0), "days_on_market": _num(0), "status": _str(), "url": _str()}}},
        "arv": {"type": "object", "additionalProperties": False,
                "required": ["value", "reason"],
                "properties": {"value": _num(0), "reason": _str()}},
        "notes": _str(),
    },
}

def run(addr, extra=None, timeout=210):
    """Returns dict: {"ok":True,"data":{...}} or {"ok":False,"error":"..."}"""
    with tempfile.TemporaryDirectory() as td:
        out, sch = os.path.join(td, "o.json"), os.path.join(td, "schema.json")
        json.dump(SCHEMA, open(sch, "w"))
        prompt = PROMPT.format(addr=addr)
        if extra and extra.get("more"):
            have = "\n".join("- %s, sold $%s on %s, %s sqft" % (c.get("address"), c.get("price"), c.get("date"), c.get("sqft")) for c in (extra.get("exclude") or [])[:14])
            prompt += ("\n\nTHIS IS A REQUEST FOR MORE COMPS. These comps were already found, do NOT repeat them:\n" + have +
                       "\nFind up to 6 ADDITIONAL good sold comps. You may widen the search to 0.5 mile and 18 months, and say so in notes. "
                       "Return only the NEW comps in the comps list, and an empty active list. Then give ONE best estimated ARV that considers both the comps above and your new ones.")
        try:
            r = subprocess.run([CODEX, "--search", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                                "-C", td, "--output-schema", sch, "-o", out, "-"],
                               input=prompt, text=True, capture_output=True, timeout=timeout)
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
