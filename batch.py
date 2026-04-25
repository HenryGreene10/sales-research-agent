import pandas as pd
import json
import time
from agent import research_company
from database import init_db, save_research, company_exists

def process_batch(companies: list) -> list:
    """Research a list of companies and return results"""
    results = []
    total = len(companies)
    
    for i, company in enumerate(companies):
        company = company.strip()
        if not company:
            continue
            
        print(f"\n[{i+1}/{total}] Processing {company}...")
        
        # Skip if already researched today
        if company_exists(company):
            print(f"  ⚡ Already researched today, loading from database")
            continue
        
        try:
            result = research_company(company)
            save_research(result)
            results.append(result)
            
            # Small delay between companies to be nice to the APIs
            if i < total - 1:
                print(f"  ⏳ Waiting 2 seconds before next company...")
                time.sleep(2)
                
        except Exception as e:
            print(f"  ❌ Error researching {company}: {e}")
            results.append({
                "company": company,
                "error": str(e),
                "opportunity_score": 0,
                "confidence": "failed"
            })
    
    return results

def results_to_dataframe(results: list) -> pd.DataFrame:
    """Convert results to a pandas dataframe for display"""
    rows = []
    for r in results:
        rows.append({
            "Company": r.get("company", ""),
            "Score": r.get("opportunity_score", 0),
            "Confidence": r.get("confidence", ""),
            "Pain Point": r.get("pain_point", "")[:100] + "..." if r.get("pain_point") else "",
            "Outreach Angle": r.get("outreach_angle", "")[:100] + "..." if r.get("outreach_angle") else "",
        })
    
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Score", ascending=False)
    return df
