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
HOUSE TYPE: for the subject, every comp and every for-sale listing, set house_type to exactly one of: land (vacant lot), manufactured (mobile/manufactured home), sfh_detached (single family, stand-alone), semi_detached (shares one wall with one neighbor), townhouse_end (row/townhouse end unit), townhouse_interior (row/townhouse interior unit), condo_coop (condo, co-op, apartment style). Use null if you cannot tell. Comps and for-sale listings should be the SAME house type as the subject; do not mix types.
Rules: never invent a comp or a price. Use null for anything you could not confirm. If fewer than 4 good comps exist, return fewer and say so in notes (do not pad). Put caveats in notes."""

TYPES = ["land", "manufactured", "sfh_detached", "semi_detached", "townhouse_end", "townhouse_interior", "condo_coop"]
LABELS = {"land": "Land (vacant lot)", "manufactured": "Manufactured house", "sfh_detached": "SFH detached", "semi_detached": "Semi-detached",
          "townhouse_end": "Townhouse / row, end unit", "townhouse_interior": "Townhouse / row, interior unit", "condo_coop": "Condo / Co-op"}
def _ht(): return {"type": ["string", "null"], "enum": TYPES + [None]}

def _num(t): return {"type": ["number", "null"]}
def _str(): return {"type": ["string", "null"]}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["subject", "comps", "active", "arv", "notes"],
    "properties": {
        "subject": {"type": "object", "additionalProperties": False,
                    "required": ["address", "beds", "baths", "sqft", "year_built", "lot_sqft", "condition", "house_type"],
                    "properties": {"address": _str(), "beds": _num(0), "baths": _num(0), "sqft": _num(0),
                                   "year_built": _num(0), "lot_sqft": _num(0), "condition": _str(), "house_type": _ht()}},
        "comps": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["address", "sold_price", "sold_date", "beds", "baths", "sqft", "distance_mi", "condition", "url", "verified", "house_type"],
            "properties": {"address": _str(), "sold_price": _num(0), "sold_date": _str(), "beds": _num(0), "baths": _num(0),
                           "sqft": _num(0), "distance_mi": _num(0), "condition": _str(), "url": _str(), "verified": {"type": "boolean"}, "house_type": _ht()}}},
        "active": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["address", "list_price", "beds", "baths", "sqft", "distance_mi", "days_on_market", "status", "url", "house_type"],
            "properties": {"address": _str(), "list_price": _num(0), "beds": _num(0), "baths": _num(0), "sqft": _num(0),
                           "distance_mi": _num(0), "days_on_market": _num(0), "status": _str(), "url": _str(), "house_type": _ht()}}},
        "arv": {"type": "object", "additionalProperties": False,
                "required": ["value", "reason"],
                "properties": {"value": _num(0), "reason": _str()}},
        "notes": _str(),
    },
}

def _geo(addr):
    """Latitude/longitude from the free US Census geocoder (curl: macOS python lacks CA certs). None if not found."""
    import urllib.parse
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?benchmark=Public_AR_Current&format=json&address=" + urllib.parse.quote(addr or "")
    for _ in range(2):
        try:
            r = subprocess.run(["curl", "-sS", "-m", "12", url], capture_output=True, text=True)
            m = json.loads(r.stdout)["result"]["addressMatches"]
            if m: return (m[0]["coordinates"]["y"], m[0]["coordinates"]["x"])
            return None
        except Exception:
            continue
    return None

def _miles(a, b):
    import math
    p1, p2 = math.radians(a[0]), math.radians(b[0]); dp = p2 - p1; dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))

def measure_distances(subject_addr, data):
    """Replace the AI's guessed distances with miles measured between map points. Keeps the AI value (dist_src='ai') if an address can't be placed."""
    from concurrent.futures import ThreadPoolExecutor
    rows = [c for c in (data.get("comps") or []) if isinstance(c, dict)] + [c for c in (data.get("active") or []) if isinstance(c, dict)]
    addrs = [subject_addr] + [r.get("address") for r in rows]
    with ThreadPoolExecutor(6) as ex: pts = list(ex.map(_geo, addrs))
    base = pts[0]
    for r, pt in zip(rows, pts[1:]):
        if base and pt:
            r["distance_mi"] = round(_miles(base, pt), 2); r["dist_src"] = "map"
        else:
            r["dist_src"] = "ai"

def run(addr, extra=None, timeout=210):
    """Returns dict: {"ok":True,"data":{...}} or {"ok":False,"error":"..."}"""
    with tempfile.TemporaryDirectory() as td:
        out, sch = os.path.join(td, "o.json"), os.path.join(td, "schema.json")
        json.dump(SCHEMA, open(sch, "w"))
        prompt = PROMPT.format(addr=addr)
        ht = (extra or {}).get("house_type") or (extra or {}).get("known_type")
        if ht in LABELS:
            prompt += ("\n\nHOUSE TYPE CONFIRMED BY THE USER: the subject is a %s (house_type=%s). Search ONLY for sold comps and for-sale listings of this same type. "
                       "Do not return other types. If you cannot find enough of this type, return fewer and say so in notes." % (LABELS[ht], ht))
        les = (extra or {}).get("lessons") or []
        if les:
            prompt += "\n\nLEARN FROM PAST CORRECTIONS by our team of the house type (apply the same judgment to similar homes):\n" + "\n".join(
                "- %s (%s bd/%s ba, %s sqft): the AI said %s, the correct type is %s" % (l.get("address"), l.get("beds"), l.get("baths"), l.get("sqft"),
                                                                                       LABELS.get(l.get("ai_type"), l.get("ai_type") or "unknown"), LABELS.get(l.get("user_type"), l.get("user_type")))
                for l in les[:12])
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
            data = json.loads(raw)
            try: measure_distances(addr, data)
            except Exception: pass
            return {"ok": True, "data": data}
        except Exception:
            return {"ok": False, "error": "bad JSON: " + raw[:300]}

if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), indent=2))
