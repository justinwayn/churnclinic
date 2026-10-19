"""SQLite storage layer. No ORM — plain sqlite3, $0, zero dependencies."""
import json
import secrets
import sqlite3
import time
from typing import Any, Optional

import config


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS cancellations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stripe_subscription_id TEXT UNIQUE,
                stripe_customer_id TEXT,
                customer_email TEXT,
                customer_name TEXT,
                plan_name TEXT,
                mrr_cents INTEGER DEFAULT 0,
                canceled_at INTEGER,
                survey_token TEXT UNIQUE,
                reason TEXT,
                reason_detail TEXT,
                surveyed_at INTEGER,
                status TEXT DEFAULT 'canceled',      -- canceled|saved|paused
                saved_at INTEGER,
                pause_months INTEGER,
                pause_stripe TEXT,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS drip_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cancellation_id INTEGER,
                email_key TEXT,
                scheduled_for INTEGER,
                sent_at INTEGER,
                UNIQUE(cancellation_id, email_key)
            );
            CREATE TABLE IF NOT EXISTS templates (
                email_key TEXT PRIMARY KEY,
                subject TEXT,
                body TEXT,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS pro_interest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s','now'))
            );
            """
        )
        # Migration for DBs created before pause_stripe existed: record of
        # what the Stripe pause attempt actually did ("stripe_paused" or
        # "recorded_only: <reason>").
        cols = [r["name"] for r in c.execute("PRAGMA table_info(cancellations)")]
        if "pause_stripe" not in cols:
            c.execute("ALTER TABLE cancellations ADD COLUMN pause_stripe TEXT")


def save_pro_interest(email: str) -> bool:
    """Notify-me signup for Clinic Pro before checkout is configured."""
    email = (email or "").strip()[:254]
    if "@" not in email or "." not in email.split("@")[-1]:
        return False
    with _conn() as c:
        c.execute("INSERT INTO pro_interest (email) VALUES (?)", (email,))
    return True


# ---------- cancellations ----------

def create_cancellation(data: dict) -> dict:
    token = secrets.token_urlsafe(16)
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO cancellations
               (stripe_subscription_id, stripe_customer_id, customer_email,
                customer_name, plan_name, mrr_cents, canceled_at, survey_token)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(stripe_subscription_id) DO NOTHING""",
            (
                data.get("stripe_subscription_id"),
                data.get("stripe_customer_id"),
                data.get("customer_email"),
                data.get("customer_name"),
                data.get("plan_name"),
                data.get("mrr_cents", 0),
                data.get("canceled_at", int(time.time())),
                token,
            ),
        )
        row = c.execute(
            "SELECT * FROM cancellations WHERE stripe_subscription_id = ?",
            (data.get("stripe_subscription_id"),),
        ).fetchone()
        _ = cur
    return dict(row)


def get_cancellation(cid: int) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM cancellations WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def get_by_token(token: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM cancellations WHERE survey_token = ?", (token,)
        ).fetchone()
    return dict(row) if row else None


def record_survey(token: str, reason: str, detail: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            """UPDATE cancellations SET reason = ?, reason_detail = ?,
               surveyed_at = strftime('%s','now') WHERE survey_token = ?""",
            (reason, detail, token),
        )
    return cur.rowcount > 0


def mark_saved(stripe_customer_id: str, mrr_cents: int = 0) -> bool:
    """Customer resubscribed -> the cancellation is a save."""
    with _conn() as c:
        cur = c.execute(
            """UPDATE cancellations SET status = 'saved',
               saved_at = strftime('%s','now')
               WHERE stripe_customer_id = ? AND status = 'canceled'""",
            (stripe_customer_id,),
        )
    return cur.rowcount > 0


def mark_paused(token: str, months: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            """UPDATE cancellations SET status = 'paused', pause_months = ?
               WHERE survey_token = ? AND status = 'canceled'""",
            (months, token),
        )
    return cur.rowcount > 0


def mark_pause_result(token: str, result: str) -> None:
    """Record what the Stripe pause attempt did, so claims stay honest."""
    with _conn() as c:
        c.execute(
            "UPDATE cancellations SET pause_stripe = ? WHERE survey_token = ?",
            (result[:250], token),
        )


def list_cancellations(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM cancellations ORDER BY canceled_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- drip ----------

def schedule_drip(cancellation_id: int) -> None:
    now = int(time.time())
    with _conn() as c:
        for key, days in config.DRIP_SCHEDULE:
            c.execute(
                """INSERT OR IGNORE INTO drip_sends
                   (cancellation_id, email_key, scheduled_for)
                   VALUES (?,?,?)""",
                (cancellation_id, key, now + days * 86400),
            )


def due_drips(now: Optional[int] = None) -> list[dict]:
    now = now or int(time.time())
    with _conn() as c:
        rows = c.execute(
            """SELECT d.*, c.customer_email, c.customer_name, c.status,
                      c.plan_name, c.mrr_cents, c.survey_token
               FROM drip_sends d
               JOIN cancellations c ON c.id = d.cancellation_id
               WHERE d.sent_at IS NULL AND d.scheduled_for <= ?
                 AND c.status = 'canceled'
               ORDER BY d.scheduled_for""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_drip_sent(drip_id: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE drip_sends SET sent_at = strftime('%s','now') WHERE id = ?",
            (drip_id,),
        )


# ---------- templates ----------

DEFAULT_TEMPLATES = {
    "acknowledge": {
        "subject": "Sorry to see you go, {name} — quick question?",
        "body": (
            "Hi {name},\n\n"
            "I saw you canceled {product}. No hard feelings — but I'd love "
            "to know why, so I can make it better.\n\n"
            "It takes 20 seconds: {survey_url}\n\n"
            "Thanks either way,\n{founder}"
        ),
    },
    "help_pause": {
        "subject": "Stuck on something? Or just need a break?",
        "body": (
            "Hi {name},\n\n"
            "Quick follow-up on your {product} cancellation.\n\n"
            "If something wasn't working, reply and tell me — I fix things fast.\n"
            "If you just need a breather, you can pause instead of canceling: "
            "{pause_url}\n\n"
            "Either way, the door's open.\n{founder}"
        ),
    },
    "discount_call": {
        "subject": "One last thing before you go",
        "body": (
            "Hi {name},\n\n"
            "Last note, I promise.\n\n"
            "If price was the issue, here's 30% off for 3 months — no strings: "
            "just reply \"YES\" and I'll apply it.\n\n"
            "Or if you'd rather talk it through, grab 15 minutes with me: "
            "reply and we'll find a time.\n\n"
            "Thanks for giving {product} a shot.\n{founder}"
        ),
    },
}


def seed_templates() -> None:
    with _conn() as c:
        for key, t in DEFAULT_TEMPLATES.items():
            c.execute(
                "INSERT OR IGNORE INTO templates (email_key, subject, body) VALUES (?,?,?)",
                (key, t["subject"], t["body"]),
            )


def get_template(key: str) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM templates WHERE email_key = ?", (key,)
        ).fetchone()
    if row:
        return dict(row)
    d = DEFAULT_TEMPLATES[key]
    return {"email_key": key, "subject": d["subject"], "body": d["body"]}


def set_template(key: str, subject: str, body: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO templates (email_key, subject, body, updated_at)
               VALUES (?,?,?,strftime('%s','now'))
               ON CONFLICT(email_key) DO UPDATE SET
                 subject=excluded.subject, body=excluded.body,
                 updated_at=strftime('%s','now')""",
            (key, subject, body),
        )


def all_templates() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM templates").fetchall()
    return [dict(r) for r in rows]


# ---------- stats ----------

REASONS = [
    ("too_expensive", "Too expensive"),
    ("missing_feature", "Missing a feature I need"),
    ("not_using", "Wasn't using it enough"),
    ("switched_tool", "Switched to another tool"),
    ("technical_issue", "Technical issue / bug"),
    ("other", "Other"),
]


def stats() -> dict[str, Any]:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM cancellations").fetchone()["n"]
        saved = c.execute(
            "SELECT COUNT(*) n FROM cancellations WHERE status='saved'"
        ).fetchone()["n"]
        paused = c.execute(
            "SELECT COUNT(*) n FROM cancellations WHERE status='paused'"
        ).fetchone()["n"]
        surveyed = c.execute(
            "SELECT COUNT(*) n FROM cancellations WHERE reason IS NOT NULL"
        ).fetchone()["n"]
        recovered = c.execute(
            "SELECT COALESCE(SUM(mrr_cents),0) s FROM cancellations WHERE status='saved'"
        ).fetchone()["s"]
        lost = c.execute(
            "SELECT COALESCE(SUM(mrr_cents),0) s FROM cancellations WHERE status='canceled'"
        ).fetchone()["s"]
        by_reason = c.execute(
            """SELECT reason, COUNT(*) n FROM cancellations
               WHERE reason IS NOT NULL GROUP BY reason ORDER BY n DESC"""
        ).fetchall()
        recent = c.execute(
            """SELECT id, customer_email, customer_name, plan_name, mrr_cents,
                      reason, status, canceled_at, surveyed_at
               FROM cancellations ORDER BY canceled_at DESC LIMIT 25"""
        ).fetchall()
    label = {k: v for k, v in REASONS}
    return {
        "total_cancellations": total,
        "saved": saved,
        "paused": paused,
        "save_rate": round(saved / total * 100, 1) if total else 0.0,
        "survey_response_rate": round(surveyed / total * 100, 1) if total else 0.0,
        "recovered_mrr_cents": recovered,
        "lost_mrr_cents": lost,
        "reasons": [
            {"key": r["reason"], "label": label.get(r["reason"], r["reason"]),
             "count": r["n"]}
            for r in by_reason
        ],
        "recent": [dict(r) for r in recent],
    }
