-- Deal Sourcing CRM schema

CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    contact_name    TEXT,
    contact_email   TEXT,
    website         TEXT,
    notes           TEXT,
    stage           TEXT NOT NULL DEFAULT 'Not Contacted',
    date_added      TEXT NOT NULL,
    last_touch_date TEXT,
    next_touch_date TEXT,
    total_touches   INTEGER NOT NULL DEFAULT 0,
    awaiting_response INTEGER NOT NULL DEFAULT 0, -- 1 when a reply has come in that Dana hasn't drafted a response to yet
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS touches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    touch_number    INTEGER NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'cadence', -- cadence | handoff | checkin | conversation
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    variants        TEXT NOT NULL DEFAULT '[]', -- JSON array of {label, subject, body} alternates offered at draft time
    status          TEXT NOT NULL DEFAULT 'pending_review', -- pending_review | sent
    got_reply       INTEGER NOT NULL DEFAULT 0,
    reply_text      TEXT, -- what the prospect actually said, pasted in when marking this touch as replied-to
    reply_message_id TEXT, -- the inbound reply's Message-ID header, so the NEXT outbound email can thread onto it
    email_message_id TEXT, -- the Message-ID we generated when WE actually sent this touch over SMTP
    created_at      TEXT NOT NULL,
    sent_at         TEXT
);

-- NOTE: company_id here is informational only (NOT a foreign key with
-- cascade delete). This table is a permanent "we've already handled this
-- email" log -- it must survive a company being deleted, otherwise
-- recreating a company with the same contact email causes the inbox
-- checker to rediscover old already-handled replies as if they were new
-- and misattribute them to the new company record.
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id      TEXT PRIMARY KEY, -- the inbound email's Message-ID header, so the inbox checker never double-processes a reply
    company_id      INTEGER,
    processed_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_touches_company ON touches(company_id);
CREATE INDEX IF NOT EXISTS idx_touches_status ON touches(status);
CREATE INDEX IF NOT EXISTS idx_companies_stage ON companies(stage);
CREATE INDEX IF NOT EXISTS idx_companies_next_touch ON companies(next_touch_date);
