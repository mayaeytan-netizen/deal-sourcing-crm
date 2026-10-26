import json
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify

import ai_drafting
import email_reader
import email_sender
import importer
from agent_identity import AGENT_NAME, COMPANY_NAME, HUMAN_NAME
from cadence import (
    STAGES,
    INACTIVE_STAGES,
    CADENCE_INTERVAL_DAYS,
    CHECKIN_INTERVAL_DAYS,
    due_status,
    fmt_date,
    next_touch_after,
    parse_date,
)
from db import get_db, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# Tuned for the light cream background: darker/more saturated than you'd
# use on a dark theme, so the badge text stays readable against white cards.
STAGE_COLORS = {
    "Not Contacted": "#64748b",
    "Contacted": "#2563eb",
    "Follow-up 1": "#0891b2",
    "Follow-up 2": "#0e7490",
    "Responded": "#7c3aed",
    "Interested": "#15803d",
    "Declined": "#c0392b",
    "Closed": "#b8860b",
}

ADVANCEABLE_STAGES = {"Not Contacted", "Contacted", "Follow-up 1", "Follow-up 2"}
STAGE_FOR_TOUCH = {1: "Contacted", 2: "Follow-up 1", 3: "Follow-up 2"}
INBOX_POLL_SECONDS = int(os.environ.get("INBOX_POLL_SECONDS", "45"))


def company_row_to_dict(row):
    return dict(row)


def annotate_company(c):
    c = dict(c)
    c["due_status"] = due_status(c.get("next_touch_date"))
    c["is_inactive"] = c["stage"] in INACTIVE_STAGES
    return c


def _find_thread_parent(conn, company_id, before_touch_id=None):
    """Returns (in_reply_to, references) for this company's next outbound
    email, so a real send lands in the same conversation instead of
    starting a new one.

    in_reply_to is just the most recent message in the exchange. references
    is the FULL chain of every Message-ID seen so far, in order -- Gmail can
    often thread correctly off a single In-Reply-To match, but Outlook/
    Exchange is stricter and wants the complete chain back to the original
    message to recognize a multi-hop thread (send -> their reply -> our
    reply -> ...), not just the immediate parent."""
    sql = """SELECT email_message_id, reply_message_id FROM touches WHERE company_id = ?"""
    params = [company_id]
    if before_touch_id is not None:
        sql += " AND id != ?"
        params.append(before_touch_id)
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql, params).fetchall()

    chain = []
    for row in rows:
        if row["email_message_id"]:
            chain.append(row["email_message_id"])
        if row["reply_message_id"]:
            chain.append(row["reply_message_id"])

    if not chain:
        return None, None
    return chain[-1], " ".join(chain)


def _original_thread_subject(conn, company_id):
    """The subject the very first email to this company went out with, with
    any leading 'Re:'/'Fwd:' stripped. Outlook (unlike Gmail) groups a
    conversation by matching BOTH the threading headers AND the normalized
    subject text -- correct In-Reply-To/References isn't enough on its own,
    a drafted reply with an unrelated invented subject still starts a new
    conversation there. So every reply in a thread reuses this, prefixed
    with 'Re:', rather than whatever subject the draft itself generated."""
    row = conn.execute(
        "SELECT subject FROM touches WHERE company_id = ? ORDER BY touch_number ASC, id ASC LIMIT 1",
        (company_id,),
    ).fetchone()
    if not row:
        return None
    return re.sub(r"^(re|fwd?)\s*:\s*", "", row["subject"], flags=re.IGNORECASE).strip()
    return row["reply_message_id"] or row["email_message_id"]


def _handle_new_reply(conn, company, reply_text):
    """Shared by both the manual 'mark got a reply' form and the automatic
    inbox checker: advances the stage, flags the company as awaiting a
    response (what makes it show up on the dashboard), and queues the right
    kind of draft -- the first-ever handoff, or a conversation continuation
    for anything after that. Returns a flash message string, or None."""
    was_advanceable = company["stage"] in ADVANCEABLE_STAGES
    if was_advanceable:
        conn.execute(
            "UPDATE companies SET stage='Responded', next_touch_date=NULL, updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), company["id"]),
        )

    conn.execute("UPDATE companies SET awaiting_response=1 WHERE id=?", (company["id"],))

    already_handed_off = conn.execute(
        "SELECT id FROM touches WHERE company_id = ? AND kind = 'handoff'", (company["id"],)
    ).fetchone()
    already_pending = conn.execute(
        "SELECT id FROM touches WHERE company_id = ? AND status = 'pending_review'", (company["id"],)
    ).fetchone()

    kind = None
    variants = None
    # A real question (e.g. "what does Daisy actually do?") gets answered
    # before any handoff, even on the very first reply -- introducing Maya
    # before answering what they actually asked would read as dodging it.
    # The handoff itself is gated on "has one gone out yet", not on the
    # stage at the moment of this specific reply -- stage already flips to
    # Responded on the first reply regardless of which kind of draft that
    # reply produces, so gating on the pre-reply stage would mean a
    # question-first reply could permanently skip the handoff entirely.
    still_live = company["stage"] not in ("Declined", "Closed")
    if reply_text and ai_drafting.is_asking_about_company(reply_text) and not already_pending:
        kind = "conversation"
        variants = ai_drafting.generate_reply_options(dict(company), reply_text)
    elif not already_handed_off and still_live:
        kind = "handoff"
        variants = ai_drafting.generate_handoff_options(dict(company))
    elif reply_text and not already_pending:
        kind = "conversation"
        variants = ai_drafting.generate_reply_options(dict(company), reply_text)

    if not variants:
        return None

    default = variants[0]
    conn.execute(
        """INSERT INTO touches (company_id, touch_number, kind, subject, body, variants, status, got_reply, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending_review', 0, ?)""",
        (
            company["id"],
            company["total_touches"] + 1,
            kind,
            default["subject"],
            default["body"],
            json.dumps(variants),
            datetime.utcnow().isoformat(),
        ),
    )
    if kind == "handoff":
        return f"{company['name']} is interested -- {AGENT_NAME} drafted a handoff to {HUMAN_NAME} in the review queue."
    return f"{company['name']} replied -- {AGENT_NAME} drafted a response in the review queue."


@app.before_request
def ensure_db():
    init_db()


@app.context_processor
def inject_pending_count():
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM touches WHERE status = 'pending_review'"
    ).fetchone()["c"]
    conn.close()
    return {
        "pending_count": count,
        "agent_name": AGENT_NAME,
        "company_name": COMPANY_NAME,
        "human_name": HUMAN_NAME,
        "smtp_configured": email_sender.is_configured(),
        "imap_configured": email_reader.is_configured(),
        "inbox_poll_seconds": INBOX_POLL_SECONDS,
    }


@app.route("/")
def dashboard():
    conn = get_db()
    companies = [annotate_company(r) for r in conn.execute("SELECT * FROM companies").fetchall()]

    funnel = OrderedDict((s, 0) for s in STAGES)
    for c in companies:
        funnel[c["stage"]] += 1

    due_counts = {"overdue": 0, "due_today": 0, "due_this_week": 0}
    for c in companies:
        if c["due_status"] in due_counts:
            due_counts[c["due_status"]] += 1

    # Weekly outreach volume, last 8 weeks (Mon-start buckets)
    sent_touches = conn.execute(
        "SELECT sent_at, got_reply FROM touches WHERE status = 'sent' ORDER BY sent_at"
    ).fetchall()

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    week_buckets = OrderedDict()
    for i in range(7, -1, -1):
        wk_start = this_monday - timedelta(weeks=i)
        week_buckets[wk_start] = 0
    for t in sent_touches:
        sent_date = datetime.fromisoformat(t["sent_at"]).date()
        wk_start = sent_date - timedelta(days=sent_date.weekday())
        if wk_start in week_buckets:
            week_buckets[wk_start] += 1

    total_sent = len(sent_touches)
    total_replies = sum(1 for t in sent_touches if t["got_reply"])
    response_rate = round((total_replies / total_sent) * 100, 1) if total_sent else 0.0

    pending_drafts = conn.execute(
        "SELECT COUNT(*) AS c FROM touches WHERE status = 'pending_review'"
    ).fetchone()["c"]

    # Companies with a reply Dana hasn't drafted/sent a response to yet --
    # this is the "keep talking to them" queue, surfaced right on the
    # dashboard so a new reply doesn't get missed.
    needs_response_rows = conn.execute(
        """SELECT companies.id, companies.name, companies.stage, companies.updated_at
           FROM companies WHERE companies.awaiting_response = 1
           ORDER BY companies.updated_at DESC"""
    ).fetchall()
    needs_response = []
    for row in needs_response_rows:
        last_reply = conn.execute(
            """SELECT reply_text, sent_at FROM touches
               WHERE company_id = ? AND reply_text IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()
        has_draft = conn.execute(
            "SELECT id FROM touches WHERE company_id = ? AND status = 'pending_review'",
            (row["id"],),
        ).fetchone()
        needs_response.append(
            {
                "id": row["id"],
                "name": row["name"],
                "stage": row["stage"],
                "reply_snippet": (last_reply["reply_text"][:140] if last_reply and last_reply["reply_text"] else None),
                "has_draft": bool(has_draft),
            }
        )

    conn.close()

    return render_template(
        "dashboard.html",
        funnel=funnel,
        funnel_colors=[STAGE_COLORS[s] for s in funnel.keys()],
        stage_colors=STAGE_COLORS,
        due_counts=due_counts,
        week_labels=[d.strftime("%b %d") for d in week_buckets.keys()],
        week_values=list(week_buckets.values()),
        total_sent=total_sent,
        total_replies=total_replies,
        response_rate=response_rate,
        pending_drafts=pending_drafts,
        total_companies=len(companies),
        needs_response=needs_response,
    )


def _companies_filter_sql(stage_filter, q, sort):
    sql = "SELECT * FROM companies WHERE 1=1"
    params = []
    if stage_filter:
        sql += " AND stage = ?"
        params.append(stage_filter)
    if q:
        sql += " AND (name LIKE ? OR contact_name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])

    sort_map = {
        "next_touch": "next_touch_date IS NULL, next_touch_date ASC",
        "name": "name ASC",
        "stage": "stage ASC",
        "last_touch": "last_touch_date IS NULL, last_touch_date DESC",
    }
    sql += " ORDER BY " + sort_map.get(sort, sort_map["next_touch"])
    return sql, params


@app.route("/companies")
def companies_list():
    stage_filter = request.args.get("stage", "")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "next_touch")

    sql, params = _companies_filter_sql(stage_filter, q, sort)
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    companies = [annotate_company(r) for r in rows]
    conn.close()

    return render_template(
        "companies.html",
        companies=companies,
        stages=STAGES,
        stage_colors=STAGE_COLORS,
        stage_filter=stage_filter,
        q=q,
        sort=sort,
    )


@app.route("/companies/export")
def export_companies():
    stage_filter = request.args.get("stage", "")
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "next_touch")

    sql, params = _companies_filter_sql(stage_filter, q, sort)
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    xlsx_bytes = importer.export_companies_to_xlsx(rows)
    filename = f"companies_export_{date.today().isoformat()}.xlsx"
    response = app.response_class(
        xlsx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.route("/companies/new", methods=["POST"])
def create_company():
    form = request.form
    now = datetime.utcnow().isoformat()
    today = fmt_date(date.today())
    conn = get_db()
    conn.execute(
        """INSERT INTO companies
           (name, contact_name, contact_email, website, notes, stage,
            date_added, last_touch_date, next_touch_date, total_touches,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'Not Contacted', ?, NULL, ?, 0, ?, ?)""",
        (
            form.get("name", "").strip(),
            form.get("contact_name", "").strip() or None,
            form.get("contact_email", "").strip() or None,
            form.get("website", "").strip() or None,
            form.get("notes", "").strip() or None,
            today,
            today,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    flash(f"Added {form.get('name')} to the pipeline.", "success")
    return redirect(url_for("companies_list"))


@app.route("/companies/import", methods=["POST"])
def import_companies():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a file to import first.", "error")
        return redirect(url_for("companies_list"))

    try:
        result = importer.parse_companies_file(upload.filename, upload.read())
    except importer.ImportError_ as exc:
        flash(str(exc), "error")
        return redirect(url_for("companies_list"))

    if not result["companies"]:
        flash("No companies found in that file -- check it has a company name column with rows under it.", "error")
        return redirect(url_for("companies_list"))

    now = datetime.utcnow().isoformat()
    today = date.today()
    conn = get_db()
    for c in result["companies"]:
        if c["stage"] == "Declined":
            next_touch = fmt_date(next_touch_after(today, CHECKIN_INTERVAL_DAYS))
        elif c["stage"] in INACTIVE_STAGES:
            next_touch = None
        else:
            next_touch = fmt_date(today)
        conn.execute(
            """INSERT INTO companies
               (name, contact_name, contact_email, website, notes, stage,
                date_added, last_touch_date, next_touch_date, total_touches,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, ?)""",
            (
                c["name"], c["contact_name"], c["contact_email"], c["website"], c["notes"], c["stage"],
                fmt_date(today), next_touch, now, now,
            ),
        )
    conn.commit()
    conn.close()

    msg = f"Imported {len(result['companies'])} companies from {upload.filename}."
    if result["skipped"]:
        msg += f" Skipped {result['skipped']} row(s) with no company name."
    flash(msg, "success")
    return redirect(url_for("companies_list"))


@app.route("/companies/<int:company_id>")
def company_detail(company_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if row is None:
        conn.close()
        flash("Company not found.", "error")
        return redirect(url_for("companies_list"))
    company = annotate_company(row)
    touch_rows = conn.execute(
        "SELECT * FROM touches WHERE company_id = ? ORDER BY touch_number ASC", (company_id,)
    ).fetchall()
    touches = [
        _annotate_draft(t) if t["status"] == "pending_review" else dict(t) for t in touch_rows
    ]
    has_pending_draft = any(t["status"] == "pending_review" for t in touches)
    conn.close()
    return render_template(
        "company_detail.html",
        company=company,
        touches=touches,
        stages=STAGES,
        stage_colors=STAGE_COLORS,
        has_pending_draft=has_pending_draft,
    )


@app.route("/companies/<int:company_id>/edit", methods=["POST"])
def update_company(company_id):
    form = request.form
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        """UPDATE companies SET name=?, contact_name=?, contact_email=?, website=?,
           notes=?, stage=?, updated_at=? WHERE id=?""",
        (
            form.get("name", "").strip(),
            form.get("contact_name", "").strip() or None,
            form.get("contact_email", "").strip() or None,
            form.get("website", "").strip() or None,
            form.get("notes", "").strip() or None,
            form.get("stage"),
            now,
            company_id,
        ),
    )
    # Moving manually into Responded/Interested/Closed stops the cadence
    # engine entirely. Declined is different: it's not a dead end, it gets
    # a long-interval check-in instead (see CHECKIN_INTERVAL_DAYS).
    new_stage = form.get("stage")
    if new_stage == "Declined":
        checkin_date = fmt_date(date.today() + timedelta(days=CHECKIN_INTERVAL_DAYS))
        conn.execute("UPDATE companies SET next_touch_date = ? WHERE id = ?", (checkin_date, company_id))
    elif new_stage in INACTIVE_STAGES:
        conn.execute("UPDATE companies SET next_touch_date = NULL WHERE id = ?", (company_id,))
    conn.commit()
    conn.close()
    flash("Company updated.", "success")
    return redirect(url_for("company_detail", company_id=company_id))


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
def delete_company(company_id):
    conn = get_db()
    conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    conn.commit()
    conn.close()
    flash("Company deleted.", "success")
    return redirect(url_for("companies_list"))


def _annotate_draft(row):
    """Parses a pending touch's stored variants JSON and figures out which
    option is currently selected + whether it's a placeholder. Shared by the
    review queue and the company page, since a pending draft needs the same
    interactive picker/approve UI in both places."""
    d = dict(row)
    try:
        d["variant_options"] = json.loads(d["variants"]) or []
    except (TypeError, ValueError):
        d["variant_options"] = []
    d["selected_index"] = 0
    for i, v in enumerate(d["variant_options"]):
        if v.get("subject") == d["subject"] and v.get("body") == d["body"]:
            d["selected_index"] = i
            break
    if d["variant_options"]:
        d["is_stub"] = bool(d["variant_options"][d["selected_index"]].get("is_stub"))
    else:
        d["is_stub"] = False
    return d


@app.route("/queue")
def review_queue():
    conn = get_db()
    draft_rows = conn.execute(
        """SELECT touches.*, companies.name AS company_name, companies.stage AS company_stage,
                  companies.contact_email AS company_email
           FROM touches JOIN companies ON touches.company_id = companies.id
           WHERE touches.status = 'pending_review'
           ORDER BY touches.created_at ASC"""
    ).fetchall()
    drafts = [_annotate_draft(row) for row in draft_rows]

    drafted_company_ids = {d["company_id"] for d in drafts}
    all_companies = [annotate_company(r) for r in conn.execute("SELECT * FROM companies").fetchall()]
    due_without_draft = [
        c
        for c in all_companies
        if c["due_status"] in ("overdue", "due_today", "due_this_week")
        and not c["is_inactive"]
        and c["id"] not in drafted_company_ids
    ]
    due_without_draft.sort(key=lambda c: (c["next_touch_date"] or ""))

    conn.close()
    return render_template(
        "queue.html",
        drafts=drafts,
        due_without_draft=due_without_draft,
        stage_colors=STAGE_COLORS,
    )


@app.route("/queue/generate/<int:company_id>", methods=["POST"])
def generate_draft(company_id):
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if company is None:
        conn.close()
        flash("Company not found.", "error")
        return redirect(request.referrer or url_for("review_queue"))

    existing = conn.execute(
        "SELECT id FROM touches WHERE company_id = ? AND status = 'pending_review'", (company_id,)
    ).fetchone()
    if existing:
        conn.close()
        flash("A draft is already pending review for this company.", "error")
        return redirect(request.referrer or url_for("review_queue"))

    touch_number = company["total_touches"] + 1
    is_checkin = company["stage"] == "Declined"
    kind = "checkin" if is_checkin else "cadence"
    if is_checkin:
        variants = ai_drafting.generate_checkin_options(dict(company))
    else:
        variants = ai_drafting.generate_draft_options(dict(company), touch_number)
    default = variants[(touch_number - 1) % len(variants)]

    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO touches (company_id, touch_number, kind, subject, body, variants, status, got_reply, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending_review', 0, ?)""",
        (company_id, touch_number, kind, default["subject"], default["body"], json.dumps(variants), now),
    )
    conn.commit()
    conn.close()
    label = "placeholder" if default.get("is_stub") else "AI"
    if is_checkin:
        flash(
            f"{AGENT_NAME} drafted {len(variants)} {label} check-in options for "
            f"{company['name']} (declined, following up after {CHECKIN_INTERVAL_DAYS} days).",
            "success",
        )
    else:
        flash(
            f"{AGENT_NAME} drafted {len(variants)} {label} options for touch #{touch_number} "
            f"with {company['name']}.",
            "success",
        )
    return redirect(request.referrer or url_for("review_queue"))


@app.route("/queue/<int:touch_id>/action", methods=["POST"])
def queue_action(touch_id):
    action = request.form.get("action")
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()

    conn = get_db()
    touch = conn.execute("SELECT * FROM touches WHERE id = ?", (touch_id,)).fetchone()
    if touch is None:
        conn.close()
        flash("Draft not found.", "error")
        return redirect(request.referrer or url_for("review_queue"))

    if action == "discard":
        conn.execute("DELETE FROM touches WHERE id = ?", (touch_id,))
        conn.commit()
        conn.close()
        flash("Draft discarded.", "success")
        return redirect(request.referrer or url_for("review_queue"))

    if action == "save":
        conn.execute("UPDATE touches SET subject=?, body=? WHERE id=?", (subject, body, touch_id))
        conn.commit()
        conn.close()
        flash("Draft saved.", "success")
        return redirect(request.referrer or url_for("review_queue"))

    if action == "approve":
        company = conn.execute(
            "SELECT * FROM companies WHERE id = ?", (touch["company_id"],)
        ).fetchone()

        really_send = request.form.get("really_send") == "1"
        sent_message_id = None
        if really_send:
            thread_parent, references_chain = _find_thread_parent(conn, company["id"], before_touch_id=touch_id)
            if thread_parent:
                # Keep the ORIGINAL thread's subject rather than whatever the
                # draft invented -- Outlook groups a conversation by matching
                # subject text in addition to the threading headers, so a
                # mismatched subject still starts a new conversation there
                # even with correct In-Reply-To/References.
                original_subject = _original_thread_subject(conn, company["id"])
                if original_subject:
                    subject = f"Re: {original_subject}"
                elif not subject.lower().startswith("re:"):
                    subject = f"Re: {subject}"
            try:
                sent_message_id = email_sender.send_email(
                    company["contact_email"], subject, body,
                    in_reply_to=thread_parent, references=references_chain,
                )
            except Exception as exc:
                conn.close()
                flash(f"Didn't send -- {exc}", "error")
                return redirect(request.referrer or url_for("review_queue"))

        now = datetime.utcnow()
        today = now.date()

        conn.execute(
            "UPDATE touches SET subject=?, body=?, status='sent', sent_at=?, email_message_id=? WHERE id=?",
            (subject, body, now.isoformat(), sent_message_id, touch_id),
        )

        new_total = company["total_touches"] + 1
        new_stage = company["stage"]
        if touch["kind"] == "cadence" and company["stage"] in ADVANCEABLE_STAGES:
            candidate = STAGE_FOR_TOUCH.get(touch["touch_number"], "Follow-up 2")
            if STAGES.index(candidate) > STAGES.index(company["stage"]):
                new_stage = candidate

        next_touch = None
        if touch["kind"] == "cadence" and new_stage not in INACTIVE_STAGES:
            next_touch = fmt_date(next_touch_after(today, CADENCE_INTERVAL_DAYS))
        elif touch["kind"] == "checkin" and new_stage == "Declined":
            next_touch = fmt_date(next_touch_after(today, CHECKIN_INTERVAL_DAYS))

        conn.execute(
            """UPDATE companies SET last_touch_date=?, next_touch_date=?, total_touches=?,
               stage=?, awaiting_response=0, updated_at=? WHERE id=?""",
            (fmt_date(today), next_touch, new_total, new_stage, now.isoformat(), company["id"]),
        )
        conn.commit()
        conn.close()
        verb = {
            "handoff": "handoff",
            "checkin": "check-in",
            "conversation": "reply",
        }.get(touch["kind"], "touch")
        if really_send:
            flash(f"Sent to {company['contact_email']} and logged the {verb} for {company['name']}.", "success")
        else:
            flash(f"Marked sent and logged the {verb} for {company['name']}.", "success")
        return redirect(request.referrer or url_for("review_queue"))

    conn.close()
    flash("Unknown action.", "error")
    return redirect(request.referrer or url_for("review_queue"))


@app.route("/touches/<int:touch_id>/reply", methods=["POST"])
def toggle_reply(touch_id):
    conn = get_db()
    touch = conn.execute("SELECT * FROM touches WHERE id = ?", (touch_id,)).fetchone()
    if touch is None:
        conn.close()
        flash("Touch not found.", "error")
        return redirect(request.referrer or url_for("companies_list"))

    reply_text = email_reader._strip_quoted_reply(request.form.get("reply_text", "").strip())
    new_value = 0 if touch["got_reply"] else 1
    conn.execute(
        "UPDATE touches SET got_reply=?, reply_text=? WHERE id=?",
        (new_value, reply_text or None, touch_id),
    )

    if new_value == 1:
        company = conn.execute(
            "SELECT * FROM companies WHERE id = ?", (touch["company_id"],)
        ).fetchone()
        message = _handle_new_reply(conn, company, reply_text)
        if message:
            flash(message, "success")

    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("companies_list"))


@app.route("/companies/<int:company_id>/generate-response", methods=["POST"])
def generate_response(company_id):
    """Manual retry/trigger for a conversation-continuation draft, using the
    most recently logged reply on file for this company."""
    conn = get_db()
    company = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if company is None:
        conn.close()
        flash("Company not found.", "error")
        return redirect(url_for("companies_list"))

    existing = conn.execute(
        "SELECT id FROM touches WHERE company_id = ? AND status = 'pending_review'", (company_id,)
    ).fetchone()
    if existing:
        conn.close()
        flash("A draft is already pending review for this company.", "error")
        return redirect(url_for("company_detail", company_id=company_id))

    last_reply = conn.execute(
        """SELECT reply_text FROM touches WHERE company_id = ? AND reply_text IS NOT NULL
           ORDER BY id DESC LIMIT 1""",
        (company_id,),
    ).fetchone()
    reply_text = last_reply["reply_text"] if last_reply else ""

    variants = ai_drafting.generate_reply_options(dict(company), reply_text)
    default = variants[0]
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO touches (company_id, touch_number, kind, subject, body, variants, status, got_reply, created_at)
           VALUES (?, ?, 'conversation', ?, ?, ?, 'pending_review', 0, ?)""",
        (company_id, company["total_touches"] + 1, default["subject"], default["body"], json.dumps(variants), now),
    )
    conn.commit()
    conn.close()
    flash(f"{AGENT_NAME} drafted a response for {company['name']}.", "success")
    return redirect(request.referrer or url_for("review_queue"))


_inbox_check_lock = threading.Lock()


def check_inbox_for_replies():
    """Pulls any new real-inbox replies in over IMAP and logs them against
    the matching company, same as clicking 'Mark got a reply' by hand.
    Returns the number of replies processed. Safe to call with no IMAP
    config set -- email_reader just returns an empty list in that case.

    Guarded by a lock so the background poller and a manual 'Check inbox
    now' click can't ever run at the same time and open two IMAP sessions
    at once -- Gmail caps simultaneous connections per account and returns
    an [ALERT] error if that's exceeded."""
    if not _inbox_check_lock.acquire(blocking=False):
        return 0
    try:
        conn = get_db()
    except Exception:
        _inbox_check_lock.release()
        raise
    try:
        rows = conn.execute(
            "SELECT id, contact_email FROM companies WHERE contact_email IS NOT NULL AND contact_email != ''"
        ).fetchall()
        by_email = {}
        ambiguous_emails = set()
        for r in rows:
            key = r["contact_email"].strip().lower()
            if key in by_email:
                ambiguous_emails.add(key)
            by_email[key] = r["id"]
        if not by_email:
            return 0

        # Same contact email can legitimately belong to more than one
        # company record (e.g. testing with your own address). Email alone
        # can't disambiguate that, so also build a thread-based lookup:
        # which company a given one of OUR sent emails actually belongs to.
        # A reply's In-Reply-To/References pointing at one of our Message-
        # IDs identifies the right company even when the sender address is
        # shared.
        company_by_message_id = {
            t["email_message_id"]: t["company_id"]
            for t in conn.execute(
                "SELECT company_id, email_message_id FROM touches WHERE email_message_id IS NOT NULL"
            )
        }

        matches = email_reader.fetch_new_replies(set(by_email.keys()))
        count = 0
        for m in matches:
            already = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_id = ?", (m["message_id"],)
            ).fetchone()
            if already:
                continue

            company_id = None
            thread_ids = ([m["in_reply_to"]] if m.get("in_reply_to") else []) + list(m.get("references") or [])
            for mid in thread_ids:
                if mid in company_by_message_id:
                    company_id = company_by_message_id[mid]
                    break

            if company_id is None:
                if m["from_email"] in ambiguous_emails:
                    # Several companies share this contact email and nothing
                    # in the reply's headers says which thread it belongs
                    # to -- guessing risks attaching it to the wrong
                    # company (and generating a handoff for one that was
                    # never actually contacted), so skip it rather than guess.
                    continue
                company_id = by_email.get(m["from_email"])
            if not company_id:
                continue
            company = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
            if company is None:
                continue

            conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, company_id, processed_at) VALUES (?, ?, ?)",
                (m["message_id"], company_id, datetime.utcnow().isoformat()),
            )

            last_sent = conn.execute(
                "SELECT id FROM touches WHERE company_id = ? AND status = 'sent' ORDER BY id DESC LIMIT 1",
                (company_id,),
            ).fetchone()
            if last_sent:
                conn.execute(
                    "UPDATE touches SET got_reply=1, reply_text=?, reply_message_id=? WHERE id=?",
                    (m["body"], m["message_id"], last_sent["id"]),
                )
            _handle_new_reply(conn, company, m["body"])
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()
        _inbox_check_lock.release()


@app.route("/check-replies", methods=["POST"])
def check_replies_now():
    if not email_reader.is_configured():
        flash("Inbox checking isn't configured -- set SMTP_USERNAME/SMTP_PASSWORD to enable it.", "error")
        return redirect(request.referrer or url_for("dashboard"))
    try:
        count = check_inbox_for_replies()
    except Exception as exc:
        flash(f"Couldn't check the inbox -- {exc}", "error")
        return redirect(request.referrer or url_for("dashboard"))
    if count:
        flash(f"Found {count} new repl{'y' if count == 1 else 'ies'} and updated the dashboard.", "success")
    else:
        flash("Checked the inbox -- no new replies.", "success")
    return redirect(request.referrer or url_for("dashboard"))


def _poll_inbox_loop():
    while True:
        try:
            if email_reader.is_configured():
                check_inbox_for_replies()
        except Exception as exc:
            print(f"[inbox poll] error checking inbox: {exc}")
        time.sleep(INBOX_POLL_SECONDS)


def start_inbox_poller():
    """Runs check_inbox_for_replies() on a background thread every
    INBOX_POLL_SECONDS, so new replies show up on the dashboard without
    anyone clicking anything. Guarded so Werkzeug's debug-mode reloader
    (which re-execs this whole module in a separate watcher process) only
    starts one poller thread, not two -- `app.debug` isn't set to True until
    app.run(debug=True) actually runs, so the caller sets it beforehand."""
    if not email_reader.is_configured():
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    t = threading.Thread(target=_poll_inbox_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    init_db()
    app.debug = True
    start_inbox_poller()
    app.run(debug=True, port=5050, threaded=True)
