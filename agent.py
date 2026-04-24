from dotenv import load_dotenv
import os
import anthropic
from tavily import TavilyClient

load_dotenv()

claude_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
def research_company(company_name):
    # Step 1: Search the web for real data
    print(f"Searching for info on {company_name}...")
    search_results = tavily_client.search(
        query=f"{company_name} company overview recent news 2025",
        max_results=5
    )
    
    # Step 2: Pull out the text from results
    raw_data = ""
    for result in search_results["results"]:
        raw_data += f"Source: {result['url']}\n{result['content']}\n\n"
    
    # Step 3: Feed real data into Claude to synthesize
    print("Analyzing with Claude...")
    message = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"""Using the following real-time research data, create a structured company brief for {company_name}.

Research Data:
{raw_data}

Format your response as:
## What They Do
## Key Customers
## Recent News
## Potential Pain Point
## Suggested Outreach Angle
"""
            }
        ]
    )
    
    return message.content[0].text

# Run it
result = research_company("Salesforce")
print(result)
