# Sales Research Agent

An autonomous AI agent that generates structured company research briefs in real time.

## What it does
User inputs a company name. The agent:
1. Searches the web for live data using Tavily
2. Feeds results into Claude for synthesis
3. Returns a structured brief with pain points and outreach angle

## Tech Stack
- Python
- Claude API (Anthropic) — reasoning and synthesis
- Tavily API — real-time web search
- Streamlit — UI (coming soon)

## Key AI Concepts Demonstrated
- Agentic search
- RAG (Retrieval Augmented Generation)
- Structured output
- Multi-step reasoning pipeline

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env
# Add your API keys to .env
python agent.py
```

## Status
In active development.
