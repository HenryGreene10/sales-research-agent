import os
import json
from dotenv import load_dotenv
import anthropic
from tavily import TavilyClient
from database import get_similar_companies

load_dotenv()

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


# ── SEARCH FUNCTIONS ───────────────────────────────────────────

def search_web(query: str) -> str:
    print(f"  🔍 search_web: {query}")
    results = tavily.search(query=query, max_results=3)
    output = ""
    for r in results["results"]:
        output += f"Source: {r['url']}\n{r['content']}\n\n"
    return output


def get_job_postings(company_name: str) -> str:
    query = f"{company_name} jobs hiring 2025 site:linkedin.com OR site:greenhouse.io OR site:lever.co"
    print(f"  💼 get_job_postings: {company_name}")
    results = tavily.search(query=query, max_results=3)
    output = ""
    for r in results["results"]:
        output += f"Source: {r['url']}\n{r['content']}\n\n"
    return output


def get_sec_filing(company_name: str) -> str:
    query = f"{company_name} 10-K OR earnings call 2025 revenue growth"
    print(f"  📊 get_sec_filing: {company_name}")
    results = tavily.search(query=query, max_results=3)
    output = ""
    for r in results["results"]:
        output += f"Source: {r['url']}\n{r['content']}\n\n"
    return output


def get_funding_news(company_name: str) -> str:
    query = f"{company_name} funding raised investment 2024 2025"
    print(f"  💰 get_funding_news: {company_name}")
    results = tavily.search(query=query, max_results=3)
    output = ""
    for r in results["results"]:
        output += f"Source: {r['url']}\n{r['content']}\n\n"
    return output


# ── TOOL DEFINITIONS (passed to Claude) ───────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "search_web",
        "description": (
            "General web search. Use for company overview, products, customers, "
            "or recent news not covered by a specialized tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query, 5-8 words"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_job_postings",
        "description": (
            "Search LinkedIn, Greenhouse, and Lever for current job postings. "
            "Reveals which teams and technologies the company is actively investing in."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string"}
            },
            "required": ["company_name"]
        }
    },
    {
        "name": "get_sec_filing",
        "description": (
            "Search for 10-K filings and earnings calls. "
            "Use only for public companies to understand revenue, growth, and financial health."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string"}
            },
            "required": ["company_name"]
        }
    },
    {
        "name": "get_funding_news",
        "description": (
            "Search for recent funding rounds and investment activity. "
            "Best for private companies and startups."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string"}
            },
            "required": ["company_name"]
        }
    },
    {
        "name": "finish_research",
        "description": "Call this when you have gathered enough to write a complete sales brief.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


def _dispatch_tool(name: str, inputs: dict) -> str:
    if name == "search_web":
        return search_web(inputs["query"])
    if name == "get_job_postings":
        return get_job_postings(inputs["company_name"])
    if name == "get_sec_filing":
        return get_sec_filing(inputs["company_name"])
    if name == "get_funding_news":
        return get_funding_news(inputs["company_name"])
    return ""


# ── AGENT LOOP ─────────────────────────────────────────────────

def research_company(company_name: str, user_context: dict = None) -> dict:
    print(f"\n🤖 Researching {company_name}...")

    # Build context strings from seller profile
    if user_context:
        sell = user_context.get("what_you_sell", "")
        size = user_context.get("target_size", "")
        industry = user_context.get("target_industry", "")
        industry_str = f" {industry}" if industry else ""
        seller_line = (
            f"The seller sells {sell} to {size}{industry_str} companies. "
            f"Focus on what matters for this specific sale."
        )
        scoring_persona = (
            f"You are scoring this company as a sales target for someone who sells "
            f"{sell} to {size}{industry_str} companies. "
            f"Score and frame everything from that specific perspective."
        )
    else:
        seller_line = ""
        scoring_persona = "opportunity_score is 1-10 based on how good a sales target they are."

    # ── STEP 1: Tool-calling research loop (Haiku) ──
    gathered_info = []
    max_tool_calls = 4

    # Build benchmark context from past research in the database
    similar = get_similar_companies(company_name, user_context)
    if similar:
        benchmark_lines = [
            f"- {c['company']} (score: {c['score']}/10): "
            f"Pain point: {c['pain_point']}. "
            f"Outreach angle: {c['outreach_angle']}."
            for c in similar
        ]
        benchmark_context = (
            "For reference, similar companies that scored well previously:\n"
            + "\n".join(benchmark_lines)
            + f"\n\nUse these as a benchmark when evaluating and scoring {company_name}. "
            "If this company shares characteristics with high scorers, weight that positively."
        )
    else:
        benchmark_context = ""

    opening = f"Research {company_name} for a sales intelligence brief."
    if seller_line:
        opening += f"\n{seller_line}"
    if benchmark_context:
        opening += f"\n\n{benchmark_context}"
    opening += (
        "\n\nUse the available tools to gather: what they do, who their customers are, "
        "recent news, financial health or funding, hiring trends, and their biggest challenges. "
        "Call finish_research when you have enough for a complete brief."
    )

    messages = [{"role": "user", "content": opening}]

    for i in range(max_tool_calls):
        response = claude.messages.create(
            model=HAIKU,
            max_tokens=500,
            tools=TOOL_DEFINITIONS,
            messages=messages
        )

        # Keep the conversation coherent for multi-turn tool use
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print(f"  ✅ Research complete after {i} tool calls")
            break

        # Process every tool_use block in this turn
        tool_results = []
        done = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == "finish_research":
                print(f"  ✅ Research complete after {i} tool calls")
                done = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Research marked complete."
                })
                break

            result = _dispatch_tool(block.name, block.input)
            gathered_info.append(f"[{block.name}]\n{result}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result
            })

        messages.append({"role": "user", "content": tool_results})

        if done:
            break

    # ── STEP 2: Synthesis (Sonnet) ──
    print(f"  ✍️  Writing brief...")

    all_research = "\n\n".join(gathered_info)

    brief = claude.messages.create(
        model=SONNET,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": f"""Based on this research about {company_name}, write a structured sales brief.

Research:
{all_research}

Reply with ONLY a JSON object in this exact format, nothing else:
{{
  "company": "{company_name}",
  "what_they_do": "2 sentence summary",
  "key_customers": "who buys from them",
  "recent_news": "most important recent development",
  "pain_point": "their biggest current business challenge",
  "outreach_angle": "how to approach selling to them",
  "opportunity_score": 7,
  "confidence": "high",
  "confidence_reason": "why you are or aren't confident"
}}

{scoring_persona}
confidence is high/medium/low based on how much data you found."""
        }]
    )

    try:
        raw_text = brief.content[0].text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw_text)
    except Exception:
        result = {"company": company_name, "raw": brief.content[0].text}

    return result


# ── RUN IT ─────────────────────────────────────────────────────
if __name__ == "__main__":
    result = research_company("Stripe")
    print("\n── FINAL BRIEF ──")
    print(json.dumps(result, indent=2))
