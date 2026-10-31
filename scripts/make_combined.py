"""Build ONE workflow with everything on one canvas: chat/orchestrator chain on top, worker chain below.
The orchestrator calls the worker's own webhook (inside this same workflow) once per sub-question, exactly as in the tested split design.
Usage: N8N_API_KEY=... python3 make_combined.py [--telegram]   (re-run to update; --telegram adds the Telegram Trigger, only do that after the old bot workflow is deactivated)"""
import json, os, re, sys, uuid, urllib.request
K = os.environ["N8N_API_KEY"]; WITH_TG = "--telegram" in sys.argv
def api(p, m="GET", d=None):
    r = urllib.request.Request("http://127.0.0.1:5678/api/v1" + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers={"X-N8N-API-KEY": K, "Content-Type": "application/json"})
    try: return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e: raise SystemExit(f"HTTP {e.code} on {m} {p}: {e.read().decode()[:500]}")
SEP = {"worker": "JWmbkRV1qbQ9o5tx", "orch": "YAQxUiKkl7pHlt1E", "tg": "TYYqRQ2xtP43ffPu"}
W, O, TG = (api("/workflows/" + SEP[k]) for k in ("worker", "orch", "tg"))
wn = {n["name"]: n for n in W["nodes"] if "stickyNote" not in n["type"]}
on = {n["name"]: n for n in O["nodes"] if "stickyNote" not in n["type"]}
tn = {n["name"]: n for n in TG["nodes"] if "stickyNote" not in n["type"]}
try: prev = json.load(open("combined_target.json"))
except FileNotFoundError: prev = None
OPATH = prev["orch_path"] if prev else "orch-" + uuid.uuid4().hex[:16]
WPATH = prev["worker_path"] if prev else "worker-" + uuid.uuid4().hex[:16]
uid = lambda: str(uuid.uuid4()); S = 260; R2 = 900; WY = 1600   # three rows: Telegram on top, Orchestrator in the middle, Worker below
cp = lambda n: json.loads(json.dumps(n))

nodes = {}
# ---- chain A: chat in -> plan -> ask worker -> merge -> reply ----
def add(name, src, x, y, note=None, **patch):
    n = cp(src); n["name"] = name; n["position"] = [x, y]; n["id"] = uid()
    if note: n["notes"], n["notesInFlow"] = note, True
    n["parameters"].update(patch.get("parameters", {})); nodes[name] = n; return n
entry_ref = "$('Entry Question').first().json.question"
add("Orchestrator Webhook", on["Orchestrator Webhook"], 0, -170, "Entry for tests and API calls", parameters={"path": OPATH, "responseMode": "responseNode"})
nodes["Orchestrator Webhook"]["webhookId"] = uid()
entry = {"id": uid(), "name": "Entry Question", "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [S, 0], "notes": "Question text + chat id", "notesInFlow": True,
  "parameters": {"assignments": {"assignments": [
    {"id": uid(), "name": "question", "value": "={{ ($json.message?.text || $json.body?.question || '').trim() }}", "type": "string"},
    {"id": uid(), "name": "chatId", "value": "={{ $json.message?.chat?.id ?? '' }}", "type": "string"}]}, "options": {}}}
nodes["Entry Question"] = entry
for name, x, y in (("Plan", 260, R2), ("Parse Plan", 520, R2), ("Call Worker", 1040, R2), ("Combine", 1300, R2), ("Compound?", 1820, R2), ("Merge Answers", 2080, R2 - 170), ("Final", 2340, R2 - 170), ("Single Result", 2080, R2 + 170)):
    n = add(name, on[name], x, y, on[name].get("notes"))
    n["parameters"] = json.loads(json.dumps(n["parameters"]).replace("$('Orchestrator Webhook').first().json.body.question", entry_ref).replace("$('Orchestrator Webhook').first().json.body?.question", "$('Entry Question').first().json.question"))
nodes["Call Worker"]["parameters"]["url"] = f"http://127.0.0.1:5678/webhook/{WPATH}"
nodes["Call Worker"]["notes"] = "Runs the worker chain below, once per part"
nodes["Reply Router"] = {"id": uid(), "name": "Reply Router", "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [2600, 0], "notes": "Telegram chat? send reply. Else return JSON", "notesInFlow": True,
  "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
    "conditions": [{"id": uid(), "leftValue": "={{ $('Entry Question').first().json.chatId }}", "rightValue": "", "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}], "combinator": "and"}, "options": {}}}
send = add("Send Telegram Reply", tn["Send Telegram Reply"], 2860, -130, "Replies with answer + sources")
send["parameters"]["chatId"] = "={{ $('Entry Question').first().json.chatId }}"
send["parameters"]["text"] = ("={{ (() => { const r = $json; const rank = u => /sec\\.gov\\/Archives/.test(u) ? 0 : /data\\.sec\\.gov/.test(u) ? 2 : 1;"
  " const src = [...new Set(r.sources || [])].sort((a, b) => rank(a) - rank(b)).slice(0, 8);"
  " const tail = src.length ? '\\n\\nSources:\\n' + src.join('\\n') : '';"
  " return (String(r.answer || 'Sorry, something went wrong. Please try again.').trim() + tail).slice(0, 4000); })() }}")   # filings first, then web, then raw SEC data; up to 8 links
add("Respond Orchestrator", wn["Respond"], 2860, 170, "Returns JSON to the caller", parameters={"responseBody": "={{ JSON.stringify($json) }}"})
if WITH_TG: add("Telegram Trigger", tn["Telegram Trigger"], 0, 170, "A user message arrives")

# ---- chain B: worker ----
shift = lambda n, name: add(name, n, n["position"][0], n["position"][1] + WY, n.get("notes"))
for name, n in wn.items():
    if name in ("Eval Webhook",): continue
    shift(n, name)
add("Worker Entry", wn["Eval Webhook"], 0, WY, "Entry: receives {question} from Call Worker", parameters={"path": WPATH, "responseMode": "responseNode"})
nodes["Worker Entry"]["webhookId"] = uid()

# ---- connections ----
def conns_from(src_conn, rename=None):
    out = {}
    for a, v in src_conn.items(): out[(rename or {}).get(a, a)] = json.loads(json.dumps(v).replace('"Eval Webhook"', '"Worker Entry"'))
    return out
c = conns_from(W["connections"], {"Eval Webhook": "Worker Entry"})
link = lambda *t: {"main": [[{"node": x, "type": "main", "index": 0} for x in t]]}
c["Orchestrator Webhook"] = link("Entry Question"); c["Entry Question"] = link("Plan"); c["Plan"] = link("Parse Plan"); c["Parse Plan"] = link("Call Worker")
c["Call Worker"] = link("Combine"); c["Combine"] = link("Compound?")
c["Compound?"] = {"main": [[{"node": "Merge Answers", "type": "main", "index": 0}], [{"node": "Single Result", "type": "main", "index": 0}]]}
c["Merge Answers"] = link("Final"); c["Final"] = link("Reply Router"); c["Single Result"] = link("Reply Router")
c["Reply Router"] = {"main": [[{"node": "Send Telegram Reply", "type": "main", "index": 0}], [{"node": "Respond Orchestrator", "type": "main", "index": 0}]]}
if WITH_TG: c["Telegram Trigger"] = link("Entry Question")

# ---- frames (sticky notes behind each stage) ----
src = open("arrange_workflows.py").read()
SHORT = eval(re.search(r"SHORT = (\{.*?\n\})", src, re.S).group(1))
SHORT.update({"Telegram: chat in": "A Telegram message (or a test call) arrives and becomes the same thing either way: question text plus the chat id.",
  "Orchestrator: plan": SHORT["1. Plan"], "Orchestrator: ask the worker": "Each part is sent to the worker row below (its own webhook, same workflow) and the part answers are collected.",
  "Orchestrator: merge": "One part: pass the worker's answer through. Several parts: Claude merges them, keeps exact figures, labels filings vs web.",
  "Telegram: reply": "Telegram chat: send the answer plus up to four source links. Test call: return JSON."})
frames = [("Telegram: chat in", ["Orchestrator Webhook", "Entry Question"] + (["Telegram Trigger"] if WITH_TG else []), 4), ("Orchestrator: plan", ["Plan", "Parse Plan"], 4),
  ("Orchestrator: ask the worker", ["Call Worker", "Combine"], 6), ("Orchestrator: merge", ["Compound?", "Merge Answers", "Final", "Single Result"], 5),
  ("Telegram: reply", ["Reply Router", "Send Telegram Reply", "Respond Orchestrator"], 4)]
for old, new, color in (("1. Understand the question", "Worker: understand the question", 4), ("2a. Filing text (10-K / 10-Q)", "Worker: filing text (10-K / 10-Q)", 5), ("2b. Exact numbers (SEC XBRL)", "Worker: exact numbers (SEC XBRL)", 6),
                        ("2c. Web news", "Worker: web news", 3), ("2d. Direct reply", "Worker: direct reply", 7), ("3. Write and return the answer", "Worker: write and return the answer", 4)):
    SHORT[new] = SHORT[old]
members = {"Worker: understand the question": ["Worker Entry", "Question", "Build Router Request", "Jev Router", "Decide Route", "Route"],
  "Worker: filing text (10-K / 10-Q)": ["Search Filings (Pinecone)", "Shape Hits", "KB Context", "Jev Grade KB", "KB Verdict", "KB Good?"], "Worker: exact numbers (SEC XBRL)": ["SEC XBRL Facts", "Extract Figures", "Figures Found?"],
  "Worker: web news": ["Keenable Search", "Web Context", "Jev Grade Web", "Web Verdict", "Web Good?", "Fallback"], "Worker: direct reply": ["Direct"],
  "Worker: write and return the answer": ["Answer Input", "Generate Answer", "Anthropic Chat Model", "Package Result", "Respond"]}
colors = {"Worker: understand the question": 4, "Worker: filing text (10-K / 10-Q)": 5, "Worker: exact numbers (SEC XBRL)": 6, "Worker: web news": 3, "Worker: direct reply": 7, "Worker: write and return the answer": 4}
frames += [(k, v, colors[k]) for k, v in members.items()]
def sticky(text, x, y, w, h, color): return {"id": uid(), "name": "Sticky " + text[:20], "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [x, y], "parameters": {"content": text, "width": w, "height": h, "color": color}}
stickies = []
for name, mem, color in frames:
    pts = [nodes[m]["position"] for m in mem]; x0, x1, y0, y1 = min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)
    stickies.append(sticky(f"### {name}\n{SHORT[name]}", x0 - 40, y0 - 190, max(x1 - x0 + 320, 560), y1 - y0 + 190 + 250, color))
stickies.append(sticky("## SEC Agentic RAG, top to bottom: TELEGRAM (message in, reply out) | ORCHESTRATOR (plan, ask the worker, merge) | WORKER (answers one question)", 0, -560, 3300, 130, 7))

PERF = """## Real timing and cost
Measured from this workflow's own runs (Oct 2026). Median time per step:
- Jev router (picks source, company, metric): 0.29 s
- Jev evidence check (filings or web): 0.30 s
- Planner, Claude Haiku 5.5: 1.2 s
- Answer, Claude Sonnet 5.5: 4.0 s
- Merge of several parts, Claude Sonnet 5.5: 6.2 s
- Whole question: simple about 4 to 9 s, compound about 15 to 20 s

Cost per question, from recorded tokens at list prices:
- Jev: $0.00013 per worker run, about 1% of the total
- Answer step: about $0.011 per part (98% of the total)
- Simple question: about $0.011. Six-part compound: up to about $0.08
- Not counted: Pinecone, Keenable web search, hosting

Prices per 1M tokens in/out: Sonnet 5.5 $2 / $10, Haiku 5.5 $0.10 / $0.50, Jev $0.042 in."""
stickies.append(sticky(PERF, 0, -1130, 1000, 520, 5))   # measured timing and cost note
body = {"name": "SEC Agentic RAG - all in one", "nodes": list(nodes.values()) + stickies, "connections": c, "settings": {"executionOrder": "v1"}}
if prev: api("/workflows/" + prev["id"], "PUT", body); wid = prev["id"]
else: wid = api("/workflows", "POST", body)["id"]
json.dump({"id": wid, "orch_path": OPATH, "worker_path": WPATH}, open("combined_target.json", "w"))
json.dump({"id": wid, "path": OPATH}, open("orch_target.json", "w")); json.dump({"id": wid, "path": WPATH}, open("eval_target.json", "w"))
print("combined workflow", wid, "| telegram trigger:", WITH_TG, "| nodes:", len(nodes), "frames:", len(stickies))
