"""Create the ORCHESTRATOR workflow (webhook): plans sub-questions with a small Claude model, sends each to the worker
pipeline (the eval copy / single-question workflow) over its webhook, then merges the answers.
Usage: N8N_API_KEY=... python3 make_orchestrator.py   (writes orch_target.json; re-running updates the same workflow)"""
import json, os, urllib.request, uuid
K = os.environ["N8N_API_KEY"]
def api(p, m="GET", d=None):
    r = urllib.request.Request("http://127.0.0.1:5678/api/v1" + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers={"X-N8N-API-KEY": K, "Content-Type": "application/json"})
    try: return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e: raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:400]}")
W = json.load(open("eval_target.json"))                    # worker = single-question pipeline
COMPANIES = ", ".join(f"{v['name']} ({k})" for k, v in json.load(open("companies.json")).items())
CRED = {"anthropicApi": {"id": "tFtTjm3DNKM3YV2q", "name": "Anthropic account"}}
uid = lambda: str(uuid.uuid4())
try: old = json.load(open("orch_target.json"))
except FileNotFoundError: old = None
PATH = old["path"] if old else "orch-" + uuid.uuid4().hex[:16]

PLAN_SYSTEM = f"""You split a user's question for a US tech-company SEC filings assistant into the smallest set of self-contained sub-questions.
Covered companies: {COMPANIES}.
Rules:
- Output JSON only: {{"parts": ["sub-question 1", "sub-question 2"]}} with 1 to 6 parts.
- Split when the question involves several companies, several financial metrics, or mixes SEC-filing facts with news, market or investor reaction.
- Each part names at most ONE company. When a question asks for the same thing about several companies, write one part per company.
- Questions about risks, strategy, products or business should be phrased "According to <Company>'s most recent 10-K or 10-Q, ..." so they are answered from that company's filings.
- Each part must stand alone: name the company explicitly, state the metric and period, never use pronouns like "their" or "it".
- One company and one metric (or one topic) from one kind of source is ONE part: return the question unchanged.
- If the question is about a company that is not in the covered list, or no company at all, return it unchanged as ONE part.
- Examples:
  "Compare Microsoft's and Amazon's R&D spending last year and say if investors reacted badly" -> {{"parts": ["What was Microsoft's research and development expense in its most recent fiscal year?", "What was Amazon's research and development expense in its most recent fiscal year?", "How have investors reacted to Microsoft's and Amazon's R&D spending?"]}}
  "What were Apple's revenue and net income last year?" -> {{"parts": ["What was Apple's revenue in its most recent fiscal year?", "What was Apple's net income in its most recent fiscal year?"]}}
  "Compare Tesla's and Netflix's net income last year, summarize the main risks each mentions in its 10-K, and tell me how investors reacted" -> {{"parts": ["What was Tesla's net income in its most recent fiscal year?", "What was Netflix's net income in its most recent fiscal year?", "According to Tesla's most recent 10-K, what are the main risk factors?", "According to Netflix's most recent 10-K, what are the main risk factors?", "How have investors reacted recently to Tesla's and Netflix's latest results?"]}}
  "What are Tesla's main supply chain risks?" -> {{"parts": ["What are Tesla's main supply chain risks?"]}}
  "What does Shopify do and what is its revenue?" -> {{"parts": ["What does Shopify do and what is its revenue?"]}}
- Never invent facts, never add parts the user did not ask for, never answer the question yourself."""
SYNTH_SYSTEM = """You merge answers to sub-questions into one reply for a Telegram user about US tech companies.
Rules: use ONLY the facts in the sub-answers; keep exact figures and their periods; say clearly which parts come from SEC filings and which from web news (web is not official); if a sub-answer says something could not be confirmed, say so; never add numbers, facts or claims that are not in the sub-answers (no outside knowledge); no investment advice. Plain text, no tables, under 1,400 characters; finish every sentence."""

def anthropic(name, pos, model, system, user_expr, max_tokens, timeout=60000, on_error=None):
    body = "={{ JSON.stringify({ model: %s, max_tokens: %d, system: %s, messages: [{ role: 'user', content: %s }] }) }}" % (json.dumps(model), max_tokens, json.dumps(system), user_expr)
    return {"id": uid(), "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos, "retryOnFail": True, "maxTries": 2, "waitBetweenTries": 2000,
      "parameters": {"method": "POST", "url": "https://api.anthropic.com/v1/messages", "authentication": "predefinedCredentialType", "nodeCredentialType": "anthropicApi",
        "sendHeaders": True, "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
        "sendBody": True, "specifyBody": "json", "jsonBody": body, "options": {"timeout": timeout}}, "credentials": CRED, **({"onError": on_error} if on_error else {})}

PARSE = r"""
const q = String($('Orchestrator Webhook').first().json.body?.question || '').trim();
let parts = [];
try {
  const txt = ($input.first().json.content || []).map(c => c.text || '').join('');
  const j = JSON.parse(txt.slice(txt.indexOf('{'), txt.lastIndexOf('}') + 1));
  parts = (j.parts || []).map(p => String(p).trim()).filter(Boolean).slice(0, 6);
} catch (e) { /* planner failed: fall back to the original question */ }
if (!parts.length) parts = [q];
return parts.map((part, i) => ({ json: { question: q, part, idx: i, n: parts.length } }));
"""
COMBINE = r"""
const plan = $('Parse Plan').all().map(i => i.json);
const res = $input.all().map(i => i.json);
const parts = plan.map((p, i) => ({ q: p.part, answer: res[i]?.answer || '', mode: res[i]?.mode ?? null, route: res[i]?.route ?? null, ticker: res[i]?.ticker ?? null, sources: res[i]?.sources || [] }));
const question = plan[0].question;
if (parts.length === 1) return [{ json: { compound: false, result: { ...res[0], parts } } }];
const lines = parts.map((p, i) => `Sub-question ${i + 1}: ${p.q}\nAnswer (${p.mode || 'no mode'}): ${p.answer || '(no answer)'}\nSources: ${p.sources.join(', ') || 'none'}`);
return [{ json: { compound: true, question, parts, synthInput: `User question: ${question}\n\n` + lines.join('\n\n') } }];
"""
FINAL = r"""
const c = $('Combine').first().json;
const answer = ($input.first().json.content || []).map(x => x.text || '').join('').trim();
const sources = [...new Set(c.parts.flatMap(p => p.sources))];
return [{ json: { question: c.question, answer, mode: 'compound', route: 'compound', ticker: null, sources, parts: c.parts.map(p => ({ q: p.q, route: p.route, mode: p.mode, ticker: p.ticker })) } }];
"""
SINGLE = "return [{ json: $input.first().json.result }];"
nodes = [
 {"id": uid(), "name": "Orchestrator Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0], "webhookId": uid(), "parameters": {"httpMethod": "POST", "path": PATH, "responseMode": "lastNode", "options": {}}},
 anthropic("Plan", [250, 0], "claude-haiku-5-5", PLAN_SYSTEM, "$('Orchestrator Webhook').first().json.body.question", 800, timeout=25000, on_error="continueRegularOutput"),   # a stuck planner must not freeze the bot: Parse Plan falls back to one part
 {"id": uid(), "name": "Parse Plan", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [500, 0], "parameters": {"jsCode": PARSE}},
 {"id": uid(), "name": "Call Worker", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [750, 0], "retryOnFail": True, "maxTries": 2, "waitBetweenTries": 2000,
  "parameters": {"method": "POST", "url": f"http://127.0.0.1:5678/webhook/{W['path']}", "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({ question: $json.part }) }}", "options": {"timeout": 150000}}},
 {"id": uid(), "name": "Combine", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [1000, 0], "parameters": {"jsCode": COMBINE}},
 {"id": uid(), "name": "Compound?", "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [1250, 0], "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
   "conditions": [{"id": uid(), "leftValue": "={{ $json.compound }}", "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}, "options": {}}},
 anthropic("Merge Answers", [1500, -100], "claude-sonnet-5-5", SYNTH_SYSTEM, "$json.synthInput", 3000),
 {"id": uid(), "name": "Final", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [1750, -100], "parameters": {"jsCode": FINAL}},
 {"id": uid(), "name": "Single Result", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [1500, 150], "parameters": {"jsCode": SINGLE}},
]
link = lambda *t: {"main": [[{"node": x, "type": "main", "index": 0} for x in t]]}
conns = {"Orchestrator Webhook": link("Plan"), "Plan": link("Parse Plan"), "Parse Plan": link("Call Worker"), "Call Worker": link("Combine"), "Combine": link("Compound?"),
         "Compound?": {"main": [[{"node": "Merge Answers", "type": "main", "index": 0}], [{"node": "Single Result", "type": "main", "index": 0}]]}, "Merge Answers": link("Final")}
body = {"name": "ORCH eval - plan, call worker, merge", "nodes": nodes, "connections": conns, "settings": {"executionOrder": "v1"}}
if old: api("/workflows/" + old["id"], "PUT", body); wid = old["id"]
else: wid = api("/workflows", "POST", body)["id"]
json.dump({"id": wid, "path": PATH}, open("orch_target.json", "w")); print("orchestrator", wid, PATH)
