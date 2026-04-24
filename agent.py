import os
import json
from dotenv import load_dotenv
import anthropic
from tavily import TavilyClient

load_dotenv()

# Two different models — Haiku for cheap decisions, Sonnet for final output
claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


# ── TOOLS ──────────────────────────────────────────────────────
def search_web(query: str) -> str:
    """Tool Claude can use to search the web"""
    print(f"  🔍 Searching: {query}")
    results = tavily.search(query=query, max_results=3)
    # Pull out just the content, not the full object
    output = ""
    for r in results["results"]:
        output += f"Source: {r['url']}\n{r['content']}\n\n"
    return output


# ── AGENT LOOP ─────────────────────────────────────────────────
def research_company(company_name: str) -> dict:
    print(f"\n🤖 Researching {company_name}...")

    # This is the agent's memory — everything it knows so far
    gathered_info = []
    max_searches = 4  # hard limit so we don't burn tokens
    searches_done = 0

    # ── STEP 1: Claude decides what to search for ──
    # We use cheap Haiku for this decision step
    while searches_done < max_searches:

        # Build context of what we've gathered so far
        context = "\n\n".join(gathered_info) if gathered_info else "Nothing yet."

        # Ask Claude what to search for next
        decision = claude.messages.create(
            model=HAIKU,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""You are researching {company_name} for a sales brief.

What you know so far:
{context}

What is the single most important thing still missing?
If you have enough for a complete brief, reply with just: DONE
Otherwise reply with a search query of 5 words or less.
Nothing else."""
            }]
        )

        next_action = decision.content[0].text.strip()

        # Claude says it has enough — break the loop
        if next_action == "DONE":
            print(f"  ✅ Claude satisfied after {searches_done} searches")
            break

        # Claude wants more info — run the search
        results = search_web(next_action)
        gathered_info.append(f"Search: {next_action}\nResults: {results}")
        searches_done += 1

    # ── STEP 2: Claude writes the final brief ──
    # Now we use smarter Sonnet for the actual output
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

opportunity_score is 1-10 based on how good a sales target they are.
confidence is high/medium/low based on how much data you found."""
        }]
    )

    # Parse the JSON output
    try:
        # Strip markdown backticks if Claude added them
        raw_text = brief.content[0].text.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw_text)
    except:
        # If Claude didn't return clean JSON, return raw text
        result = {"company": company_name, "raw": brief.content[0].text}

    return result


# ── RUN IT ─────────────────────────────────────────────────────
if __name__ == "__main__":
    result = research_company("Stripe")
    print("\n── FINAL BRIEF ──")
    print(json.dumps(result, indent=2))
