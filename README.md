# Sales Research Agent

An autonomous AI agent that generates real-time company intelligence briefs for sales teams.

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

- [x] Batch processing — upload CSV of companies, research all at once
- [x] Persistent database — SQLite tracks every company researched over time
- [x] Export to CSV — download a ranked opportunity list for your sales team
- [x] Rate limiting — session-based usage controls (production-ready)
- [x] Seller context — personalized scoring and framing per seller profile
- [x] DB-backed memory — past research used as benchmarks for new scoring
- [ ] Document ingestion — upload prospect PDFs for deeper analysis

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
