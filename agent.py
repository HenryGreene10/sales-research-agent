import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return None

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

try:
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError:
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def stop_after_attempt(*args, **kwargs):
        return None

    def wait_exponential(*args, **kwargs):
        return None

from database import (
    add_evidence_items,
    add_watchlist_event,
    complete_research_run,
    create_research_run,
    fail_research_run,
    get_company_entity_id,
    get_latest_research_run,
    get_recent_research_run,
    get_recent_research_run_for_resolution,
    get_run_evidence,
    get_similar_companies,
    normalize_company_name,
    touch_watchlist,
    upsert_company_resolution,
)

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

claude = (
    anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    if anthropic is not None and ANTHROPIC_API_KEY
    else None
)
tavily = TavilyClient(api_key=TAVILY_API_KEY) if TavilyClient is not None and TAVILY_API_KEY else None

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
NOW = lambda: datetime.now(timezone.utc)

BLOCKED_OFFICIAL_DOMAINS = {
    "linkedin.com",
    "www.linkedin.com",
    "wikipedia.org",
    "www.wikipedia.org",
    "crunchbase.com",
    "www.crunchbase.com",
    "pitchbook.com",
    "www.pitchbook.com",
    "bloomberg.com",
    "www.bloomberg.com",
    "facebook.com",
    "www.facebook.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}

SIGNAL_PATTERNS = {
    "funding": {
        "keywords": [
            "funding",
            "raised",
            "series a",
            "series b",
            "series c",
            "seed round",
            "investment",
            "venture capital",
        ],
        "weight": 2.0,
        "reason": "Recent capital usually increases budget, urgency, and the ability to buy."
    },
    "hiring_spike": {
        "keywords": [
            "hiring",
            "job opening",
            "open roles",
            "expanding team",
            "headcount",
            "careers",
        ],
        "weight": 1.6,
        "reason": "Hiring signals often indicate execution pressure, change, or new initiatives."
    },
    "product_launch": {
        "keywords": [
            "launch",
            "launched",
            "announced",
            "introduced",
            "released",
            "debuted",
            "rollout",
        ],
        "weight": 1.8,
        "reason": "Product launches often create integration, enablement, and scaling pain."
    },
    "leadership_change": {
        "keywords": [
            "appointed",
            "new ceo",
            "new cto",
            "new cfo",
            "joined as",
            "chief",
            "board",
        ],
        "weight": 1.5,
        "reason": "Leadership changes can create new priorities, budgets, and openness to vendors."
    },
    "market_expansion": {
        "keywords": [
            "expand",
            "expansion",
            "new market",
            "international",
            "opened office",
            "geographic growth",
        ],
        "weight": 1.7,
        "reason": "Expansion usually creates operational complexity and urgency."
    },
    "partnership_or_acquisition": {
        "keywords": [
            "partnership",
            "partnered",
            "alliance",
            "acquired",
            "acquisition",
            "merger",
        ],
        "weight": 1.7,
        "reason": "Partnerships and acquisitions often create transition and integration pain."
    },
    "regulatory_catalyst": {
        "keywords": [
            "regulatory",
            "compliance",
            "investigation",
            "lawsuit",
            "privacy",
            "security",
        ],
        "weight": 1.4,
        "reason": "Regulatory pressure can accelerate buying decisions."
    },
    "tech_stack_signal": {
        "keywords": [
            "stackshare",
            "builtwith",
            "integration",
            "api",
            "developer platform",
            "cloud",
            "data platform",
        ],
        "weight": 1.3,
        "reason": "Technology signals can indicate implementation fit or replacement opportunities."
    },
}

FRESHNESS_POLICIES = {
    "general_web_search": {"freshness_days": 180, "intent": "historical_context"},
    "recent_news": {"freshness_days": 30, "intent": "recent_signals"},
    "jobs_and_hiring": {"freshness_days": 90, "intent": "hiring_momentum"},
    "sec_filings": {"freshness_days": 180, "intent": "public_company_fundamentals"},
    "funding_and_momentum": {"freshness_days": 180, "intent": "private_company_momentum"},
    "website_positioning": {"freshness_days": 30, "intent": "messaging_and_positioning"},
    "tech_stack_signals": {"freshness_days": 120, "intent": "technical_fit"},
}


def utc_now_iso() -> str:
    return NOW().replace(microsecond=0).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_domain(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().replace("www.", "")


def _domain_is_blocked(domain: str) -> bool:
    return domain in BLOCKED_OFFICIAL_DOMAINS or domain.endswith(".wikipedia.org")


def _parse_json_response(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    cleaned = text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return fallback


@retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
def _tavily_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    if tavily is None:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    results = tavily.search(query=query, max_results=max_results)
    return results.get("results", [])


@retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
def _claude_message(model: str, prompt: str, max_tokens: int = 1000) -> str:
    if claude is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    response = claude.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _format_evidence_for_prompt(evidence_items: list[dict[str, Any]]) -> str:
    if not evidence_items:
        return "No evidence found."
    lines = []
    for item in evidence_items:
        lines.append(
            f"- [{item.get('tool_name', 'unknown')}] {item.get('title') or item.get('url')}: "
            f"{item.get('snippet', '')}"
        )
    return "\n".join(lines)


def _company_name_tokens(company_name: str) -> list[str]:
    stripped = normalize_company_name(company_name)
    suffixes = {
        "inc",
        "corp",
        "corporation",
        "company",
        "co",
        "llc",
        "ltd",
        "group",
        "holdings",
        "technologies",
        "technology",
    }
    return [token for token in stripped.split() if token not in suffixes]


def _score_resolution_candidate(company_name: str, item: dict[str, Any]) -> tuple[int, str, str]:
    url = item.get("url", "")
    title = (item.get("title") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    domain = _extract_domain(url)
    if not domain or _domain_is_blocked(domain):
        return (-999, domain, url)

    score = 0
    root_domain = domain.split(".")[0]
    company_tokens = _company_name_tokens(company_name)

    if any(token == root_domain or token in root_domain for token in company_tokens):
        score += 6
    if any(token in title for token in company_tokens):
        score += 3
    if any(token in snippet for token in company_tokens):
        score += 2
    if any(marker in title for marker in ["official", "homepage", "investor relations", "careers"]):
        score += 2
    if any(marker in domain for marker in ["news", "blog", "medium", "substack", "prnewswire"]):
        score -= 3
    if any(marker in title for marker in ["press release", "news", "funding", "announcement"]) and not any(
        token == root_domain or token in root_domain for token in company_tokens
    ):
        score -= 2

    return (score, domain, url)


def _official_candidate(company_name: str, evidence_items: list[dict[str, Any]]) -> tuple[str, str]:
    ranked = sorted(
        (_score_resolution_candidate(company_name, item) for item in evidence_items),
        key=lambda item: item[0],
        reverse=True,
    )
    for score, domain, url in ranked:
        if score > -999:
            return domain, url
    for item in evidence_items:
        domain = _extract_domain(item.get("url"))
        if domain and not _domain_is_blocked(domain):
            return domain, item.get("url", "")
    return "", ""


def _ticker_from_text(text: str) -> str | None:
    match = re.search(r"(NASDAQ|NYSE|AMEX)[:\s]+([A-Z.\-]{1,8})", text)
    if match:
        return match.group(2)
    return None


def _cik_from_text(text: str) -> str | None:
    match = re.search(r"\bCIK[:\s]+(\d{6,10})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _infer_company_type(full_text: str) -> str:
    if any(token in full_text for token in ["10-k", "10-q", "investor relations", "nasdaq", "nyse", "earnings call"]):
        return "public"
    if any(token in full_text for token in ["series a", "series b", "series c", "funding", "venture", "private company"]):
        return "private"
    if any(token in full_text for token in ["location", "hours", "yelp", "tripadvisor", "restaurant", "near me"]):
        return "local"
    return "unknown"


def _infer_industry(full_text: str) -> str | None:
    for keyword, label in [
        ("fintech", "Fintech"),
        ("healthcare", "Healthcare"),
        ("security", "Security"),
        ("data", "Data Infrastructure"),
        ("sales", "Sales Software"),
        ("payments", "Payments"),
        ("ai", "AI Software"),
        ("restaurant", "Local Services"),
        ("retail", "Retail"),
    ]:
        if keyword in full_text:
            return label
    return None


def _build_resolution_trace(company_name: str, evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace = []
    for item in evidence_items[:5]:
        score, domain, url = _score_resolution_candidate(company_name, item)
        trace.append(
            {
                "title": item.get("title", ""),
                "domain": domain,
                "url": url,
                "score": score,
            }
        )
    return trace


def _heuristic_resolution(company_name: str, evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    full_text = " ".join(
        f"{item.get('title', '')} {item.get('snippet', '')} {item.get('url', '')}"
        for item in evidence_items
    ).lower()
    domain, website = _official_candidate(company_name, evidence_items)
    ticker = _ticker_from_text(full_text.upper())
    cik = _cik_from_text(full_text)
    company_type = _infer_company_type(full_text)
    industry = _infer_industry(full_text)

    confidence = 0.45
    if website:
        confidence += 0.2
    if company_type != "unknown":
        confidence += 0.15
    if ticker or cik:
        confidence += 0.1

    return {
        "input_name": company_name,
        "normalized_name": normalize_company_name(company_name),
        "resolved_name": company_name.strip(),
        "domain": domain,
        "website": website,
        "company_type": company_type,
        "ticker": ticker,
        "cik": cik,
        "industry": industry,
        "confidence": round(min(confidence, 0.95), 2),
        "evidence": evidence_items[:3],
        "resolution_trace": _build_resolution_trace(company_name, evidence_items),
    }


def resolve_company(company_name: str) -> dict[str, Any]:
    query = (
        f"{company_name} official website company overview investor relations "
        f"{NOW().year}"
    )
    evidence_items: list[dict[str, Any]] = []
    try:
        results = _tavily_search(query, max_results=5)
        for result in results:
            evidence_items.append(
                {
                    "tool_name": "company_resolution",
                    "query": query,
                    "url": result.get("url", ""),
                    "title": result.get("title", ""),
                    "snippet": result.get("content", ""),
                    "retrieved_at": utc_now_iso(),
                    "metadata": {"source_type": "search"},
                }
            )
    except Exception as exc:
        resolution = _heuristic_resolution(company_name, [])
        resolution["evidence"] = [
            {
                "tool_name": "company_resolution",
                "query": query,
                "url": "",
                "title": "Resolution fallback",
                "snippet": f"Resolution search failed: {exc}",
                "retrieved_at": utc_now_iso(),
                "metadata": {"error": str(exc)},
            }
        ]
        resolution["resolution_trace"] = []
        return resolution

    fallback = _heuristic_resolution(company_name, evidence_items)
    prompt = f"""Resolve this company for downstream research routing.

Return ONLY valid JSON with these fields:
{{
  "input_name": "{company_name}",
  "normalized_name": "lowercase normalized string",
  "resolved_name": "best company name",
  "domain": "company domain or empty string",
  "website": "official website or empty string",
  "company_type": "public|private|local|unknown",
  "ticker": "ticker or null",
  "cik": "cik or null",
  "industry": "industry or null",
  "confidence": 0.0,
  "evidence": [],
  "resolution_trace": []
}}

Use the evidence below. Prefer the official company website over directory sites.
If you are unsure, keep fields empty and lower confidence.

Evidence:
{_format_evidence_for_prompt(evidence_items)}
"""

    try:
        resolved = _parse_json_response(_claude_message(HAIKU, prompt, max_tokens=700), fallback)
    except Exception:
        resolved = fallback

    resolved["input_name"] = company_name
    resolved["normalized_name"] = normalize_company_name(company_name)
    resolved["domain"] = _extract_domain(resolved.get("domain") or resolved.get("website"))
    resolved["website"] = resolved.get("website") or (
        f"https://{resolved['domain']}" if resolved.get("domain") else ""
    )
    resolved["confidence"] = round(_safe_float(resolved.get("confidence"), fallback["confidence"]), 2)
    resolved["evidence"] = evidence_items[:3]
    resolved["resolution_trace"] = _build_resolution_trace(company_name, evidence_items)
    if resolved.get("company_type") not in {"public", "private", "local", "unknown"}:
        resolved["company_type"] = fallback["company_type"]
    if not resolved.get("domain") and fallback.get("domain"):
        resolved["domain"] = fallback["domain"]
        resolved["website"] = fallback.get("website") or resolved.get("website")
    if not resolved.get("industry"):
        resolved["industry"] = fallback.get("industry")
    return resolved


def _freshness_clause(tool_name: str) -> str:
    freshness_days = FRESHNESS_POLICIES.get(tool_name, {}).get("freshness_days", 90)
    return f"last {freshness_days} days"


def build_research_plan(
    resolution: dict[str, Any],
    seller_profile: dict[str, Any],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or NOW()
    company_name = resolution.get("resolved_name") or resolution.get("input_name", "")
    domain = resolution.get("domain")
    company_type = resolution.get("company_type", "unknown")
    year = now.year
    product_context = seller_profile.get("product_description", "")
    target_context = seller_profile.get("target_industries", "")

    plan = [
        {
            "tool_name": "general_web_search",
            "query": f"{company_name} company overview products customers {_freshness_clause('general_web_search')} {year}",
            "max_results": 4,
        },
        {
            "tool_name": "recent_news",
            "query": f"{company_name} latest news announcements partnerships {_freshness_clause('recent_news')} {year}",
            "max_results": 4,
        },
        {
            "tool_name": "jobs_and_hiring",
            "query": (
                f"{company_name} jobs hiring careers growth {_freshness_clause('jobs_and_hiring')} "
                f"site:linkedin.com OR site:greenhouse.io OR site:lever.co"
            ),
            "max_results": 4,
        },
    ]

    if company_type == "public":
        plan.append(
            {
                "tool_name": "sec_filings",
                "query": f"{company_name} 10-K 10-Q earnings call revenue guidance {_freshness_clause('sec_filings')} {year}",
                "max_results": 4,
            }
        )
    if company_type in {"private", "unknown"}:
        plan.append(
            {
                "tool_name": "funding_and_momentum",
                "query": f"{company_name} funding raised investment momentum {_freshness_clause('funding_and_momentum')} {year}",
                "max_results": 4,
            }
        )
    if domain:
        plan.append(
            {
                "tool_name": "website_positioning",
                "query": f"site:{domain} {company_name} product platform customer pricing {_freshness_clause('website_positioning')}",
                "max_results": 4,
            }
        )
        if any(term in (product_context + " " + target_context).lower() for term in ["software", "data", "api", "platform", "ai", "security"]):
            plan.append(
                {
                    "tool_name": "tech_stack_signals",
                    "query": f"{company_name} tech stack integrations builtwith stackshare developers platform {_freshness_clause('tech_stack_signals')}",
                    "max_results": 4,
                }
            )

    # Deduplicate tool names while preserving order.
    seen = set()
    unique_plan = []
    for item in plan:
        if item["tool_name"] in seen:
            continue
        seen.add(item["tool_name"])
        unique_plan.append(item)
    return unique_plan


def _search_tool(plan_item: dict[str, Any]) -> dict[str, Any]:
    query = plan_item.get("query", "")
    tool_name = plan_item["tool_name"]
    retrieved_at = utc_now_iso()
    freshness = FRESHNESS_POLICIES.get(tool_name, {})
    results = _tavily_search(query, max_results=int(plan_item.get("max_results", 4)))
    evidence = [
        {
            "tool_name": tool_name,
            "query": query,
            "url": result.get("url", ""),
            "title": result.get("title", ""),
            "snippet": result.get("content", ""),
            "retrieved_at": retrieved_at,
            "metadata": {
                "tool_name": tool_name,
                "freshness_days": freshness.get("freshness_days"),
                "intent": freshness.get("intent"),
            },
        }
        for result in results
    ]
    return {
        "tool_name": tool_name,
        "query": query,
        "status": "ok",
        "retrieved_at": retrieved_at,
        "freshness_days": freshness.get("freshness_days"),
        "intent": freshness.get("intent"),
        "evidence": evidence,
        "error": None,
    }


def execute_tool_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for item in plan:
        try:
            results.append(_search_tool(item))
        except Exception as exc:
            results.append(
                {
                    "tool_name": item["tool_name"],
                    "query": item.get("query", ""),
                    "status": "error",
                    "retrieved_at": utc_now_iso(),
                    "evidence": [],
                    "error": str(exc),
                }
            )
    return results


def _flatten_evidence(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for result in tool_results:
        items.extend(result.get("evidence", []))
    return items


def detect_why_now_signals(tool_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    signals: list[dict[str, Any]] = []
    seen_types: set[tuple[str, str]] = set()

    for evidence in _flatten_evidence(tool_results):
        text = f"{evidence.get('title', '')} {evidence.get('snippet', '')}".lower()
        for signal_type, config in SIGNAL_PATTERNS.items():
            if not any(keyword in text for keyword in config["keywords"]):
                continue
            key = (signal_type, evidence.get("url", ""))
            if key in seen_types:
                continue
            signals.append(
                {
                    "type": signal_type,
                    "signal": f"{signal_type.replace('_', ' ').title()} detected from {evidence.get('title') or evidence.get('url')}",
                    "reason": config["reason"],
                    "evidence_url": evidence.get("url"),
                    "evidence_snippet": evidence.get("snippet"),
                    "weight": config["weight"],
                }
            )
            seen_types.add(key)

    if not signals:
        return [], 0.0

    total_weight = sum(signal["weight"] for signal in signals[:5])
    trigger_score = min(10.0, round(3.5 + total_weight, 1))
    return signals[:5], trigger_score


def _aggregate_sources(evidence_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    sources = []
    seen = set()
    for item in evidence_items:
        url = item.get("url")
        if not url or url in seen:
            continue
        sources.append(
            {
                "tool_name": item.get("tool_name", "unknown"),
                "url": url,
                "title": item.get("title") or url,
            }
        )
        seen.add(url)
    return sources


def _snippets_for_tool(evidence_items: list[dict[str, Any]], tool_name: str) -> list[str]:
    snippets = []
    for item in evidence_items:
        if item.get("tool_name") != tool_name:
            continue
        snippet = " ".join(
            part.strip()
            for part in [item.get("title", ""), item.get("snippet", "")]
            if part and part.strip()
        )
        if snippet:
            snippets.append(snippet)
    return snippets


def _source_urls_for_tool(evidence_items: list[dict[str, Any]], tool_name: str) -> set[str]:
    return {
        item.get("url", "")
        for item in evidence_items
        if item.get("tool_name") == tool_name and item.get("url")
    }


def compare_run_changes(previous_brief: dict[str, Any], latest_brief: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    previous_signals = set(previous_brief.get("why_now_signals", []))
    latest_signals = set(latest_brief.get("why_now_signals", []))
    new_signals = sorted(latest_signals - previous_signals)
    if new_signals:
        events.append(
            {
                "event_type": "new_signal",
                "title": f"New why-now trigger for {latest_brief.get('company')}",
                "summary": "; ".join(new_signals),
                "payload": {"new_signals": new_signals},
            }
        )

    previous_score = _safe_float(previous_brief.get("opportunity_score"), 0.0)
    latest_score = _safe_float(latest_brief.get("opportunity_score"), 0.0)
    score_delta = round(latest_score - previous_score, 1)
    if abs(score_delta) >= 1.0:
        direction = "increased" if score_delta > 0 else "decreased"
        events.append(
            {
                "event_type": "score_change",
                "title": f"Opportunity score {direction} for {latest_brief.get('company')}",
                "summary": f"Score moved from {previous_score} to {latest_score}.",
                "payload": {
                    "previous_score": previous_score,
                    "latest_score": latest_score,
                    "delta": score_delta,
                },
            }
        )

    previous_trigger = _safe_float(previous_brief.get("trigger_score"), 0.0)
    latest_trigger = _safe_float(latest_brief.get("trigger_score"), 0.0)
    trigger_delta = round(latest_trigger - previous_trigger, 1)
    if abs(trigger_delta) >= 1.0:
        direction = "increased" if trigger_delta > 0 else "decreased"
        events.append(
            {
                "event_type": "trigger_score_change",
                "title": f"Trigger score {direction} for {latest_brief.get('company')}",
                "summary": f"Trigger score moved from {previous_trigger} to {latest_trigger}.",
                "payload": {
                    "previous_trigger_score": previous_trigger,
                    "latest_trigger_score": latest_trigger,
                    "delta": trigger_delta,
                },
            }
        )

    return events


def compare_run_evidence_changes(
    previous_evidence: list[dict[str, Any]],
    latest_evidence: list[dict[str, Any]],
    company_name: str,
) -> list[dict[str, Any]]:
    events = []

    previous_website = set(_snippets_for_tool(previous_evidence, "website_positioning"))
    latest_website = set(_snippets_for_tool(latest_evidence, "website_positioning"))
    new_website_messages = sorted(latest_website - previous_website)
    if new_website_messages:
        events.append(
            {
                "event_type": "website_messaging_change",
                "title": f"Website messaging changed for {company_name}",
                "summary": new_website_messages[0][:240],
                "payload": {"new_messaging_snippets": new_website_messages[:3]},
            }
        )

    previous_news_sources = _source_urls_for_tool(previous_evidence, "recent_news")
    latest_news_sources = _source_urls_for_tool(latest_evidence, "recent_news")
    new_news_sources = sorted(latest_news_sources - previous_news_sources)
    if new_news_sources:
        events.append(
            {
                "event_type": "new_source_detected",
                "title": f"New recent-news source detected for {company_name}",
                "summary": new_news_sources[0],
                "payload": {"new_source_urls": new_news_sources[:5]},
            }
        )

    return events


def _shared_context_terms(seller_profile: dict[str, Any], evidence_items: list[dict[str, Any]]) -> int:
    profile_text = " ".join(
        [
            seller_profile.get("product_description", ""),
            seller_profile.get("ideal_customer_profile", ""),
            seller_profile.get("target_industries", ""),
            seller_profile.get("past_wins", ""),
        ]
    ).lower()
    if not profile_text.strip():
        return 0
    profile_terms = {
        term
        for term in re.findall(r"[a-z]{4,}", profile_text)
        if term not in {"with", "that", "from", "this", "your", "have", "into", "their", "team"}
    }
    evidence_text = " ".join(
        f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        for item in evidence_items
    )
    return sum(1 for term in profile_terms if term in evidence_text)


def derive_score_components(
    resolution: dict[str, Any],
    seller_profile: dict[str, Any],
    tool_results: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    trigger_score: float,
    similar_examples: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_items = _flatten_evidence(tool_results)
    successful_tools = sum(1 for result in tool_results if result.get("status") == "ok")
    source_count = len(_aggregate_sources(evidence_items))
    shared_terms = _shared_context_terms(seller_profile, evidence_items)

    fit_score = 3.8
    if resolution.get("confidence", 0) >= 0.75:
        fit_score += 0.9
    if resolution.get("industry") and seller_profile.get("target_industries"):
        industry_text = seller_profile.get("target_industries", "").lower()
        if resolution["industry"].lower() in industry_text or any(
            token in resolution["industry"].lower() for token in industry_text.split(",")
        ):
            fit_score += 1.2
    fit_score += min(1.8, shared_terms * 0.3)
    fit_score += min(1.2, len(similar_examples) * 0.4)
    fit_score = min(10.0, round(fit_score, 1))

    evidence_score = min(10.0, round(2.5 + successful_tools * 1.0 + min(source_count, 8) * 0.35, 1))
    timing_score = min(10.0, round(2.8 + trigger_score * 0.7 + min(len(signals), 4) * 0.35, 1))
    opportunity_score = round(
        min(10.0, fit_score * 0.45 + timing_score * 0.4 + evidence_score * 0.15),
        1,
    )

    confidence_score = round(
        min(10.0, evidence_score * 0.55 + _safe_float(resolution.get("confidence"), 0.5) * 4.5),
        1,
    )
    if confidence_score >= 7.5:
        confidence = "high"
    elif confidence_score >= 5.5:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "fit_score": fit_score,
        "timing_score": timing_score,
        "evidence_score": evidence_score,
        "opportunity_score": opportunity_score,
        "confidence_score": confidence_score,
        "confidence": confidence,
        "confidence_reason": (
            f"Confidence is {confidence} because {successful_tools} tools returned data, "
            f"{source_count} distinct sources were captured, and resolution confidence was {resolution.get('confidence', 0)}."
        ),
    }


def build_account_snapshot(
    company_name: str,
    resolution: dict[str, Any],
    tool_results: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    score_components: dict[str, Any],
) -> dict[str, Any]:
    evidence_items = _flatten_evidence(tool_results)
    latest_sources = _aggregate_sources(evidence_items)[:5]
    return {
        "company": resolution.get("resolved_name") or company_name,
        "company_type": resolution.get("company_type"),
        "domain": resolution.get("domain"),
        "industry": resolution.get("industry"),
        "signal_types": [signal.get("type") for signal in signals],
        "top_signal_reasons": [signal.get("reason") for signal in signals[:3]],
        "source_count": len(latest_sources),
        "latest_source_titles": [source.get("title") for source in latest_sources],
        "score_components": {
            "fit_score": score_components.get("fit_score"),
            "timing_score": score_components.get("timing_score"),
            "evidence_score": score_components.get("evidence_score"),
            "confidence_score": score_components.get("confidence_score"),
        },
        "captured_at": utc_now_iso(),
    }


def build_score_explanation(
    company_name: str,
    score_components: dict[str, Any],
    signals: list[dict[str, Any]],
) -> list[str]:
    explanation = []
    fit_score = _safe_float(score_components.get("fit_score"), 0)
    timing_score = _safe_float(score_components.get("timing_score"), 0)
    evidence_score = _safe_float(score_components.get("evidence_score"), 0)

    if fit_score >= 7:
        explanation.append(f"{company_name} looks like a strong fit based on seller-context overlap and comparable account patterns.")
    elif fit_score >= 5:
        explanation.append(f"{company_name} appears to be a moderate fit, but the match is not strong enough to rely on without validation.")
    else:
        explanation.append(f"{company_name} does not yet show a strong fit signal from the available evidence.")

    if timing_score >= 7 and signals:
        top_signal_types = ", ".join(signal.get("type", "signal").replace("_", " ") for signal in signals[:2])
        explanation.append(f"Timing is elevated because recent signals were detected, especially around {top_signal_types}.")
    elif timing_score >= 5:
        explanation.append("Timing is plausible, but the why-now case is still moderate rather than urgent.")
    else:
        explanation.append("Timing looks weak right now because recent trigger evidence is limited.")

    if evidence_score >= 7:
        explanation.append("The evidence base is relatively strong, with multiple successful tools and distinct supporting sources.")
    elif evidence_score >= 5:
        explanation.append("The evidence base is usable but still somewhat thin or mixed.")
    else:
        explanation.append("The evidence base is weak, so this score should be treated as directional.")

    return explanation


def build_snapshot_delta(
    previous_snapshot: dict[str, Any] | None,
    latest_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_snapshot = previous_snapshot or {}
    latest_snapshot = latest_snapshot or {}
    previous_scores = (previous_snapshot.get("score_components") or {})
    latest_scores = (latest_snapshot.get("score_components") or {})

    delta = {
        "fit_score_change": round(
            _safe_float(latest_scores.get("fit_score"), 0) - _safe_float(previous_scores.get("fit_score"), 0),
            1,
        ),
        "timing_score_change": round(
            _safe_float(latest_scores.get("timing_score"), 0) - _safe_float(previous_scores.get("timing_score"), 0),
            1,
        ),
        "evidence_score_change": round(
            _safe_float(latest_scores.get("evidence_score"), 0) - _safe_float(previous_scores.get("evidence_score"), 0),
            1,
        ),
        "confidence_score_change": round(
            _safe_float(latest_scores.get("confidence_score"), 0) - _safe_float(previous_scores.get("confidence_score"), 0),
            1,
        ),
        "new_signal_types": sorted(
            set(latest_snapshot.get("signal_types", [])) - set(previous_snapshot.get("signal_types", []))
        ),
        "new_source_titles": sorted(
            set(latest_snapshot.get("latest_source_titles", [])) - set(previous_snapshot.get("latest_source_titles", []))
        ),
    }
    return delta


def _fallback_brief(
    company_name: str,
    resolution: dict[str, Any],
    seller_profile: dict[str, Any],
    tool_results: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    trigger_score: float,
) -> dict[str, Any]:
    sources = _aggregate_sources(_flatten_evidence(tool_results))
    snippets = [item.get("snippet", "") for item in _flatten_evidence(tool_results)[:4] if item.get("snippet")]
    summary = " ".join(snippets)[:500] or "Insufficient external data was retrieved."
    components = derive_score_components(
        resolution=resolution,
        seller_profile=seller_profile,
        tool_results=tool_results,
        signals=signals,
        trigger_score=trigger_score,
        similar_examples=[],
    )
    return {
        "company": resolution.get("resolved_name") or company_name,
        "resolved_company": resolution,
        "what_they_do": summary,
        "key_customers": "Unable to determine reliably from retrieved evidence.",
        "recent_news": summary,
        "pain_points": ["Need more evidence to determine specific pain points."],
        "why_now_signals": [signal["signal"] for signal in signals],
        "outreach_angle": (
            f"Lead with {seller_profile.get('product_description', 'your product')} "
            "and validate current priorities before pitching."
        ),
        "opportunity_score": components["opportunity_score"] if sources else 2.5,
        "fit_score": components["fit_score"] if sources else 2.5,
        "timing_score": components["timing_score"] if sources else (trigger_score or 2.0),
        "evidence_score": components["evidence_score"] if sources else 2.0,
        "trigger_score": trigger_score,
        "confidence": components["confidence"] if sources else "low",
        "confidence_reason": "Fallback summary was used because the synthesis step failed or external data was limited.",
        "score_rationale": "The score is based on partial evidence and should be treated as directional.",
        "sources": sources,
        "account_snapshot": build_account_snapshot(
            company_name=company_name,
            resolution=resolution,
            tool_results=tool_results,
            signals=signals,
            score_components=components,
        ),
        "score_explanation": build_score_explanation(
            company_name=resolution.get("resolved_name") or company_name,
            score_components=components,
            signals=signals,
        ),
    }


def synthesize_brief(
    company_name: str,
    resolution: dict[str, Any],
    seller_profile: dict[str, Any],
    tool_results: list[dict[str, Any]],
    similar_examples: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    trigger_score: float,
) -> dict[str, Any]:
    evidence_items = _flatten_evidence(tool_results)
    sources = _aggregate_sources(evidence_items)
    fallback = _fallback_brief(
        company_name,
        resolution,
        seller_profile,
        tool_results,
        signals,
        trigger_score,
    )

    tool_summary = []
    for result in tool_results:
        tool_summary.append(
            {
                "tool_name": result["tool_name"],
                "query": result.get("query"),
                "status": result.get("status"),
                "evidence_count": len(result.get("evidence", [])),
                "freshness_days": result.get("freshness_days"),
                "intent": result.get("intent"),
                "error": result.get("error"),
            }
        )

    derived_scores = derive_score_components(
        resolution=resolution,
        seller_profile=seller_profile,
        tool_results=tool_results,
        signals=signals,
        trigger_score=trigger_score,
        similar_examples=similar_examples,
    )

    prompt = f"""Write a commercial account brief from the seller's perspective.

Seller profile:
{json.dumps(seller_profile, indent=2)}

Resolved company:
{json.dumps(resolution, indent=2)}

Retrieved evidence:
{json.dumps(evidence_items[:18], indent=2)}

Similar benchmark examples:
{json.dumps(similar_examples, indent=2)}

Detected why-now signals:
{json.dumps(signals, indent=2)}

Computed trigger score: {trigger_score}

Reply with ONLY valid JSON in this shape:
{{
  "company": "{resolution.get('resolved_name') or company_name}",
  "what_they_do": "2 sentence summary",
  "key_customers": "buyer and customer summary",
  "recent_news": "most relevant recent development",
  "pain_points": ["pain point 1", "pain point 2"],
  "why_now_signals": ["signal 1", "signal 2"],
  "outreach_angle": "how to approach the account",
  "opportunity_score": 7.5,
  "fit_score": 7.2,
  "timing_score": 8.1,
  "evidence_score": 7.0,
  "confidence": "high",
  "confidence_reason": "why this is high/medium/low confidence",
  "score_rationale": "why this account ranks where it does"
}}

Rules:
- Base every claim on retrieved evidence.
- Be specific about sales relevance.
- Keep confidence low if the evidence is weak or conflicting.
- Use the detected signals if they are credible.
"""

    try:
        brief = _parse_json_response(_claude_message(SONNET, prompt, max_tokens=1200), fallback)
    except Exception:
        brief = fallback

    brief["company"] = brief.get("company") or resolution.get("resolved_name") or company_name
    brief["resolved_company"] = resolution
    brief["pain_points"] = brief.get("pain_points") or []
    if isinstance(brief["pain_points"], str):
        brief["pain_points"] = [brief["pain_points"]]
    brief["why_now_signals"] = brief.get("why_now_signals") or [signal["signal"] for signal in signals]
    if isinstance(brief["why_now_signals"], str):
        brief["why_now_signals"] = [brief["why_now_signals"]]
    llm_opportunity = _safe_float(brief.get("opportunity_score"), derived_scores["opportunity_score"])
    llm_fit = _safe_float(brief.get("fit_score"), derived_scores["fit_score"])
    llm_timing = _safe_float(brief.get("timing_score"), derived_scores["timing_score"])
    llm_evidence = _safe_float(brief.get("evidence_score"), derived_scores["evidence_score"])

    brief["fit_score"] = round((llm_fit + derived_scores["fit_score"]) / 2, 1)
    brief["timing_score"] = round((llm_timing + derived_scores["timing_score"]) / 2, 1)
    brief["evidence_score"] = round((llm_evidence + derived_scores["evidence_score"]) / 2, 1)
    brief["opportunity_score"] = round((llm_opportunity + derived_scores["opportunity_score"]) / 2, 1)
    brief["trigger_score"] = trigger_score
    brief["confidence"] = brief.get("confidence") or derived_scores["confidence"]
    brief["confidence_score"] = derived_scores["confidence_score"]
    if not brief.get("confidence_reason"):
        brief["confidence_reason"] = derived_scores["confidence_reason"]
    brief["sources"] = sources
    brief["score_explanation"] = build_score_explanation(
        company_name=brief["company"],
        score_components={
            "fit_score": brief["fit_score"],
            "timing_score": brief["timing_score"],
            "evidence_score": brief["evidence_score"],
        },
        signals=signals,
    )
    brief["account_snapshot"] = build_account_snapshot(
        company_name=company_name,
        resolution=resolution,
        tool_results=tool_results,
        signals=signals,
        score_components=derived_scores,
    )
    brief["tool_trace"] = tool_summary
    brief["decision_trace"] = [
        {
            "step": "resolve_company",
            "company_type": resolution.get("company_type"),
            "domain": resolution.get("domain"),
            "confidence": resolution.get("confidence"),
        },
        {
            "step": "tool_routing",
            "selected_tools": [item["tool_name"] for item in tool_summary],
            "freshness_policies": {
                item["tool_name"]: {
                    "freshness_days": item.get("freshness_days"),
                    "intent": item.get("intent"),
                }
                for item in tool_summary
            },
        },
        {
            "step": "benchmark_memory",
            "example_count": len(similar_examples),
        },
        {
            "step": "signal_detection",
            "signal_types": [signal["type"] for signal in signals],
            "trigger_score": trigger_score,
        },
        {
            "step": "score_components",
            "derived_scores": derived_scores,
        },
    ]
    brief["generated_at"] = utc_now_iso()
    return brief


def research_company(
    company_name: str,
    seller_profile: dict[str, Any],
    seller_id: int,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if not force_refresh:
        cached = get_recent_research_run(seller_id, company_name, max_age_hours=24)
        if cached and cached.get("final_brief"):
            cached_brief = cached["final_brief"]
            cached_brief["from_cache"] = True
            return cached_brief

    resolution = resolve_company(company_name)
    if not force_refresh:
        resolved_cached = get_recent_research_run_for_resolution(
            seller_id=seller_id,
            resolution=resolution,
            max_age_hours=24,
        )
        if resolved_cached and resolved_cached.get("final_brief"):
            cached_brief = resolved_cached["final_brief"]
            cached_brief["from_cache"] = True
            return cached_brief

    company_id = upsert_company_resolution(resolution)
    run_id = create_research_run(
        seller_id=seller_id,
        input_name=company_name,
        resolution=resolution,
        company_id=company_id,
    )

    try:
        normalized_name = normalize_company_name(company_name)
        similar_examples = get_similar_companies(
            seller_id=seller_id,
            company_id=get_company_entity_id(resolution) or company_id,
            normalized_name=normalized_name,
            company_type=resolution.get("company_type"),
        )

        plan = build_research_plan(resolution, seller_profile)
        tool_results = execute_tool_plan(plan)
        add_evidence_items(run_id, _flatten_evidence(tool_results))

        signals, trigger_score = detect_why_now_signals(tool_results)
        brief = synthesize_brief(
            company_name=company_name,
            resolution=resolution,
            seller_profile=seller_profile,
            tool_results=tool_results,
            similar_examples=similar_examples,
            signals=signals,
            trigger_score=trigger_score,
        )

        complete_research_run(run_id, brief, status="completed")
        return brief
    except Exception as exc:
        fail_research_run(run_id, str(exc))
        return {
            "company": company_name,
            "resolved_company": resolution,
            "pain_points": [],
            "why_now_signals": [],
            "outreach_angle": "Unable to generate because the research run failed.",
            "opportunity_score": 0.0,
            "fit_score": 0.0,
            "timing_score": 0.0,
            "trigger_score": 0.0,
            "confidence": "low",
            "confidence_reason": f"Research failed: {exc}",
            "score_rationale": "No score because the run failed.",
            "sources": [],
            "tool_trace": [],
            "decision_trace": [],
            "generated_at": utc_now_iso(),
            "error": str(exc),
        }


def monitor_watchlist(
    company_name: str,
    seller_profile: dict[str, Any],
    seller_id: int,
    watchlist_id: int,
) -> dict[str, Any]:
    previous_run = get_latest_research_run(seller_id, company_name)
    previous_brief = previous_run.get("final_brief") if previous_run else None
    previous_evidence = get_run_evidence(previous_run["id"]) if previous_run else []

    latest_brief = research_company(
        company_name=company_name,
        seller_profile=seller_profile,
        seller_id=seller_id,
        force_refresh=True,
    )
    latest_run = get_latest_research_run(seller_id, company_name)
    latest_run_id = latest_run.get("id") if latest_run else None
    latest_evidence = get_run_evidence(latest_run_id) if latest_run_id else []

    events = []
    if previous_brief is None:
        events.append(
            {
                "event_type": "initial_snapshot",
                "title": f"Watchlist started for {company_name}",
                "summary": "Initial watchlist snapshot created from the first research run.",
                "payload": {"opportunity_score": latest_brief.get("opportunity_score")},
            }
        )
    else:
        events.extend(compare_run_changes(previous_brief, latest_brief))
        events.extend(compare_run_evidence_changes(previous_evidence, latest_evidence, company_name))

    for event in events:
        add_watchlist_event(
            watchlist_id=watchlist_id,
            research_run_id=latest_run_id,
            event_type=event["event_type"],
            title=event["title"],
            summary=event["summary"],
            payload=event.get("payload"),
        )
    touch_watchlist(watchlist_id)

    return {
        "brief": latest_brief,
        "events": events,
        "watchlist_id": watchlist_id,
    }


if __name__ == "__main__":
    sample_seller = {
        "name": "Demo Seller",
        "product_description": "AI sales automation software",
        "ideal_customer_profile": "Revenue teams at software companies",
        "target_company_size": "Mid-Market",
        "target_industries": "B2B SaaS",
        "past_wins": "",
        "disqualifiers": "",
    }
    print(json.dumps(resolve_company("Stripe"), indent=2))
