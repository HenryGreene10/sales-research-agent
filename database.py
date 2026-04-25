import sqlite3
import json
from datetime import datetime

DB_FILE = "research.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
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
    """)
    conn.commit()
    conn.close()

def save_research(result: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO companies (
            company_name, what_they_do, key_customers,
            recent_news, pain_point, outreach_angle,
            opportunity_score, confidence, confidence_reason,
            raw_json, researched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.get("company"),
        result.get("what_they_do"),
        result.get("key_customers"),
        result.get("recent_news"),
        result.get("pain_point"),
        result.get("outreach_angle"),
        result.get("opportunity_score"),
        result.get("confidence"),
        result.get("confidence_reason"),
        json.dumps(result),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_all_companies():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT company_name, opportunity_score, confidence,
               pain_point, outreach_angle, researched_at
        FROM companies
        ORDER BY opportunity_score DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def company_exists(company_name: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM companies
        WHERE company_name = ?
        AND date(researched_at) = date('now')
    """, (company_name,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists
