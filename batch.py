import pandas as pd

from agent import research_company
from database import company_exists


def process_batch(
    companies: list[str],
    seller_profile: dict,
    seller_id: int,
    force_refresh: bool = False,
) -> list[dict]:
    """Research a list of companies and return result objects."""
    results = []
    total = len(companies)

    for i, company in enumerate(companies):
        company = company.strip()
        if not company:
            continue

        print(f"\n[{i + 1}/{total}] Processing {company}...")

        if not force_refresh and company_exists(company, seller_id=seller_id):
            print("  ⚡ Reusing fresh seller-scoped research run")

        try:
            result = research_company(
                company_name=company,
                seller_profile=seller_profile,
                seller_id=seller_id,
                force_refresh=force_refresh,
            )
            results.append(result)
        except Exception as exc:
            print(f"  ❌ Error researching {company}: {exc}")
            results.append(
                {
                    "company": company,
                    "error": str(exc),
                    "opportunity_score": 0.0,
                    "trigger_score": 0.0,
                    "confidence": "failed",
                    "pain_points": [],
                    "why_now_signals": [],
                }
            )

    return results


def results_to_dataframe(results: list[dict]) -> pd.DataFrame:
    rows = []
    for result in results:
        rows.append(
            {
                "Company": result.get("company", ""),
                "Opportunity Score": result.get("opportunity_score", 0),
                "Trigger Score": result.get("trigger_score", 0),
                "Confidence": result.get("confidence", ""),
                "Pain Points": " | ".join(result.get("pain_points", [])),
                "Why Now Signals": " | ".join(result.get("why_now_signals", [])),
                "Outreach Angle": result.get("outreach_angle", ""),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Opportunity Score", "Trigger Score"], ascending=False)
    return df
