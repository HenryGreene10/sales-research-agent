# Sales Research Agent

An agentic commercial intelligence system that turns live company signals, seller context, and evidence-backed research into ranked account briefs.

**[Live Demo →](https://sales-research-agent-bzi73dwliaf9fkpbdliop4.streamlit.app/)**

![Sales Research Agent Screenshot](demo.png)

## What It Does

A sales rep defines their selling context (what they sell, target company size, industry), then types in a company name. The agent autonomously:
1. Selects which specialized tools to call (web search, job postings, SEC filings, funding news)
2. Retrieves similar past companies from its database as scoring benchmarks
3. Synthesizes findings into a structured brief
4. Scores the company 1-10 from the seller's specific perspective
5. Flags confidence level based on data quality

What takes a junior analyst 2 hours takes this agent 30 seconds.

## Current State

The current version is a working V2:

- Streamlit frontend for single-company and batch research
- Company resolution and seller-scoped duplicate control
- Dynamic tool routing and freshness windows by company type
- Evidence-backed scoring with traceable sources and decision paths
- SQLite-backed seller memory, run history, snapshots, and watchlists
- Runtime safety with retries, request timeouts, graceful fallbacks, and batch fault tolerance

The core V2 workflow is implemented. What remains is production polish: more screenshots and docs, larger-scale soak testing, and operational automation around recurring refreshes.

## V2 Outcome

V2 upgraded this project from a demo-grade research agent into a more credible commercial intelligence product.

Core product goal:

Seller profile
→ input target accounts
→ resolve the company correctly
→ run only the right research tools
→ detect why-now opportunity signals
→ rank accounts with evidence-backed scoring
→ inspect sources, trace, and rationale
→ export results

The focus is not “more AI for its own sake.” The focus is trust, routing intelligence, provenance, personalization, and signal detection.

V2 is now complete in the repo: the workflow, persistence model, inspectability, watchlists, runtime safety, and batch fault tolerance are all implemented.

## Why This Is Not A Chatbot

Most AI demos just wrap a prompt around ChatGPT. This is different:

- **Agentic** — Claude decides which tools to call and when to stop, not hardcoded logic
- **Native tool use** — Claude API tool use, not string-parsed ReAct. Claude calls specialized tools (`search_web`, `get_job_postings`, `get_sec_filing`, `get_funding_news`) and declares when it has enough
- **Seller-personalized** — user defines what they sell, target size, and industry upfront; every score and recommendation is framed from that specific sales perspective
- **Memory** — past research is stored in SQLite and surfaced as benchmark context when scoring similar companies
- **Multi-model** — cheap Haiku drives the research loop; Sonnet synthesizes the final brief
- **Structured output** — consistent JSON schema every time, not freeform text
- **Cost aware** — hard tool-call limits and model tiering keep costs under $0.02/run

## Tech Stack

- **Python** — core language
- **Claude API (Anthropic)** — tool use loop (Haiku) + synthesis (Sonnet)
- **Tavily API** — real-time web search
- **Streamlit** — frontend UI
- **SQLite** — persistent company database and benchmark memory

## Key AI Concepts Demonstrated

- **Native tool use** — Claude selects and calls tools via the Anthropic tools API
- **RAG (Retrieval Augmented Generation)** — fresh web data injected into model context
- **Agentic search** — model autonomously chooses queries and specialized tools
- **Personalized scoring** — seller context shapes the scoring persona sent to Sonnet
- **DB-backed memory** — similar past companies retrieved and injected as benchmarks
- **Structured output parsing** — reliable JSON extraction from LLM responses
- **Cost optimization** — tiered model usage based on task complexity

## Architecture

### V1

```
Seller Context (what you sell, target size, industry)
↓
User Input (company name)
↓
Tool-Use Research Loop (Claude Haiku)
→ Calls: search_web / get_job_postings / get_sec_filing / get_funding_news
→ Retrieves similar companies from DB as scoring benchmarks
→ Calls finish_research when done (up to 4 tool calls)
↓
Brief Writer (Claude Sonnet)
→ Synthesizes all findings
→ Scores opportunity 1-10 from seller's perspective
→ Flags confidence level
↓
Structured Output (JSON → UI)
→ Saved to SQLite for future benchmarking
→ Exportable to CSV
```

### V2 Architecture

```
Seller Profile
↓
Account Input (single company or CSV)
↓
Company Resolution
→ normalize name
→ resolve domain / website
→ classify public vs private vs unknown
→ attach ticker / CIK when available
↓
Routing Layer
→ choose only relevant research tools
→ set freshness windows dynamically from runtime date
↓
Research and Evidence Collection
→ web search
→ recent news
→ jobs / hiring
→ funding / momentum
→ SEC / filings when public
→ website / positioning analysis
→ optional tech stack / competitor signals
↓
Signal Detection
→ identify why-now opportunity triggers
→ classify signals by type
→ score timing / urgency
↓
Scoring and Brief Generation
→ seller-scoped benchmark retrieval
→ evidence-backed score rationale
→ opportunity score + trigger score + confidence
↓
Inspectable Output
→ final brief
→ sources
→ tool trace
→ decision trace
→ exportable ranked results
↓
Persistence
→ seller-scoped research runs
→ evidence items
→ duplicate control
→ future watchlists / monitoring
```

## Operational Notes

- Batch uploads accept UTF-8 CSV files with a `company` column.
- Duplicate company rows in a batch are removed before research.
- External Tavily and Claude calls use retries plus request timeouts.
- Partial tool failures do not abort a research run; the UI surfaces those failures in the trace.
- Batch runs continue even if one account fails.

## Setup

```bash
git clone https://github.com/yourusername/sales-research-agent
cd sales-research-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add your API keys to .env
python -m streamlit run app.py
```

## Environment Variables

```
ANTHROPIC_API_KEY=your-key
TAVILY_API_KEY=your-key
```

## Features

- [x] Company resolution — normalize names, resolve domain/website, and classify company type before research
- [x] Dynamic routing — choose tools and freshness windows based on the resolved company
- [x] Evidence-backed scoring — preserve sources, snippets, timestamps, tool traces, and decision traces
- [x] Seller-scoped persistence — seller profiles, runs, aliases, evidence, snapshots, and watchlists stored in SQLite
- [x] Batch processing — upload CSV of companies, dedupe rows, and research all at once
- [x] Export to CSV — download a ranked opportunity list for your sales team
- [x] Watchlists — refresh saved accounts and inspect score/signal deltas over time
- [x] Runtime safety — retries, request timeouts, graceful fallbacks, and batch fault tolerance
- [x] Rate limiting — session-based usage controls
- [x] DB-backed memory — past research used as benchmarks for new scoring
- [ ] Document ingestion — upload prospect PDFs for deeper analysis

## V2 Completion Status

- [x] Company resolution before research
- [x] Dynamic freshness logic by tool type
- [x] Conditional research routing for public vs private vs unknown accounts
- [x] Evidence and provenance capture for every tool result
- [x] Seller-scoped personalization and persistence
- [x] Seller-scoped memory retrieval and benchmark fixes
- [x] Duplicate control using normalized names, domains, aliases, and seller scope
- [x] Runtime safety with retries, timeouts, graceful fallbacks, and batch isolation
- [x] Visible trace UI with resolved company, sources, score inputs, tool trace, and decision trace
- [x] Focused workflow from seller profile to ranked exportable opportunities
- [x] Test coverage for routing, memory, duplicates, provenance, snapshots, watchlists, and batch error handling
- [x] README and positioning updated to present the project as a commercial intelligence agent

## Differentiator: Why-Now Opportunity Signals

The core v2 differentiator is not generic summarization. It is signal detection tied to sales relevance.

Signals to detect:

- recent funding rounds
- hiring spikes
- product launches
- leadership changes
- market expansions
- partnerships or acquisitions
- regulatory or macro catalysts
- messaging changes on website
- tech stack changes

Target output:

```text
Why This Account Now:
- Company increased AI hiring in the last 90 days
- New product launch suggests integration pain
- Recent funding suggests budget and urgency

Opportunity Trigger Score: 8.6/10
```

The ideal implementation is evidence-first:

1. Retrieve signals from sources
2. Classify them into typed signal categories
3. Score their sales relevance
4. Use the model to explain the result clearly

## V3 Candidates

The next step should be a small V3 patch, not a rewrite.

Most of the core product work is already done. The remaining work is operational and productization-focused:

- scheduled or background watchlist refreshes instead of manual refresh only
- alerting when new triggers appear on watched accounts
- stronger observability around latency, failures, and tool-level outcomes
- optional multi-user/auth support if the app moves beyond a single-operator workflow
- deployment hardening and soak testing for larger batch runs
- richer exports or CRM handoff if this becomes part of a real sales workflow

That would shift the system from reactive research to light ongoing opportunity monitoring.

## Suggested Next Steps

The project should still evolve incrementally rather than be rewritten.

Recommended order from here:

1. Add scheduled watchlist refresh and alert generation
2. Add basic observability and structured run logging
3. Run larger real-world batch and watchlist soak tests
4. Improve README screenshots, example output, and demo walkthrough
5. Add integrations only if they support a real workflow, such as CRM export or notification delivery

## What I'd Do With More Time

This pattern — autonomous research → structured output → scored ranking — transfers directly to:
- Vendor risk assessment
- Competitive intelligence
- Customer churn prediction
- Investment due diligence

The agent doesn't know it's doing "sales research." It's a decision-making pipeline that happens to be pointed at companies.

## Author

Built by Henry — CS + Economics background focused on applied AI systems that extract business value from data.

[LinkedIn](https://www.linkedin.com/in/henry-greene/) | [GitHub](https://github.com/HenryGreene10)
