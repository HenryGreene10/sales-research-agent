import csv
import io
from typing import Any

try:
    import pandas as pd
except ImportError:
    pd = None

from agent import research_company
from database import company_exists


class _SimpleSeries(list):
    def __eq__(self, other: object):
        return [value == other for value in self]


class _SimpleDataFrame:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []

    @property
    def empty(self) -> bool:
        return not self._rows

    def sort_values(self, columns: list[str], ascending: bool = False):
        sorted_rows = sorted(
            self._rows,
            key=lambda row: tuple(row.get(column, 0) for column in columns),
            reverse=not ascending,
        )
        return _SimpleDataFrame(sorted_rows)

    def to_csv(self, index: bool = False) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=self.columns)
        writer.writeheader()
        writer.writerows(self._rows)
        return buffer.getvalue()

    def __getitem__(self, key: str) -> _SimpleSeries:
        return _SimpleSeries(row.get(key) for row in self._rows)


def _build_dataframe(rows: list[dict[str, Any]]):
    if pd is not None:
        return pd.DataFrame(rows)
    return _SimpleDataFrame(rows)


def process_batch(
    companies: list[str],
    seller_profile: dict,
    seller_id: int,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
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


def summarize_batch_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    failed_results = [result for result in results if result.get("error")]
    cached_results = [result for result in results if result.get("from_cache")]
    successful_results = [result for result in results if not result.get("error")]
    return {
        "total": len(results),
        "successful": len(successful_results),
        "failed": len(failed_results),
        "cached": len(cached_results),
        "failed_companies": [result.get("company", "") for result in failed_results if result.get("company")],
    }


def results_to_dataframe(results: list[dict[str, Any]]) -> Any:
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
                "Error": result.get("error", ""),
            }
        )

    df = _build_dataframe(rows)
    if not df.empty:
        df = df.sort_values(["Opportunity Score", "Trigger Score"], ascending=False)
    return df
