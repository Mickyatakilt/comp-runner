"""FAST mode: Codex (ChatGPT plan) searches the web for comps + ARV, like asking ChatGPT. No Claude, no API credit.
Returns STRUCTURED JSON (dict). Everything is UNVERIFIED (web pages, not confirmed sales).
Run: python fast_comps.py "<address>"   -> prints JSON"""
import copy, json, os, subprocess, sys, tempfile, time

CODEX = os.environ.get("CODEX_BIN") or os.path.expanduser("~/.codex-cli/bin/codex")

PROMPT = """{addr} - find me comps for the property. Identify the subject property first (beds, baths, sqft, year built, lot size, condition if known).
For the subject give THREE size numbers when you can find them: sqft_public_record (county/tax record living area), sqft_listing (the number a Zillow/Redfin listing shows), and sqft (the one you consider right, above-grade living area if known). Also give style (one of: colonial, split_level, split_foyer, rambler_ranch, cape_cod, bungalow, contemporary, victorian, other, or null).
Then give the best 4-6 SOLD comps: sold in the last 12 months. SEARCH THE SUBJECT'S OWN STREET AND THE STREETS AROUND IT FIRST. Stay within 0.5 miles; our code measures every distance by map and THROWS AWAY anything farther, so far-away homes are wasted. Same house style as the subject where possible (set style on every comp), and sqft within about 25% of the subject's.
SOLD PRICE RULE: sold_price must be the RECORDED CLOSED SALE PRICE from the page's sale/price history (with its sale date). NEVER use a Redfin Estimate, Zestimate, tax value or list price. If a page only shows an estimate, skip that home. Real sale prices are usually round numbers (like $447,000 or $444,900); odd values like $432,221 are estimates and our code will discard them.
For each comp give: address, sold price, sold date, beds, baths, sqft, distance in miles, condition (renovated/good/average/distressed/unknown), the source page URL, and verified=false unless you actually saw the sale price and date on the page.
Then ONE single best estimated ARV (whole dollars, your best judgment using all you know, not a range) and one short line why.
ALSO REQUIRED, do not skip: search Zillow, Redfin and Realtor.com for homes CURRENTLY FOR SALE near the subject and return up to 5 ACTIVE listings (within about 1 mile; if fewer than 3 are found widen to 2 miles and say so in notes; similar beds/baths/sqft/style, currently on the market, not sold): for each give address, list price, beds, baths, sqft, distance in miles, days on market, listing status (active/pending/coming soon), and the listing page URL. They are only context for the buyer's competition and are NOT used for the ARV.
LISTING STATUS: for every sold comp set listing_status to the home's CURRENT status on Zillow/Redfin: sold (closed sale), active (still for sale), pending, coming_soon, or unknown. Prefer homes that are truly sold; never present an active listing as sold.
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
                    "required": ["address", "beds", "baths", "sqft", "year_built", "lot_sqft", "condition", "house_type", "style", "sqft_public_record", "sqft_listing"],
                    "properties": {"style": _str(), "sqft_public_record": _num(0), "sqft_listing": _num(0), "address": _str(), "beds": _num(0), "baths": _num(0), "sqft": _num(0),
                                   "year_built": _num(0), "lot_sqft": _num(0), "condition": _str(), "house_type": _ht()}},
        "comps": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["address", "sold_price", "sold_date", "beds", "baths", "sqft", "distance_mi", "condition", "url", "verified", "house_type", "listing_status", "style"],
            "properties": {"style": _str(), "address": _str(), "sold_price": _num(0), "sold_date": _str(), "beds": _num(0), "baths": _num(0),
                           "sqft": _num(0), "distance_mi": _num(0), "condition": _str(), "url": _str(), "verified": {"type": "boolean"}, "house_type": _ht(), "listing_status": {"type": ["string", "null"], "enum": ["sold", "active", "pending", "coming_soon", "unknown", None]}}}},
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


# ---------------- VET LAYER: hard rules applied in CODE, not by asking the AI nicely ----------------
import datetime, re, statistics
MAX_MI = 0.5            # sold comps must be within this many map-miles ...
WIDEN_MI = 1.0          # ... or this far only if fewer than MIN_GOOD survive (flagged "widened")
MIN_GOOD = 3
SQFT_BAND = 0.25        # comp sqft within +/-25% of the subject's
MONTHS = 12
WIDEN_MONTHS = 18
OUTLIER = 0.30          # $/sqft more than 30% off the median is left out of the ARV math (shown unticked)
NOT_SOLD = ("active", "pending", "coming_soon")

def _key(a):
    m = re.match(r"\s*(\d+[a-z]?)\s+([a-z0-9]+)", str(a or "").lower())
    return (m.group(1), m.group(2)) if m else None

def _date(v):
    v = str(v or "").strip()
    m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", v)
    if m: return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    for f in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try: return datetime.datetime.strptime(v, f).date()
        except Exception: pass
    return None

def _mi(c):
    try: return float(c.get("distance_mi"))
    except Exception: return None

TH = ("townhouse_end", "townhouse_interior")
def _check(c, subj, today):
    """Return (reasons_to_drop, soft_flags) for one sold comp."""
    drop, flag = [], []
    st, ct = subj.get("house_type"), c.get("house_type")
    if not c.get("address") or not c.get("sold_price"): drop.append("missing address or price")
    if c.get("listing_status") in NOT_SOLD: drop.append("not sold yet (%s)" % c.get("listing_status"))
    d = _date(c.get("sold_date"))
    if d is None: drop.append("no sold date")
    elif d > today: drop.append("sold date in the future")
    c["_age_mo"] = None if d is None else (today - d).days / 30.4
    dist = _mi(c)
    if dist is None: drop.append("distance unknown")
    elif c.get("dist_src") != "map": flag.append("distance unconfirmed (AI figure)")
    c["_dist"] = dist
    st, ct = subj.get("house_type"), c.get("house_type")
    if st and ct and st != ct:
        if st in TH and ct in TH and c.get("src_trusted"): pass          # real records say just "Townhouse": end vs interior unknown, allow
        elif st in TH and ct in TH: flag.append("%s (subject is %s)" % ("end unit" if ct == "townhouse_end" else "interior unit", "an end unit" if st == "townhouse_end" else "interior"))
        else: drop.append("different house type (%s vs %s)" % (ct, st))
    try:
        if not c.get("src_trusted") and c.get("sold_price") and float(c["sold_price"]) % 100 != 0: drop.append("price $%s looks like a site estimate, not a recorded sale" % format(int(float(c["sold_price"])), ","))
    except Exception: pass
    sq, cs = subj.get("sqft"), c.get("sqft")
    cands = subj.get("_cands") or ([sq] if sq else [])
    if cands and cs:
        if all(abs(cs - x) / x > SQFT_BAND for x in cands):
            try:
                tight = (c.get("src_trusted") and c.get("distance_mi") is not None and float(c["distance_mi"]) <= 0.25 and ct and st and (ct == st or (ct in TH and st in TH))
                         and subj.get("year_built") and c.get("year_built") and abs(subj["year_built"] - c["year_built"]) <= 3
                         and subj.get("beds") and c.get("beds") and abs(subj["beds"] - c["beds"]) <= 1)
            except Exception: tight = False
            if tight: flag.append("size differs (finished basement?), price used as-is"); c["_plain"] = True
            else: drop.append("size %s sqft vs subject %s" % (int(cs), "/".join(str(int(x)) for x in cands)))
    elif cands and not cs: flag.append("no sqft")
    elif sq and cs:
        pass
    elif not cs: flag.append("no sqft")
    ss, cst = subj.get("style"), c.get("style")
    c["_style_diff"] = bool(ss and cst and ss != cst and "other" not in (ss, cst))
    try:
        if subj.get("year_built") and c.get("year_built") and abs(float(subj["year_built"]) - float(c["year_built"])) > 15:
            c["_style_diff"] = True; c["_age_note"] = "built %d vs subject %d" % (c["year_built"], subj["year_built"])
    except Exception: pass
    if (c.get("condition") or "").lower() in ("distressed", "fixer", "poor"): flag.append("distressed sale")
    return drop, flag

def vet(subject_addr, data, extra=None):
    """Hard-filter comps/active listings, recompute ONE ARV from what survives. Mutates and returns data."""
    today = datetime.date.today()
    def _f(v):
        try: return float(str(v).replace(",", "").replace("$", "")) if v not in (None, "") else None
        except Exception: return None
    subj = data.get("subject") or {}
    for k in ("sqft", "sqft_public_record", "sqft_listing"): subj[k] = _f(subj.get(k))
    for c in data.get("comps") or []:
        if isinstance(c, dict):
            for k in ("sqft", "sold_price", "distance_mi"): c[k] = _f(c.get(k))
    extra = dict(extra or {}); extra["exclude"] = [dict(x, price=_f(x.get("price")), sqft=_f(x.get("sqft"))) for x in (extra.get("exclude") or []) if isinstance(x, dict)]
    skey = _key(subject_addr)
    sp0, sl0 = subj.get("sqft_public_record"), subj.get("sqft_listing")
    conflict0 = bool(sp0 and sl0 and abs(sp0 - sl0) / max(sp0, sl0) > 0.2)
    if conflict0: subj["_cands"] = sorted({x for x in (subj.get("sqft"), sp0, sl0) if x})
    dropped, pool = [], []
    for c in [x for x in (data.get("comps") or []) if isinstance(x, dict)]:
        if skey and _key(c.get("address")) == skey: dropped.append((c.get("address"), "is the subject itself")); continue
        c.pop("reject_default", None)
        reasons, flags = _check(c, subj, today)
        c["_flags"] = flags
        if reasons: dropped.append((c.get("address"), "; ".join(reasons))); continue
        pool.append(c)
    # distance tiers: 0.5 mi / 12 months first, widen only when too few
    def tier(mi, mo): return [c for c in pool if c["_dist"] is not None and c["_dist"] <= mi and (c["_age_mo"] is None or c["_age_mo"] <= mo)]
    keep, widened = tier(MAX_MI, MONTHS), None
    if len(keep) < MIN_GOOD:
        k2 = tier(WIDEN_MI, WIDEN_MONTHS)
        if len(k2) > len(keep):
            keep = k2
            far = any(c["_dist"] > MAX_MI for c in k2); old_ = any((c["_age_mo"] or 0) > MONTHS for c in k2)
            widened = " / ".join(x for x in ("up to %.1f mi" % WIDEN_MI if far else "", "up to %d months old" % WIDEN_MONTHS if old_ else "") if x)
    kept_ids = {id(c) for c in keep}
    for c in pool:
        if id(c) not in kept_ids:
            lim = WIDEN_MI if widened else MAX_MI
            if c["_dist"] is not None and c["_dist"] > lim: dropped.append((c.get("address"), "%s mi away (limit %.1f)" % (c["_dist"], lim)))
            else: dropped.append((c.get("address"), "sold too long ago (%d months)" % round(c["_age_mo"] or 0)))
    # soft: prefer same style, but never leave fewer than MIN_GOOD
    same = [c for c in keep if not c["_style_diff"]]
    if len(same) >= MIN_GOOD and len(same) < len(keep):
        for c in keep:
            if c["_style_diff"]: dropped.append((c.get("address"), "different age/style (%s)" % (c.get("_age_note") or "%s vs %s" % (c.get("style"), subj.get("style")))))
        keep = same
    keep.sort(key=lambda c: (c["_dist"], c["_age_mo"] or 0))
    keep = keep[:6]
    # ARV: median $/sqft of usable comps x subject sqft (distressed + outliers excluded), never the AI's guess
    ex = [x for x in ((extra or {}).get("exclude") or []) if isinstance(x, dict)]
    def ppsf(price, sqft):
        try: return float(price) / float(sqft)
        except Exception: return None
    usable = [c for c in keep if "distressed sale" not in c["_flags"] and ppsf(c.get("sold_price"), c.get("sqft"))]
    exp = [v for v in (ppsf(x.get("price"), x.get("sqft")) for x in ex) if v]
    allp = [ppsf(c["sold_price"], c["sqft"]) for c in usable if not c.get("_plain")] + exp
    med = statistics.median(allp) if allp else None
    for c in usable:
        if not c.get("_plain") and med and len(allp) >= 4 and abs(ppsf(c["sold_price"], c["sqft"]) - med) / med > OUTLIER:
            c["_flags"].append("price outlier"); c["reject_default"] = True
    basis = [c for c in usable if not c.get("reject_default")]
    bp = [ppsf(c["sold_price"], c["sqft"]) for c in basis if not c.get("_plain")] + exp
    plain_prices = [float(c["sold_price"]) for c in basis if c.get("_plain")]
    implied = ([v * subj["sqft"] for v in bp] if subj.get("sqft") else []) + plain_prices      # each comp's implied value for the subject
    ai_arv = (data.get("arv") or {}).get("value")
    arv, why, n = None, "", len(bp) + len(plain_prices)
    pr_all = [c["sold_price"] for c in basis if c.get("sold_price")] + [float(x["price"]) for x in ex if x.get("price")]
    if conflict0 and len(pr_all) >= 2:
        arv = round(statistics.median(pr_all) / 1000.0) * 1000
        why = "Subject size is unclear (public record %d vs listing %d sqft), so this is the plain median sold price of %d comps, NOT size-adjusted." % (sp0, sl0, len(pr_all))
    elif n < 2 and (bp or keep):
        why = "Only %d usable close comp(s), too few to set an ARV. Use More comps or enter your own ARV." % n
    elif implied and subj.get("sqft"):
        arv = round(statistics.median(implied) / 1000.0) * 1000
        why = "Median of %d sold comps within %.1f mi, each scaled to the subject's %d sqft%s." % (len(implied), widened and WIDEN_MI or MAX_MI, subj["sqft"], (" (%d same-block comps with a different size used at their sale price)" % len(plain_prices)) if plain_prices else "")
    elif len(keep) >= 2:
        pr = [c["sold_price"] for c in keep if c.get("sold_price")]
        if pr and not subj.get("sqft"): arv = round(statistics.median(pr) / 1000.0) * 1000; why = "Median sold price of %d comps (subject sqft unknown, so not size-adjusted)." % len(pr)
    if ai_arv and arv and abs(ai_arv - arv) / arv > 0.15: why += " (The AI's own guess of $%s was ignored.)" % format(int(ai_arv), ",")
    nuse = max(len(basis), n); level = "high" if (nuse >= 4 and not widened and not conflict0) else ("medium" if nuse >= MIN_GOOD else "low")
    if not arv and not why: why = "No usable close comps, so no ARV was set. Add an ARV by hand or try More comps."
    elif level == "low" and arv: why += " LOW CONFIDENCE: only %d close comp(s)." % nuse
    data["arv"] = {"value": arv, "reason": why}
    for c in keep: c["distance_mi"] = c["_dist"]; c["flags"] = c["_flags"]
    # active listings: not the subject, not a different type, within 2 mi, no duplicates of sold comps
    kept_addr = {_key(c.get("address")) for c in keep}
    act = []
    for a in [x for x in (data.get("active") or []) if isinstance(x, dict)]:
        if skey and _key(a.get("address")) == skey: continue
        if subj.get("house_type") and a.get("house_type") and a["house_type"] != subj["house_type"]: continue
        if _key(a.get("address")) in kept_addr: continue
        m = _mi(a)
        if m is not None and m > 2.0: continue
        act.append(a)
    data["active"] = act[:5]
    sp, sl = subj.get("sqft_public_record"), subj.get("sqft_listing")
    conflict = None
    if sp and sl and abs(sp - sl) / max(sp, sl) > 0.2:
        conflict = "Size sources disagree: public record %d sqft vs listing %d sqft. Confirm above-grade size before trusting the ARV." % (sp, sl)
    for c in keep:
        for k in [k for k in c if k.startswith("_")]: del c[k]
    subj.pop("_cands", None)
    data["comps"] = keep
    data["quality"] = {"level": level, "usable": nuse, "radius_mi": max([c["distance_mi"] for c in keep] + [0]) if keep and widened else MAX_MI, "widened": widened,
                       "sqft_conflict": conflict, "ai_arv": ai_arv,
                       "dropped": [{"address": a, "why": w} for a, w in dropped][:12]}
    n_drop = len(dropped)
    note = "Checked by code: %d comp(s) kept, %d removed (too far, wrong size or type, not sold, or the subject itself)." % (len(keep), n_drop)
    if widened: note += " Search was widened to " + widened + " because fewer than %d were closer." % MIN_GOOD
    if conflict: note += " " + conflict
    data["notes"] = (note + " " + (data.get("notes") or ""))[:1200]
    return data


# ---------------- REAL SOLD RECORDS (Redfin/MLS via Apify, pennies per lookup) ----------------
APIFY_ACTOR = "swerve~redfin-sold-comps"
APIFY_MAX_ITEMS = 40            # ~$0.20 max per search at $0.005/result
def _apify_rows(addr, radius, window, token, timeout=90):
    """Sold homes around an address from real records. Returns list of rows or None on any failure."""
    url = "https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items?timeout=80&maxTotalChargeUsd=0.30" % APIFY_ACTOR
    body = {"addresses": [addr], "radiusMiles": radius, "soldWindow": window, "maxItems": APIFY_MAX_ITEMS}
    with tempfile.TemporaryDirectory() as td:
        hf = os.path.join(td, "h"); open(hf, "w").write("Authorization: Bearer " + token + "\nContent-Type: application/json\n"); os.chmod(hf, 0o600)
        try:
            r = subprocess.run(["curl", "-sS", "-m", str(timeout), "-X", "POST", "-H", "@" + hf, "--data-binary", "@-", url],
                               input=json.dumps(body), text=True, capture_output=True, timeout=timeout + 10)
            rows = json.loads(r.stdout)
            return rows if isinstance(rows, list) else None
        except Exception:
            return None

def _apify_comp(r):
    t = (r.get("propertyType") or "").lower()
    ht = "sfh_detached" if "single" in t else ("townhouse_interior" if "town" in t else ("condo_coop" if "condo" in t else ("land" if "land" in t or "vacant" in t else ("manufactured" if "manufact" in t or "mobile" in t else None))))
    if not r.get("soldPrice") or not r.get("soldDate") or not r.get("streetAddress"): return None
    a = r["streetAddress"] + ((" " + r["unitNumber"]) if r.get("unitNumber") and r["unitNumber"] not in r["streetAddress"] else "")
    return {"address": "%s, %s, %s %s" % (a, r.get("city") or "", r.get("state") or "", r.get("zip") or ""), "sold_price": r["soldPrice"], "sold_date": r["soldDate"],
            "beds": r.get("beds"), "baths": r.get("baths"), "sqft": r.get("sqFt"), "distance_mi": r.get("distanceMiles"), "dist_src": "map",
            "condition": None, "url": r.get("url"), "verified": True, "house_type": ht, "listing_status": "sold", "style": None,
            "year_built": r.get("yearBuilt"), "src_trusted": True, "source": "redfin-" + str(r.get("saleSource") or "record")}

def real_comps(addr, radius, window, token):
    rows = _apify_rows(addr, radius, window, token)
    if rows is None: return None
    return [c for c in (_apify_comp(r) for r in rows if isinstance(r, dict)) if c]

def _codex(prompt, timeout):
    """One Codex web-search call. Returns (data, error)."""
    with tempfile.TemporaryDirectory() as td:
        out, sch = os.path.join(td, "o.json"), os.path.join(td, "schema.json")
        json.dump(SCHEMA, open(sch, "w"))
        try:
            r = subprocess.run([CODEX, "--search", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                                "-C", td, "--output-schema", sch, "-o", out, "-"],
                               input=prompt, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "timeout"
        if not os.path.exists(out):
            return None, ((r.stderr or r.stdout) or "no output")[-400:]
        raw = open(out).read().strip()
        try: return json.loads(raw), None
        except Exception: return None, "bad JSON: " + raw[:300]

def run(addr, extra=None, timeout=210):
    """Returns dict: {"ok":True,"data":{...}} or {"ok":False,"error":"..."}.
    Pass 1 = AI web search. Then VET (hard filters + our own ARV). If fewer than MIN_GOOD good comps survive,
    ONE focused retry pass (told which comps were rejected and why), then vet the combined pool.
    Whole thing must finish inside the runner's 6-minute stale limit, so the retry is skipped when time is short."""
    t0 = time.time()
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
                   "\nFind up to 6 ADDITIONAL good sold comps. You may widen the search to 0.75 mile and 18 months, and say so in notes. "
                   "Return only the NEW comps in the comps list, and an empty active list. Then give ONE best estimated ARV that considers both the comps above and your new ones.")
    data, err = _codex(prompt, timeout)
    if data is None: return {"ok": False, "error": err}
    try: measure_distances(addr, data)
    except Exception: pass
    tok = os.environ.get("APIFY_TOKEN", "").strip()
    real = None
    if tok:
        more = bool((extra or {}).get("more"))
        real = real_comps(addr, 1.0 if more else 0.5, "sold-2yr" if more else "sold-1yr", tok)
        if real is not None:
            data["comps"] = real
            data["notes"] = "Comps are real sold records (Redfin/MLS). " + (data.get("notes") or "")
        else:
            data["notes"] = "Real sold records were unavailable this time, so these comps come from a web search and are less reliable. " + (data.get("notes") or "")
    raw_comps = list(data.get("comps") or [])
    try:
        import copy
        first = copy.deepcopy(data)
        vet(addr, data, extra)
    except Exception as e:           # never lose a result because the checker broke; say so loudly
        data["notes"] = "WARNING: the automatic comp checker failed (%r), so these comps are UNCHECKED. Verify distance and size by hand. %s" % (e, data.get("notes") or "")
        return {"ok": True, "data": data}
    q = data.get("quality") or {}
    if real is not None:
        q["source"] = "redfin"
        if q.get("usable", 0) < MIN_GOOD and not (extra or {}).get("more"):      # widen once: 1 mile, 2 years (vet still caps age at 18 months)
            more_rows = real_comps(addr, 1.0, "sold-2yr", tok)
            if more_rows:
                seen = {_key(c.get("address")) for c in raw_comps}
                raw_comps += [c for c in more_rows if _key(c.get("address")) not in seen]
                combo = copy.deepcopy(first); combo["comps"] = raw_comps
                try:
                    vet(addr, combo, extra); combo["quality"]["source"] = "redfin"; data = combo
                except Exception: pass
        return {"ok": True, "data": data}
    if q.get("usable", 0) < MIN_GOOD and time.time() - t0 < 200 and not (extra or {}).get("more"):
        rej = "\n".join("- %s: %s" % (d["address"], d["why"]) for d in q.get("dropped", []))
        have = "\n".join("- %s" % c.get("address") for c in data.get("comps", []))
        p2 = (PROMPT.format(addr=addr) + "\n\nSECOND PASS. Our checker removed these results (our code measures distance by map and enforces the limits):\n" + (rej or "(none)") +
              "\nKept so far (do not repeat):\n" + (have or "(none)") +
              "\nNow find MORE sold comps that pass: within 0.5 mile of the subject (look at the subject's own street and the immediately neighboring streets), sold in the last 12 months, same house type and style, "
              "sqft within 25% of the subject. Zillow, Redfin and Realtor.com recently-sold maps are good sources. Return fewer rather than far-away homes. Return an empty active list.")
        d2, _e = _codex(p2, max(60, min(110, int(300 - (time.time() - t0)))))
        if d2:
            try:
                measure_distances(addr, d2)
                seen = {_key(c.get("address")) for c in raw_comps}
                raw_comps += [c for c in (d2.get("comps") or []) if isinstance(c, dict) and _key(c.get("address")) not in seen]
                combo = copy.deepcopy(first); combo["comps"] = raw_comps
                combo["subject"] = first.get("subject") or d2.get("subject")
                vet(addr, combo, extra); combo["quality"]["second_pass"] = True
                data = combo
            except Exception:
                pass
    return {"ok": True, "data": data}

if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), indent=2))
