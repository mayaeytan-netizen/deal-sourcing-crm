"""Reads real replies out of the inbox over IMAP -- the read-side counterpart
to email_sender.py's SMTP send. Reuses the same SMTP_USERNAME/SMTP_PASSWORD
(a Gmail app password works for both sending and reading), so there's
nothing extra to configure beyond what real sending already needed.

Looks at recent messages (the last LOOKBACK_DAYS) and returns the ones from
a known company's contact email -- it's up to the caller (app.py, via the
`processed_messages` table) to dedupe against ones already handled. This
deliberately does NOT key off Gmail's read/unread flag: a human opening
their own inbox marks things read independently of whether the app has
processed them, so unread-only would silently miss anything you'd already
looked at yourself. Nothing here ever modifies the mailbox -- no flags are
changed, nothing is marked read.
"""

import email
import imaplib
import os
import re
import socket
import threading
from datetime import date, timedelta
from email.header import decode_header
from email.utils import parseaddr

IMAP_TIMEOUT_SECONDS = 15
WATCHDOG_SECONDS = 20
LOOKBACK_DAYS = 30


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_USERNAME") and os.environ.get("SMTP_PASSWORD"))


def _imap_host() -> str:
    return os.environ.get("IMAP_HOST", "imap.gmail.com")


def _imap_port() -> int:
    return int(os.environ.get("IMAP_PORT", "993"))


def _decode_header_value(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


# Boundaries that mark "everything from here on is the quoted previous
# email, not something the prospect actually wrote." Covers Outlook's quote
# header block, Gmail/Apple Mail's "On ... wrote:" line, and the classic
# Outlook "-----Original Message-----" separator. Case-insensitive,
# multiline, and tolerant of \r\n.
_QUOTE_BOUNDARY_PATTERNS = [
    re.compile(r"\n\s*From:.{0,200}?\n\s*(Sent|Date):.{0,200}?\n\s*To:", re.IGNORECASE | re.DOTALL),
    re.compile(r"\nOn .{0,200}? wrote:\s*\n", re.IGNORECASE),
    re.compile(r"\n-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"\n_{5,}\s*\n"),  # Outlook's "____" separator above a quoted/signature block
    re.compile(r"\nGet Outlook for (iOS|Android)", re.IGNORECASE),
]


def _strip_quoted_reply(text: str) -> str:
    """Cuts a reply body down to just the new text someone actually typed,
    dropping everything the email client appended below it (the quoted
    original message). Without this, a one-word reply like "Thanks" comes
    back as "Thanks" plus the entire prior email glued on, and that's what
    ends up feeding the next draft -- which is exactly the garbled, off-topic
    text this fixes."""
    if not text:
        return text
    earliest_cut = len(text)
    for pattern in _QUOTE_BOUNDARY_PATTERNS:
        match = pattern.search(text)
        if match and match.start() < earliest_cut:
            earliest_cut = match.start()
    # Also drop a trailing run of "> " quoted lines, if that's all that's left.
    cut_text = text[:earliest_cut]
    lines = cut_text.splitlines()
    while lines and lines[-1].strip().startswith(">"):
        lines.pop()
    return "\n".join(lines).strip()


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                return _strip_quoted_reply(payload.decode(charset, errors="replace").strip())
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/html" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                return _strip_quoted_reply(re.sub(r"<[^<]+?>", " ", html).strip())
        return ""

    payload = msg.get_payload(decode=True)
    if payload is None:
        return _strip_quoted_reply((msg.get_payload() or "").strip())
    charset = msg.get_content_charset() or "utf-8"
    return _strip_quoted_reply(payload.decode(charset, errors="replace").strip())


def fetch_new_replies(known_emails) -> list:
    """`known_emails`: a set of lowercase contact email addresses to match
    against. Returns a list of {"from_email", "subject", "body", "message_id"}
    for inbox messages from the last LOOKBACK_DAYS sent by any of those
    addresses. Callers dedupe by message_id themselves.

    Runs the actual IMAP work on a watchdog thread: some networks/proxies
    let the TCP connect through instantly but then stall the TLS/IMAP
    handshake without ever erroring or timing out at the socket level, which
    would otherwise hang this (and anything that calls it, like the
    dashboard's background poller) forever. If the watchdog doesn't finish
    within WATCHDOG_SECONDS, this raises TimeoutError instead of hanging.
    """
    if not is_configured():
        return []

    result_holder = {}

    def _run():
        try:
            result_holder["value"] = _fetch_new_replies_impl(known_emails)
        except Exception as exc:  # noqa: BLE001 -- surfaced via result_holder
            result_holder["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=WATCHDOG_SECONDS)

    if thread.is_alive():
        raise TimeoutError(
            f"IMAP check didn't finish within {WATCHDOG_SECONDS}s -- the network connection to "
            f"{_imap_host()}:{_imap_port()} stalled. This machine/network may be blocking or "
            "proxying that connection."
        )
    if "error" in result_holder:
        raise result_holder["error"]
    return result_holder.get("value", [])


def _fetch_new_replies_impl(known_emails) -> list:
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    known_emails = {e.strip().lower() for e in known_emails if e}
    if not known_emails:
        return []

    results = []
    # Belt-and-suspenders: the per-call `timeout=` kwarg on IMAP4_SSL isn't
    # reliably honored for every blocking call imaplib makes after connect
    # (login/search/fetch can still hang). socket.setdefaulttimeout() is
    # applied to every socket the stdlib creates from here on, including
    # imaplib's internal ones, so it actually bounds the whole operation.
    previous_default_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(IMAP_TIMEOUT_SECONDS)
    try:
        conn = imaplib.IMAP4_SSL(_imap_host(), _imap_port(), timeout=IMAP_TIMEOUT_SECONDS)
    except Exception:
        socket.setdefaulttimeout(previous_default_timeout)
        raise

    try:
        conn.login(username, password)
        conn.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")

        # Ask the server to do the filtering (FROM + SINCE combined in one
        # SEARCH per known contact) instead of pulling back and inspecting
        # every recent message ourselves -- a mailbox can easily have
        # hundreds of messages in the lookback window, and fetching the full
        # RFC822 body of each just to check its sender is what was actually
        # causing this to blow past the timeout, not a network problem.
        seen_ids = set()
        for contact_email in known_emails:
            status, data = conn.search(None, "FROM", f'"{contact_email}"', "SINCE", since)
            if status != "OK" or not data or not data[0]:
                continue
            for msg_id in data[0].split():
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                _, from_addr = parseaddr(msg.get("From", ""))
                from_addr = (from_addr or "").strip().lower()
                if from_addr not in known_emails:
                    continue

                message_id = (msg.get("Message-ID") or f"{msg_id.decode()}@{_imap_host()}").strip()
                # In-Reply-To/References let the caller match this reply to
                # the exact company/thread it belongs to, not just "some
                # company with this contact email" -- important when the
                # same person is the contact on more than one company record.
                in_reply_to = (msg.get("In-Reply-To") or "").strip() or None
                references_header = (msg.get("References") or "").strip()
                references = references_header.split() if references_header else []
                results.append(
                    {
                        "from_email": from_addr,
                        "subject": _decode_header_value(msg.get("Subject", "")),
                        "body": _extract_body(msg),
                        "message_id": message_id,
                        "in_reply_to": in_reply_to,
                        "references": references,
                    }
                )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
        socket.setdefaulttimeout(previous_default_timeout)

    return results
