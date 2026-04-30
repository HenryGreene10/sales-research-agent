import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_FILE = "research.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_company_name(company_name: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in company_name)
    return " ".join(normalized.split())


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, uri=DB_FILE.startswith("file:"))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_connection()
    cursor = conn.cursor()

    # Preserve the legacy table for old demo data while introducing v2 tables.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            what_they_do TEXT,
            key_customers TEXT,
            recent_news TEXT,
            pain_point TEXT,
            outreach_angle TEXT,
            opportunity_score INTEGER,
            confidence TEXT,
            confidence_reason TEXT,
            raw_json TEXT,
            researched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS seller_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            product_description TEXT NOT NULL,
            ideal_customer_profile TEXT,
            target_company_size TEXT,
            target_industries TEXT,
            past_wins TEXT,
            disqualifiers TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS company_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_name TEXT NOT NULL,
            resolved_name TEXT,
            domain TEXT NOT NULL DEFAULT '',
            website TEXT,
            company_type TEXT NOT NULL DEFAULT 'unknown',
            ticker TEXT,
            cik TEXT,
            industry TEXT,
            resolution_confidence REAL,
            resolution_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(normalized_name, domain)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS company_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(company_id, normalized_alias),
            FOREIGN KEY(company_id) REFERENCES company_entities(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            company_id INTEGER,
            input_name TEXT NOT NULL,
            normalized_input_name TEXT NOT NULL,
            status TEXT NOT NULL,
            resolved_company_json TEXT,
            final_brief_json TEXT,
            opportunity_score REAL,
            trigger_score REAL,
            confidence TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(seller_id) REFERENCES seller_profiles(id),
            FOREIGN KEY(company_id) REFERENCES company_entities(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            research_run_id INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            query_text TEXT,
            source_url TEXT,
            source_title TEXT,
            snippet TEXT,
            retrieved_at TEXT NOT NULL,
            metadata_json TEXT,
            FOREIGN KEY(research_run_id) REFERENCES research_runs(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS account_watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            company_id INTEGER,
            input_name TEXT NOT NULL,
            normalized_input_name TEXT NOT NULL,
            notes TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(seller_id, normalized_input_name),
            FOREIGN KEY(seller_id) REFERENCES seller_profiles(id),
            FOREIGN KEY(company_id) REFERENCES company_entities(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER NOT NULL,
            research_run_id INTEGER,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(watchlist_id) REFERENCES account_watchlists(id),
            FOREIGN KEY(research_run_id) REFERENCES research_runs(id)
        )
        """
    )

    conn.commit()
    conn.close()


def save_seller_profile(profile: dict[str, Any], seller_id: int | None = None) -> int:
    now = utc_now_iso()
    payload = (
        profile.get("name"),
        profile.get("product_description", "").strip(),
        profile.get("ideal_customer_profile", "").strip(),
        profile.get("target_company_size", "").strip(),
        profile.get("target_industries", "").strip(),
        profile.get("past_wins", "").strip(),
        profile.get("disqualifiers", "").strip(),
    )

    conn = _get_connection()
    cursor = conn.cursor()

    if seller_id:
        cursor.execute(
            """
            UPDATE seller_profiles
            SET name = ?, product_description = ?, ideal_customer_profile = ?,
                target_company_size = ?, target_industries = ?, past_wins = ?,
                disqualifiers = ?, updated_at = ?
            WHERE id = ?
            """,
            (*payload, now, seller_id),
        )
        saved_id = seller_id
    else:
        cursor.execute(
            """
            INSERT INTO seller_profiles (
                name, product_description, ideal_customer_profile,
                target_company_size, target_industries, past_wins,
                disqualifiers, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*payload, now, now),
        )
        saved_id = int(cursor.lastrowid)

    conn.commit()
    conn.close()
    return saved_id


def get_seller_profile(seller_id: int) -> dict[str, Any] | None:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM seller_profiles WHERE id = ?", (seller_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def _canonical_domain(domain: str | None, website: str | None) -> str:
    if domain:
        return domain.strip().lower()
    if website:
        host = website.replace("https://", "").replace("http://", "").split("/")[0]
        return host.lower()
    return ""


def upsert_company_resolution(resolution: dict[str, Any]) -> int:
    now = utc_now_iso()
    normalized_name = normalize_company_name(
        resolution.get("resolved_name") or resolution.get("input_name", "")
    )
    domain = _canonical_domain(resolution.get("domain"), resolution.get("website"))
    payload = (
        normalized_name,
        resolution.get("resolved_name"),
        domain,
        resolution.get("website"),
        resolution.get("company_type", "unknown"),
        resolution.get("ticker"),
        resolution.get("cik"),
        resolution.get("industry"),
        resolution.get("confidence"),
        json.dumps(resolution),
        now,
        now,
    )

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO company_entities (
            normalized_name, resolved_name, domain, website, company_type,
            ticker, cik, industry, resolution_confidence, resolution_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_name, domain) DO UPDATE SET
            resolved_name = excluded.resolved_name,
            website = excluded.website,
            company_type = excluded.company_type,
            ticker = excluded.ticker,
            cik = excluded.cik,
            industry = excluded.industry,
            resolution_confidence = excluded.resolution_confidence,
            resolution_json = excluded.resolution_json,
            updated_at = excluded.updated_at
        """,
        payload,
    )
    cursor.execute(
        """
        SELECT id FROM company_entities
        WHERE normalized_name = ? AND domain = ?
        """,
        (normalized_name, domain),
    )
    company_id = int(cursor.fetchone()["id"])

    aliases = {
        resolution.get("input_name", ""),
        resolution.get("resolved_name", ""),
    }
    for alias in aliases:
        alias = alias.strip()
        if not alias:
            continue
        cursor.execute(
            """
            INSERT OR IGNORE INTO company_aliases (
                company_id, alias, normalized_alias, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (company_id, alias, normalize_company_name(alias), now),
        )

    conn.commit()
    conn.close()
    return company_id


def get_company_entity_id(resolution: dict[str, Any]) -> int | None:
    normalized_name = normalize_company_name(
        resolution.get("resolved_name") or resolution.get("input_name", "")
    )
    domain = _canonical_domain(resolution.get("domain"), resolution.get("website"))
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id FROM company_entities
        WHERE normalized_name = ? AND domain = ?
        """,
        (normalized_name, domain),
    )
    row = cursor.fetchone()
    conn.close()
    return int(row["id"]) if row else None


def create_research_run(
    seller_id: int,
    input_name: str,
    resolution: dict[str, Any],
    status: str = "running",
    company_id: int | None = None,
) -> int:
    now = utc_now_iso()
    resolved_company_id = company_id or upsert_company_resolution(resolution)
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO research_runs (
            seller_id, company_id, input_name, normalized_input_name, status,
            resolved_company_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            seller_id,
            resolved_company_id,
            input_name.strip(),
            normalize_company_name(input_name),
            status,
            json.dumps(resolution),
            now,
            now,
        ),
    )
    run_id = int(cursor.lastrowid)
    conn.commit()
    conn.close()
    return run_id


def add_evidence_items(research_run_id: int, evidence_items: list[dict[str, Any]]):
    if not evidence_items:
        return

    conn = _get_connection()
    cursor = conn.cursor()
    for item in evidence_items:
        cursor.execute(
            """
            INSERT INTO evidence_items (
                research_run_id, tool_name, query_text, source_url,
                source_title, snippet, retrieved_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                research_run_id,
                item.get("tool_name", "unknown"),
                item.get("query"),
                item.get("url"),
                item.get("title"),
                item.get("snippet"),
                item.get("retrieved_at", utc_now_iso()),
                json.dumps(item.get("metadata", {})),
            ),
        )
    conn.commit()
    conn.close()


def complete_research_run(
    research_run_id: int,
    final_brief: dict[str, Any],
    status: str = "completed",
    error_message: str | None = None,
):
    now = utc_now_iso()
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE research_runs
        SET status = ?, final_brief_json = ?, opportunity_score = ?, trigger_score = ?,
            confidence = ?, error_message = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            json.dumps(final_brief),
            final_brief.get("opportunity_score"),
            final_brief.get("trigger_score"),
            final_brief.get("confidence"),
            error_message,
            now,
            research_run_id,
        ),
    )
    conn.commit()
    conn.close()


def fail_research_run(research_run_id: int, error_message: str):
    complete_research_run(
        research_run_id,
        final_brief={},
        status="failed",
        error_message=error_message,
    )


def get_recent_research_run(
    seller_id: int,
    company_name: str,
    max_age_hours: int = 24,
) -> dict[str, Any] | None:
    normalized_name = normalize_company_name(company_name)
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rr.*
        FROM research_runs rr
        WHERE rr.seller_id = ?
          AND rr.normalized_input_name = ?
          AND rr.status = 'completed'
          AND datetime(rr.created_at) >= datetime('now', ?)
        ORDER BY datetime(rr.created_at) DESC
        LIMIT 1
        """,
        (seller_id, normalized_name, f"-{max_age_hours} hours"),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("final_brief_json"):
        result["final_brief"] = json.loads(result["final_brief_json"])
    if result.get("resolved_company_json"):
        result["resolved_company"] = json.loads(result["resolved_company_json"])
    return result


def get_latest_research_run(seller_id: int, company_name: str) -> dict[str, Any] | None:
    normalized_name = normalize_company_name(company_name)
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rr.*
        FROM research_runs rr
        WHERE rr.seller_id = ?
          AND rr.normalized_input_name = ?
          AND rr.status = 'completed'
        ORDER BY datetime(rr.created_at) DESC
        LIMIT 1
        """,
        (seller_id, normalized_name),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("final_brief_json"):
        result["final_brief"] = json.loads(result["final_brief_json"])
    if result.get("resolved_company_json"):
        result["resolved_company"] = json.loads(result["resolved_company_json"])
    return result


def get_similar_companies(
    seller_id: int,
    company_id: int | None,
    normalized_name: str,
    company_type: str | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rr.id, rr.company_id, rr.opportunity_score, rr.final_brief_json,
               ce.resolved_name, ce.company_type
        FROM research_runs rr
        LEFT JOIN company_entities ce ON ce.id = rr.company_id
        WHERE rr.seller_id = ?
          AND rr.status = 'completed'
          AND rr.final_brief_json IS NOT NULL
        ORDER BY rr.opportunity_score DESC, datetime(rr.created_at) DESC
        """,
        (seller_id,),
    )

    matches: list[dict[str, Any]] = []
    seen_company_ids: set[int] = set()
    for row in cursor.fetchall():
        row_company_id = row["company_id"]
        if row_company_id and company_id and row_company_id == company_id:
            continue
        if row_company_id and row_company_id in seen_company_ids:
            continue
        brief = json.loads(row["final_brief_json"])
        brief_name = normalize_company_name(brief.get("company", ""))
        if brief_name == normalized_name:
            continue
        if company_type and row["company_type"] and row["company_type"] != company_type:
            continue
        matches.append(
            {
                "company": brief.get("company") or row["resolved_name"],
                "score": row["opportunity_score"] or 0,
                "pain_points": brief.get("pain_points") or [brief.get("pain_point")] if brief.get("pain_point") else [],
                "outreach_angle": brief.get("outreach_angle"),
                "score_rationale": brief.get("score_rationale"),
            }
        )
        if row_company_id:
            seen_company_ids.add(row_company_id)
        if len(matches) >= limit:
            break

    conn.close()
    return matches


def get_run_evidence(research_run_id: int) -> list[dict[str, Any]]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT tool_name, query_text, source_url, source_title, snippet, retrieved_at, metadata_json
        FROM evidence_items
        WHERE research_run_id = ?
        ORDER BY id ASC
        """,
        (research_run_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    evidence = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        evidence.append(item)
    return evidence


def get_all_research_runs() -> list[dict[str, Any]]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rr.id, rr.input_name, rr.opportunity_score, rr.trigger_score,
               rr.confidence, rr.created_at, rr.status, rr.final_brief_json,
               sp.name AS seller_name
        FROM research_runs rr
        LEFT JOIN seller_profiles sp ON sp.id = rr.seller_id
        ORDER BY datetime(rr.created_at) DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        item = dict(row)
        brief = json.loads(item["final_brief_json"]) if item.get("final_brief_json") else {}
        results.append(
            {
                "Run ID": item["id"],
                "Seller": item.get("seller_name") or "Unknown",
                "Company": brief.get("company") or item["input_name"],
                "Score": item.get("opportunity_score"),
                "Trigger Score": item.get("trigger_score"),
                "Confidence": item.get("confidence"),
                "Status": item.get("status"),
                "Pain Points": ", ".join(brief.get("pain_points", [])),
                "Outreach Angle": brief.get("outreach_angle"),
                "Researched At": item.get("created_at"),
            }
        )

    if results:
        return results

    # Legacy fallback so the UI can still display old demo data.
    legacy_conn = _get_connection()
    legacy_cursor = legacy_conn.cursor()
    legacy_cursor.execute(
        """
        SELECT company_name, opportunity_score, confidence,
               pain_point, outreach_angle, researched_at
        FROM companies
        ORDER BY datetime(researched_at) DESC
        """
    )
    legacy_rows = legacy_cursor.fetchall()
    legacy_conn.close()
    return [
        {
            "Run ID": None,
            "Seller": "Legacy",
            "Company": row["company_name"],
            "Score": row["opportunity_score"],
            "Trigger Score": None,
            "Confidence": row["confidence"],
            "Status": "legacy",
            "Pain Points": row["pain_point"] or "",
            "Outreach Angle": row["outreach_angle"] or "",
            "Researched At": row["researched_at"],
        }
        for row in legacy_rows
    ]


def create_or_update_watchlist(
    seller_id: int,
    company_name: str,
    company_id: int | None = None,
    notes: str = "",
) -> int:
    now = utc_now_iso()
    normalized_name = normalize_company_name(company_name)
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO account_watchlists (
            seller_id, company_id, input_name, normalized_input_name,
            notes, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(seller_id, normalized_input_name) DO UPDATE SET
            company_id = COALESCE(excluded.company_id, account_watchlists.company_id),
            notes = CASE
                WHEN excluded.notes != '' THEN excluded.notes
                ELSE account_watchlists.notes
            END,
            is_active = 1,
            updated_at = excluded.updated_at
        """,
        (
            seller_id,
            company_id,
            company_name.strip(),
            normalized_name,
            notes.strip(),
            now,
            now,
        ),
    )
    cursor.execute(
        """
        SELECT id FROM account_watchlists
        WHERE seller_id = ? AND normalized_input_name = ?
        """,
        (seller_id, normalized_name),
    )
    watchlist_id = int(cursor.fetchone()["id"])
    conn.commit()
    conn.close()
    return watchlist_id


def touch_watchlist(watchlist_id: int):
    now = utc_now_iso()
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE account_watchlists
        SET last_checked_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (now, now, watchlist_id),
    )
    conn.commit()
    conn.close()


def add_watchlist_event(
    watchlist_id: int,
    event_type: str,
    title: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    research_run_id: int | None = None,
):
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO watchlist_events (
            watchlist_id, research_run_id, event_type, title, summary,
            payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            watchlist_id,
            research_run_id,
            event_type,
            title,
            summary,
            json.dumps(payload or {}),
            utc_now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def get_watchlists(seller_id: int) -> list[dict[str, Any]]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT aw.*, ce.resolved_name, ce.domain
        FROM account_watchlists aw
        LEFT JOIN company_entities ce ON ce.id = aw.company_id
        WHERE aw.seller_id = ? AND aw.is_active = 1
        ORDER BY datetime(aw.updated_at) DESC, aw.input_name ASC
        """,
        (seller_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_watchlist_events(watchlist_id: int, limit: int = 20) -> list[dict[str, Any]]:
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM watchlist_events
        WHERE watchlist_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (watchlist_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    events = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        events.append(item)
    return events


def get_watchlist_by_company(seller_id: int, company_name: str) -> dict[str, Any] | None:
    normalized_name = normalize_company_name(company_name)
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM account_watchlists
        WHERE seller_id = ? AND normalized_input_name = ? AND is_active = 1
        LIMIT 1
        """,
        (seller_id, normalized_name),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def company_exists(company_name: str, seller_id: int, max_age_hours: int = 24) -> bool:
    return get_recent_research_run(seller_id, company_name, max_age_hours=max_age_hours) is not None


# Compatibility shim for older imports.
def save_research(result: dict):
    return result


# Compatibility shim for older imports.
def get_all_companies():
    rows = get_all_research_runs()
    return [
        (
            row["Company"],
            row["Score"],
            row["Confidence"],
            row["Pain Points"],
            row["Outreach Angle"],
            row["Researched At"],
        )
        for row in rows
    ]
