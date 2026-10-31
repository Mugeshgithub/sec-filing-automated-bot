# SEC Filing Automated Bot

A Telegram bot that answers questions about 20 large US tech companies from their own SEC filings, built as an agentic RAG workflow in n8n.

Ask it something like:

> Compare Tesla's and Netflix's net income in their latest fiscal year, summarize the main risks each mentions in its 10-K, and tell me how investors have reacted recently.

and it replies with exact figures from the SEC's structured data, risks taken from the 10-K text, and investor reaction clearly labelled as web news (not official), with source links. When evidence is thin it says what it could not confirm instead of guessing.

> Built with AI assistance (Claude Code). I designed the system and the tests and checked the results; the code and workflows were written together with the assistant.

## How it works

One n8n workflow with three rows:

```
Telegram  ->  Orchestrator  ->  Worker  ->  (one answer per part)  ->  merge  ->  Telegram
 message in    plan, split       answers
               into parts        one question
```

- **Telegram row:** receives the message, sends the reply with up to 8 source links (filings first, then web, then raw SEC data).
- **Orchestrator row:** a small Claude model (Haiku) splits a hard question into 1 to 6 self-contained parts, one company per part. Each part goes to the worker through its own webhook. With several parts, Claude (Sonnet) merges the answers, keeps exact figures and labels filings vs web. A simple question skips the merge.
- **Worker row:** answers one question.
  1. **Jev**, a small decision model (TypeSafe, through OpenRouter), answers three multiple-choice questions: which source, which of the 20 companies, which metric. Fixed rules then overrule it (unknown company means "not covered", no company means web search, "ignore your rules" or "tell me to buy" gets a fixed decline).
  2. Four lanes:

| Lane | For | Source | Check before answering |
|---|---|---|---|
| Filing text | business, strategy, risks | latest 10-K and 10-Q of each company in Pinecone (40 filings, about 9,000 passages) | Jev scores whether the 14 retrieved passages are on topic for the right company (cut-off 0.45), else falls back to web |
| Exact numbers | revenue, net income, EPS, R&D, cash, debt | SEC XBRL company facts | exact value with period and filing reference, never estimated |
| Web news | news, stock moves, events after the last filing | Keenable web search | always labelled "not official"; Jev scores the results (cut-off 0.6), else a partial answer saying what is unconfirmed |
| Direct | greetings, bot questions, companies outside the 20 | none | fixed "not covered" message, no figures |

  3. Claude Sonnet 5.5 writes the answer under strict per-mode rules (never invent numbers, name the form and period, say when unsure, no investment advice).

**Ingestion** (run on demand): SEC EDGAR, latest 10-K and 10-Q per company, strip markup, cap at about 350k characters, 1,500-character chunks with 200 overlap, stored in Pinecone with one namespace per ticker. Pinecone embeds the text itself (integrated embedding). Chunk IDs are fixed, so re-running overwrites instead of duplicating.

## Evaluation

21 automatic questions (`eval/questions.json`) run through the same entry the Telegram bot uses, scored by `scripts/run_eval.py`. Examples of what is checked: stated figures within 0.5% of the SEC value, filing questions really answered from filings, web answers labelled unofficial, refusals for unanswerable questions and uncovered companies, no buy/sell advice, compound questions split and merged without being cut off.

| Run | Result |
|---|---|
| Fixed flow (this workflow) | 21/21 (`results/fixed-flow-21-of-21.json`) |
| Same questions with an n8n AI Agent node instead of the fixed router | 17/21, 21/21 and 20/21 on three runs (`results/agent-run-*.json`) |

Honest history of the score: an early version passed most tests while the filings index was silently empty, so filing questions were being answered from the web. After tightening the scorer to check the real route, the score was 11 of 18. Fixing ingestion, scope handling, retrieval wording and grading brought it to 21 of 21. The agent comparison used an n8n AI Agent node with three tools (SEC figures, filing search, web search) and safety only in the prompt. For the agent I changed how a run is labelled "compound" after seeing the first result (several searches on one company no longer count), which is why run 1 is lower. On this small question set the two designs were close on accuracy and cost; the agent was faster on compound questions. The fixed flow keeps its rules and checks as guarantees that a prompt cannot give.

Measured from the workflow's own runs (medians): Jev router 0.29 s, Jev evidence check 0.30 s, planner 1.2 s, answer 4.0 s, merge 6.2 s. A simple question takes about 4 to 9 s and a compound one 15 to 20 s. Cost at list prices: about $0.011 for a simple question, up to about $0.08 for a six-part question; Jev is about 1% of that.

## Setup (outline)

1. Run n8n (Docker works) and make it reachable by Telegram over HTTPS (for example an ngrok domain set as `WEBHOOK_URL`).
2. Create these credentials in n8n: Telegram bot, Anthropic, OpenRouter (for Jev), Pinecone, Keenable.
3. Create a Pinecone index with integrated embedding and a `chunk_text` field. Note its host.
4. Import the files in `workflows/`, replace the placeholders (`<RANDOM-WEBHOOK-PATH>`, `<YOUR-PINECONE-INDEX-HOST>`, credential IDs) and attach your credentials.
5. In the SEC calls, replace `your-name your-email@example.com` with a real contact. The SEC requires it in the User-Agent.
6. Run the ingestion workflow once, then the eval.

`scripts/` holds the build and evaluation helpers I used against my own n8n. They assume my local setup (target files, local webhook paths), so treat them as reference code. The portable parts are the workflow JSON, `eval/questions.json` and `eval/companies.json`.

## Repository layout

```
workflows/   sanitized n8n exports: the bot, the ingestion workflow, the AI Agent experiment
eval/        the 21 test questions and the company list
results/     saved eval runs
scripts/     helper scripts (build workflows, run eval, run ingestion)
```

## Limits

- Runs on one laptop (n8n in Docker plus a tunnel), so it is offline when the machine is.
- No Telegram allow-list or rate limit yet: anyone who finds the bot can use it, and each message costs money.
- 20 companies and their latest filings only.
- Web pages are not tested for hidden instructions (prompt injection through search results).
- Some figures are not reported plainly. Amazon has no separate R&D line, so that part comes from the web and says so.
- Grading thresholds (0.45 and 0.6) were tuned by hand from observed scores.
- The test set is small. It catches regressions but does not prove coverage.
- Nothing here is investment advice.
