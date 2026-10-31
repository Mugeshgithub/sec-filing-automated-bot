"""Lay out, caption and group the three chat workflows so they can be read (and explained) left to right.
Only positions, node captions (notes), groups and sticky notes change; node names and logic are untouched. Safe to re-run.
Usage: N8N_API_KEY=... python3 arrange_workflows.py"""
import json, os, time, uuid, urllib.request
K = os.environ["N8N_API_KEY"]
def api(p, m="GET", d=None):
    r = urllib.request.Request("http://127.0.0.1:5678/api/v1" + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers={"X-N8N-API-KEY": K, "Content-Type": "application/json"})
    try: return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e: raise SystemExit(f"HTTP {e.code} on {m} {p}: {e.read().decode()[:400]}")
W, O, TG = json.load(open("eval_target.json"))["id"], json.load(open("orch_target.json"))["id"], "TYYqRQ2xtP43ffPu"
BRANCHING = set()   # frames have no single-exit rule
S = 260   # column spacing
L = {"filings": 300, "numbers": 800, "web": 1300, "direct": 2000}   # one lane per route, same order as the Route switch outputs

WORKER = {
 "title": "WORKER: answers ONE question. Route it, fetch evidence, grade the evidence, write the answer.",
 "nodes": {  # name: (x, y, caption)
  "Eval Webhook": (0, 0, "Entry: receives {question}"), "Question": (S, 0, "Cleans the question text"),
  "Build Router Request": (2*S, 0, "Prepares 3 multiple-choice questions for Jev"), "Jev Router": (3*S, 0, "Jev picks source, company, metric"),
  "Decide Route": (4*S, 0, "Fixed rules on top of Jev's choice"), "Route": (5*S, 0, "Sends it down one of 4 lanes"),
  "Search Filings (Pinecone)": (7*S, L["filings"], "Finds 10 matching filing passages"), "Shape Hits": (8*S, L["filings"], "Uniform format"),
  "KB Context": (9*S, L["filings"], "Builds context + grading request"), "Jev Grade KB": (10*S, L["filings"], "Jev: do passages answer it?"),
  "KB Verdict": (11*S, L["filings"], "Score to good / not good (0.45)"), "KB Good?": (12*S, L["filings"], "No: try the web instead"),
  "SEC XBRL Facts": (7*S, L["numbers"], "Official reported figures (SEC)"), "Extract Figures": (8*S, L["numbers"], "Picks metric + exact period"),
  "Figures Found?": (9*S, L["numbers"], "None: search filing text"),
  "Keenable Search": (7*S, L["web"], "Searches recent web news"), "Web Context": (8*S, L["web"], "Titles, URLs, dates"),
  "Jev Grade Web": (9*S, L["web"], "Jev: do results answer it?"), "Web Verdict": (10*S, L["web"], "Score to good / not good (0.6)"),
  "Web Good?": (11*S, L["web"], "No: partial answer"), "Fallback": (12*S, L["web"] + 170, "Says what is unconfirmed"),
  "Direct": (7*S, L["direct"], "No search needed"),
  "Answer Input": (14*S, 800, "All lanes meet here"), "Generate Answer": (15*S, 800, "Claude writes the answer"),
  "Anthropic Chat Model": (15*S, 1050, "claude-sonnet-5-5"), "Package Result": (16*S, 800, "Answer + route + sources as JSON"), "Respond": (17*S, 800, "Returns JSON to caller")},
 "groups": [
  ("1. Understand the question", ["Eval Webhook", "Question", "Build Router Request", "Jev Router", "Decide Route", "Route"], 4,
   "Jev (a small decision model) answers three multiple-choice questions: which source (filings, numbers, web, direct), which of the 20 companies, which metric. Fixed rules then fix its mistakes: unknown company means 'not covered', no company means web."),
  ("2a. Filing text (10-K / 10-Q)", ["Search Filings (Pinecone)", "Shape Hits", "KB Context", "Jev Grade KB", "KB Verdict", "KB Good?"], 5,
   "For business, strategy and risk questions. Searches the filings stored in Pinecone, then Jev grades whether the passages really answer the question. If not, it falls back to the web."),
  ("2b. Exact numbers (SEC XBRL)", ["SEC XBRL Facts", "Extract Figures", "Figures Found?"], 6,
   "For 'what was revenue / net income / R&D'. Reads the SEC's official structured data, so figures are exact and carry their period and filing reference. Never estimated."),
  ("2c. Web news", ["Keenable Search", "Web Context", "Jev Grade Web", "Web Verdict", "Web Good?", "Fallback"], 3,
   "For news, stock moves and anything after the last filing. Always labelled 'not official'. If Jev grades the results as insufficient, the answer says what could not be confirmed."),
  ("2d. Direct reply", ["Direct"], 7, "Greetings, how-does-this-bot-work, and companies outside the 20: answered without searching, with a clear 'not covered' message."),
  ("3. Write and return the answer", ["Answer Input", "Generate Answer", "Anthropic Chat Model", "Package Result", "Respond"], 4,
   "Every lane delivers the same shape: question, mode, context, sources. Claude writes the answer under strict per-mode rules (never invent numbers, say when unsure). Result goes back as JSON.")]}

ORCH = {
 "title": "ORCHESTRATOR: splits a hard question into parts, asks the worker for each, merges the answers.",
 "nodes": {"Orchestrator Webhook": (0, 0, "Entry: receives {question}"), "Plan": (S, 0, "Claude Haiku splits the question"), "Parse Plan": (2*S, 0, "1 to 3 parts (or the original)"),
  "Call Worker": (4*S, 0, "Runs the worker once per part"), "Combine": (6*S, 0, "Collects the part answers"), "Compound?": (7*S, 0, "More than one part?"),
  "Merge Answers": (8*S, -150, "Claude merges, no outside facts"), "Final": (9*S, -150, "Answer + sources + parts"), "Single Result": (8*S, 150, "One part: pass through")},
 "groups": [("1. Plan", ["Orchestrator Webhook", "Plan", "Parse Plan"], 4, "A small, cheap Claude model splits the question into self-contained parts: one per company, metric, or kind of source. A simple question stays as one part."),
  ("2. Ask the worker", ["Call Worker", "Combine"], 6, "Each part goes through the full worker pipeline, which routes it to filings, exact numbers or web and grades the evidence. Combine collects the part answers."),
  ("3. Merge (several parts)", ["Merge Answers", "Final"], 4, "Compound? checks the part count. One part: Single Result returns the worker's answer untouched. Several parts: Claude merges them, keeps exact figures, and says which parts are official filings and which are web news.")]}

TGRAM = {
 "title": "TELEGRAM FRONT END: chat in, answer out. All the thinking happens in the orchestrator and worker.",
 "nodes": {"Telegram Trigger": (0, 0, "A user message arrives"), "Question": (S, 0, "Keeps text + chat id"), "Call Orchestrator": (2*S, 0, "Sends the question on"), "Send Telegram Reply": (3*S, 0, "Replies with answer + sources")},
 "groups": [("Chat in, answer out", ["Telegram Trigger", "Question", "Call Orchestrator", "Send Telegram Reply"], 3, "Thin by design: it only moves messages. Hard questions are handled by the orchestrator, simple ones by the worker.")]}

SHORT = {
 "1. Understand the question": "Jev picks source, company and metric. Fixed rules then correct it: unknown company = 'not covered', no company = web.",
 "2a. Filing text (10-K / 10-Q)": "Business, strategy and risk questions. Searches stored 10-K/10-Q text; Jev grades if it answers the question, else falls back to web.",
 "2b. Exact numbers (SEC XBRL)": "'What was revenue / net income / R&D?' Reads the SEC's official data: exact figures with period and filing reference, never estimated.",
 "2c. Web news": "News and anything after the last filing. Labelled 'not official'. If Jev grades results as insufficient, the answer says what is unconfirmed.",
 "2d. Direct reply": "Greetings, bot questions and companies outside the 20: answered without searching, with a clear 'not covered' message.",
 "3. Write and return the answer": "All lanes deliver the same shape. Claude writes the answer under strict per-mode rules (never invent numbers); returned as JSON.",
 "1. Plan": "A small Claude model splits the question into self-contained parts: one per company, metric or source kind. A simple question stays one part.",
 "2. Ask the worker": "Each part runs through the full worker pipeline. Combine collects the part answers.",
 "3. Merge (several parts)": "Several parts: Claude merges them, keeps exact figures, labels filings vs web. One part skips this and returns the worker's answer.",
 "Chat in, answer out": "Only moves messages. Hard questions go to the orchestrator, which calls the worker.",
}

def sticky(text, x, y, w, h, color):
    return {"id": str(uuid.uuid4()), "name": "Sticky " + text[:18], "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [x, y], "parameters": {"content": text, "width": w, "height": h, "color": color}}

def arrange(wid, spec):
    wf = api("/workflows/" + wid); json.dump(wf, open(f"backup7-{wid}-{int(time.time())}.json", "w"))
    by = {n["name"]: n for n in wf["nodes"] if "stickyNote" not in n["type"]}
    missing = set(spec["nodes"]) ^ set(by); assert not missing, missing
    for name, (x, y, note) in spec["nodes"].items():
        by[name]["position"] = [x, y]; by[name]["notes"] = note; by[name]["notesInFlow"] = True
    nodes, groups = list(by.values()), []
    xs = [v[0] for v in spec["nodes"].values()]
    nodes.append(sticky("## " + spec["title"], min(xs), min(v[1] for v in spec["nodes"].values()) - 460, max(xs) - min(xs) + 220, 130, 7))
    for name, members, color, desc in spec["groups"]:       # frames, not n8n groups: groups collapse per browser, frames always show
        pts = [spec["nodes"][m] for m in members]; x0, x1, y0, y1 = min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)
        nodes.append(sticky(f"### {name}\n{SHORT[name]}", x0 - 40, y0 - 190, max(x1 - x0 + 320, 560), y1 - y0 + 190 + 250, color))
    api("/workflows/" + wid, "PUT", {"name": wf["name"], "nodes": nodes, "connections": wf["connections"], "settings": wf["settings"], "nodeGroups": []})
    print("arranged", wf["name"], "| active:", api("/workflows/" + wid)["active"])

for wid, spec in ((W, WORKER), (O, ORCH), (TG, TGRAM)): arrange(wid, spec)
