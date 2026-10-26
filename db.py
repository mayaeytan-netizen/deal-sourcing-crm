import os
import sqlite3
from datetime import datetime, timedelta

from agent_identity import AGENT_NAME, COMPANY_NAME, HUMAN_NAME
from cadence import (
    INACTIVE_STAGES,
    CADENCE_INTERVAL_DAYS,
    CHECKIN_INTERVAL_DAYS,
    fmt_date,
    next_touch_after,
    parse_date,
)
from seed_data import SEED_COMPANIES

DB_PATH = os.environ.get("CRM_DB_PATH", os.path.join(os.path.dirname(__file__), "crm.db"))
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed_if_empty=True):
    is_new = not os.path.exists(DB_PATH)
    conn = get_db()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    _migrate(conn)

    if seed_if_empty:
        count = conn.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]
        if count == 0:
            _seed(conn)
    conn.close()
    return is_new


def _migrate(conn):
    """Adds columns introduced after a database was first created, without
    touching existing rows. CREATE TABLE IF NOT EXISTS (above) won't alter an
    already-existing table, so new columns land here instead."""
    touch_cols = {row["name"] for row in conn.execute("PRAGMA table_info(touches)")}
    if "reply_text" not in touch_cols:
        conn.execute("ALTER TABLE touches ADD COLUMN reply_text TEXT")
    if "reply_message_id" not in touch_cols:
        conn.execute("ALTER TABLE touches ADD COLUMN reply_message_id TEXT")
    if "email_message_id" not in touch_cols:
        conn.execute("ALTER TABLE touches ADD COLUMN email_message_id TEXT")

    company_cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
    if "awaiting_response" not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN awaiting_response INTEGER NOT NULL DEFAULT 0")

    # processed_messages originally had company_id REFERENCES companies(id)
    # ON DELETE CASCADE -- deleting a company wiped its dedup history, so
    # recreating a company with the same contact email made the inbox
    # checker treat old, already-handled replies as brand new and attach
    # them to the new company record. SQLite can't drop a foreign key with
    # ALTER TABLE, so rebuild the table without it, keeping existing rows.
    fk_rows = conn.execute("PRAGMA foreign_key_list(processed_messages)").fetchall()
    if fk_rows:
        conn.execute("ALTER TABLE processed_messages RENAME TO processed_messages_old")
        conn.execute(
            """CREATE TABLE processed_messages (
                   message_id   TEXT PRIMARY KEY,
                   company_id   INTEGER,
                   processed_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            "INSERT INTO processed_messages (message_id, company_id, processed_at) "
            "SELECT message_id, company_id, processed_at FROM processed_messages_old"
        )
        conn.execute("DROP TABLE processed_messages_old")

    conn.commit()


def _seed(conn):
    now = datetime.utcnow().isoformat()

    for entry in SEED_COMPANIES:
        today = datetime.utcnow().date()
        date_added = today - timedelta(days=entry["days_added"])
        total_touches = entry["total_touches"]
        last_touch_date = None
        if entry["days_last_touch"] is not None:
            last_touch_date = today - timedelta(days=entry["days_last_touch"])

        # Reconstruct a plausible touch date for each sent touch, walking
        # backwards from last_touch_date on the same weekly cadence the app
        # uses going forward.
        touch_dates = []
        if total_touches > 0 and last_touch_date is not None:
            touch_dates = [None] * total_touches
            touch_dates[-1] = last_touch_date
            for i in range(total_touches - 1, 0, -1):
                touch_dates[i - 1] = touch_dates[i] - timedelta(days=CADENCE_INTERVAL_DAYS)

        next_touch_date = None
        if entry["stage"] == "Declined":
            next_touch_date = next_touch_after(last_touch_date, CHECKIN_INTERVAL_DAYS)
        elif entry["stage"] not in INACTIVE_STAGES:
            if last_touch_date is not None:
                next_touch_date = next_touch_after(last_touch_date, CADENCE_INTERVAL_DAYS)
            else:
                next_touch_date = date_added

        cur = conn.execute(
            """INSERT INTO companies
               (name, contact_name, contact_email, website, notes, stage,
                date_added, last_touch_date, next_touch_date, total_touches,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry["name"],
                entry["contact_name"],
                entry["contact_email"],
                entry["website"],
                entry["notes"],
                entry["stage"],
                fmt_date(date_added),
                fmt_date(last_touch_date),
                fmt_date(next_touch_date),
                total_touches,
                now,
                now,
            ),
        )
        company_id = cur.lastrowid

        replied_stages = {"Responded", "Interested", "Closed"}
        for i, touch_date in enumerate(touch_dates, start=1):
            is_last = i == total_touches
            got_reply = 1 if (is_last and entry["stage"] in replied_stages) else 0
            subject, body = _seed_touch_copy(entry, i)
            conn.execute(
                """INSERT INTO touches
                   (company_id, touch_number, kind, subject, body, status, got_reply,
                    created_at, sent_at)
                   VALUES (?, ?, 'cadence', ?, ?, 'sent', ?, ?, ?)""",
                (
                    company_id,
                    i,
                    subject,
                    body,
                    got_reply,
                    datetime.combine(touch_date, datetime.min.time()).isoformat(),
                    datetime.combine(touch_date, datetime.min.time()).isoformat(),
                ),
            )

        # Companies that already replied get Dana's warm handoff to Maya
        # logged as sent too, so the demo tells the full story.
        if entry["stage"] in replied_stages and last_touch_date is not None:
            handoff_date = min(last_touch_date + timedelta(days=1), today)
            handoff_subject, handoff_body = _seed_handoff_copy(entry)
            conn.execute(
                """INSERT INTO touches
                   (company_id, touch_number, kind, subject, body, status, got_reply,
                    created_at, sent_at)
                   VALUES (?, ?, 'handoff', ?, ?, 'sent', 0, ?, ?)""",
                (
                    company_id,
                    total_touches + 1,
                    handoff_subject,
                    handoff_body,
                    datetime.combine(handoff_date, datetime.min.time()).isoformat(),
                    datetime.combine(handoff_date, datetime.min.time()).isoformat(),
                ),
            )
            conn.execute(
                "UPDATE companies SET total_touches = ?, last_touch_date = ? WHERE id = ?",
                (total_touches + 1, fmt_date(handoff_date), company_id),
            )

    conn.commit()


def _seed_touch_copy(entry, touch_number):
    name = entry["name"]
    contact = entry["contact_name"]
    greeting = f"Hi {contact}," if contact else "Hi there,"
    signature = f"Best,\n{AGENT_NAME}\n{COMPANY_NAME}"
    if touch_number == 1:
        subject = f"Quick intro -- {COMPANY_NAME} x {name}"
        body = (
            f"{greeting}\n\nMy name is {AGENT_NAME} and I work with {COMPANY_NAME} -- "
            f"we're looking to expand and collaborate with property management and "
            f"real estate teams on deal sourcing, and {name} came up as a strong "
            f"potential fit. Would you be open to a quick call?\n\n{signature}"
        )
    elif touch_number == 2:
        subject = f"Re: Quick intro -- {COMPANY_NAME} x {name}"
        body = (
            f"{greeting}\n\nFollowing up on my note from last week -- happy to send "
            "over a few examples of how we work with partners if useful.\n\n"
            f"{signature}"
        )
    else:
        subject = f"Still here when it's useful -- {name}"
        body = (
            f"{greeting}\n\nKeeping this short -- still think there's a good reason "
            f"for {name} and {COMPANY_NAME} to connect on deal sourcing. Let me know "
            f"if now isn't the right time.\n\n{signature}"
        )
    return subject, body


def _seed_handoff_copy(entry):
    name = entry["name"]
    contact = entry["contact_name"]
    greeting = f"Hi {contact}," if contact else "Hi there,"
    body = (
        f"{greeting}\n\nSo glad to hear back from you -- thanks for taking the time. "
        f"I'm going to connect you with {HUMAN_NAME} from {COMPANY_NAME}, who leads "
        f"these partnerships directly. {HUMAN_NAME} will reach out shortly to find a "
        f"time that works for a quick call.\n\nLooking forward to {name} and "
        f"{COMPANY_NAME} working together.\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"
    )
    subject = f"Great to connect -- looping in {HUMAN_NAME} from {COMPANY_NAME}"
    return subject, body
