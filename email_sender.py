"""Actually sends email over SMTP -- real sending is opt-in and OFF unless
SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD are set in the environment. See
README for setup (Gmail app password, or any other SMTP provider).

This module never gets called automatically -- it's only invoked when a
draft is approved AND the "actually send" checkbox was checked in the
review queue. Nothing here runs on page load or on a schedule.
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import make_msgid


def is_configured() -> bool:
    return bool(
        os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USERNAME")
        and os.environ.get("SMTP_PASSWORD")
    )


def send_email(to_email: str, subject: str, body: str, in_reply_to: str = None, references: str = None) -> str:
    """Raises on any failure -- callers should catch and surface it rather
    than silently marking something as sent that wasn't. Returns the
    Message-ID this send generated, so the caller can store it and thread
    a later reply off of it.

    `in_reply_to` / `references`: pass the Message-ID of the email this one
    is replying to (handoff/conversation touches) so Gmail (and any other
    client) keeps it in the same conversation thread instead of starting a
    new one. Leave both unset for a true first touch -- there's nothing to
    thread onto yet."""
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("SMTP_FROM_EMAIL", username)
    from_name = os.environ.get("SMTP_FROM_NAME", "")

    if not (host and username and password):
        raise RuntimeError(
            "SMTP is not configured -- set SMTP_HOST, SMTP_USERNAME, and "
            "SMTP_PASSWORD (see README)."
        )
    if not to_email:
        raise RuntimeError("This company has no contact email on file.")

    message_id = make_msgid(domain=from_email.split("@")[-1] if "@" in from_email else None)

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls(context=context)
        server.login(username, password)
        server.sendmail(from_email, [to_email], msg.as_string())

    return message_id
