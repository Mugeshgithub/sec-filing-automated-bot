"""Run questions.json through the eval webhook and score the answers. Usage: N8N_API_KEY=... python3 run_eval.py"""
import json, os, re, subprocess, time, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:5678"
KEY = os.environ["N8N_API_KEY"]
W = json.load(open(f"{HERE}/eval_target.json"))            # worker pipeline
T = json.load(open(f"{HERE}/orch_target.json")) if os.environ.get("TARGET") == "orch" else W
if os.environ.get("TARGET") == "agent": T = W = json.load(open(f"{HERE}/agent_target.json"))   # AI Agent experiment
UA = "SEC-RAG-Bot your-name your-email@example.com"   # same identity the workflow uses
CIK = {"AAPL": 320193, "NVDA": 1045810}
REFUSE = ["can't","cannot","can’t","unable","not able","don't have","do not have","couldn't","could not","no information",
          "not available","outside","not covered","isn't covered","won't","doesn't","does not","not confirm","unconfirmed","no data",
          "no figure","hasn't been reported","has not been reported","not been reported","not provide","isn't something","only cover","only answer","not financial advice","not investment advice"]

def api(path, method="GET", data=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(data).encode() if data else None,
        headers={"X-N8N-API-KEY": KEY, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))

def truth(spec):
    raw = subprocess.run(["curl", "-s", "-H", f"User-Agent: {UA}",
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK[spec['ticker']]:010d}.json"], capture_output=True, check=True).stdout
    d = json.loads(raw)["facts"]["us-gaap"]
    best = None
    for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"):
        for f in d.get(tag, {}).get("units", {}).get("USD", []):
            s, e = datetime.date.fromisoformat(f["start"]), datetime.date.fromisoformat(f["end"]); days = (e - s).days
            ok = (340 <= days <= 380 and f.get("form") == "10-K") if spec["period"] == "annual" else (80 <= days <= 100)
            if ok and (best is None or f["end"] > best["end"]): best = {**f, "tag": tag}
    return best

def numbers_in(text):
    out = []
    for m in re.finditer(r"\$?\s?([\d][\d,]*\.?\d*)\s*(trillion|billion|million|bn|b|m)?\b", text, re.I):
        try: v = float(m.group(1).replace(",", ""))
        except ValueError: continue
        u = (m.group(2) or "").lower()
        v *= {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "m": 1e6}.get(u, 1)
        out.append(v)
    return out

def score(q, r, tr):
    a, low, checks = r.get("answer", ""), r.get("answer", "").lower(), {}
    checks["route"] = (r.get("route") in q["route"])
    exp = q.get("expect")
    if "truth" in q:
        checks["number_correct"] = bool(tr) and any(abs(v - tr["val"]) / tr["val"] < 0.005 for v in numbers_in(a))
    if exp in ("refuse", "refuse_or_web"):
        checks["refuses_or_flags"] = any(m in low for m in REFUSE) or (exp == "refuse_or_web" and "not from official" in low)
    if exp == "out_of_scope":
        checks["took_out_of_scope_path"] = (r.get("mode") == "out_of_scope")
    if exp == "cited": checks["answered_from_filings"] = (r.get("mode") == "kb")
    if exp == "web": checks["flags_not_official"] = "not" in low and ("official" in low or "filing" in low)
    if exp == "short": checks["short"] = len(a) < 1500
    if exp == "compound":                       # orchestrator must split, route each part, and merge
        parts = r.get("parts") or []
        checks["split"] = len(parts) >= q.get("min_parts", 2)
        checks["complete"] = a.rstrip()[-1:] in ".!?)%\""      # not cut off mid-sentence
        for need in q.get("need_routes", []): checks[f"part_{need}"] = any(p.get("route") == need for p in parts)
        for word in q.get("must_mention", []): checks[f"mentions_{word}"] = word.lower() in low
        checks["has_figure"] = bool(numbers_in(a)) if q.get("needs_figure") else True
    for bad in q.get("forbid", []): checks.setdefault("no_advice", True); checks["no_advice"] &= bad not in low
    return checks

was_active = {wf_id: api(f"/api/v1/workflows/{wf_id}")["active"] for wf_id in {W["id"], T["id"]}}   # production workflows stay active after a run
for wf_id, on in was_active.items():
    if not on: api(f"/api/v1/workflows/{wf_id}/activate", "POST")
time.sleep(2)
results = []
try:
    for q in [x for x in json.load(open(f"{HERE}/questions.json")) if not os.environ.get("ONLY") or x["id"] in os.environ["ONLY"].split(",")]:
        tr = None
        if "truth" in q:
            f = truth(q["truth"]); tr = {"val": f["val"], "end": f["end"], "tag": f["tag"]} if f else None
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{BASE}/webhook/{T['path']}", data=json.dumps({"question": q["q"]}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            r = json.load(urllib.request.urlopen(req, timeout=180)); err = None
        except Exception as e: r, err = {}, str(e)[:200]
        c = score(q, r, tr) if not err else {"ran": False}
        results.append({"id": q["id"], "cat": q["cat"], "q": q["q"], "seconds": round(time.time() - t0, 1), "error": err,
                        "route": r.get("route"), "mode": r.get("mode"), "kbScore": r.get("kbScore"), "webScore": r.get("webScore"),
                        "truth": tr, "parts": r.get("parts"), "checks": c, "passed": bool(c) and all(c.values()), "answer": r.get("answer", "")})
        print(f"{q['id']:3} {'PASS' if results[-1]['passed'] else 'FAIL'}  route={r.get('route')}  {results[-1]['seconds']}s  {c}", flush=True)
finally:
    for wf_id, on in was_active.items():
        if not on: api(f"/api/v1/workflows/{wf_id}/deactivate", "POST")
json.dump(results, open(f"{HERE}/results-{datetime.datetime.now():%Y%m%d-%H%M}.json", "w"), indent=1, ensure_ascii=False)
n = sum(r["passed"] for r in results); print(f"\nBASELINE: {n}/{len(results)} passed")
