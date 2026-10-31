"""Trigger the ingestion workflow (optionally for some tickers) and wait for it. Usage: N8N_API_KEY=... python3 run_ingest.py [TICKER ...]"""
import json, os, sys, time, urllib.request
K, T = os.environ["N8N_API_KEY"], json.load(open("ingest_target.json"))
def api(p, m="GET", d=None):
    r = urllib.request.Request("http://127.0.0.1:5678/api/v1" + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers={"X-N8N-API-KEY": K, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))
before = {e["id"] for e in api(f"/executions?workflowId={T['id']}&limit=50")["data"]}
api(f"/workflows/{T['id']}/activate", "POST"); time.sleep(2)
try:
    body = json.dumps({"tickers": sys.argv[1:]}).encode()
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:5678/webhook/{T['path']}", data=body, headers={"Content-Type": "application/json"}, method="POST"), timeout=30)
    ex = None
    for _ in range(170):                                   # up to ~28 min
        time.sleep(10)
        new = [e for e in api(f"/executions?workflowId={T['id']}&limit=50")["data"] if e["id"] not in before]
        if new and new[0]["status"] not in ("running", "new", "waiting"): ex = new[0]; break
finally:
    api(f"/workflows/{T['id']}/deactivate", "POST")
if not ex: print("timed out"); sys.exit(1)
full = api(f"/executions/{ex['id']}?includeData=true"); rd = full["data"]["resultData"]["runData"]
print("execution", ex["id"], ex["status"], "| seconds:", round((time.mktime(time.strptime(ex["stoppedAt"][:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.strptime(ex["startedAt"][:19], "%Y-%m-%dT%H:%M:%S")))))
cnt = lambda n: sum(len(b) for r in rd.get(n, []) for b in r["data"]["main"]) if n in rd else None
print("filings selected:", cnt("Select Filings"), "| ok:", sum(1 for r in rd.get("OK?", []) for x in (r["data"]["main"][0] if r["data"]["main"] else [])), "| skipped:", cnt("Skipped"), "| index runs:", len(rd.get("Index SEC Filings", [])))
for r in rd.get("Download And Clean", []):
    for it in r["data"]["main"][0]: print("  ", it["json"]["ticker"], it["json"]["form"], it["json"]["filingDate"], "ok" if it["json"].get("ok") else "SKIP", it["json"].get("chars") or it["json"].get("reason"))
up = [it["json"] for r in rd.get("Index SEC Filings", []) for it in (r["data"]["main"][0] if r["data"]["main"] else [])]
bad = [u for u in up if u.get("statusCode", 0) >= 300]
print("upsert batches:", len(up), "| failed:", len(bad), "| docs upserted:", sum((u.get("body") or {}).get("upserted_count", 0) for u in up if u not in bad))
for u in bad[:5]: print("  UPSERT ERROR:", u.get("statusCode"), json.dumps(u.get("body"))[:300])
for r in rd.get("Index SEC Filings", []):
    if r.get("error"): print("  INDEX ERROR:", r["error"].get("message", "")[:200])
if full["data"]["resultData"].get("error"): print("ERROR:", full["data"]["resultData"]["error"].get("message", "")[:300])
