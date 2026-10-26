"""Cadence math: when a company's next touch is due, and how urgent that is."""

from datetime import date, datetime, timedelta

STAGES = [
    "Not Contacted",
    "Contacted",
    "Follow-up 1",
    "Follow-up 2",
    "Responded",
    "Interested",
    "Declined",
    "Closed",
]

# Stages past which we stop scheduling new automated touches entirely --
# either a human conversation has already started (Responded, Interested)
# or the deal is done (Closed). Declined is NOT here: a "not interested"
# answer still gets a long-interval check-in cadence, see CHECKIN_INTERVAL_DAYS.
INACTIVE_STAGES = {"Responded", "Interested", "Closed"}

# No answer: follow up weekly.
CADENCE_INTERVAL_DAYS = 7

# Explicit "not interested": go quiet, then check back in case anything
# changed, on a 3-month rhythm, rather than giving up entirely.
CHECKIN_INTERVAL_DAYS = 90


def parse_date(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def fmt_date(d):
    if d is None:
        return None
    return d.strftime("%Y-%m-%d")


def next_touch_after(last_touch_date, interval_days):
    """The next due date, `interval_days` after `last_touch_date` (or today,
    if there's no last touch yet)."""
    base = parse_date(last_touch_date) or date.today()
    return base + timedelta(days=interval_days)


def due_status(next_touch_date, today=None):
    """Returns one of: 'overdue', 'due_today', 'due_this_week', 'scheduled', 'none'."""
    today = today or date.today()
    d = parse_date(next_touch_date)
    if d is None:
        return "none"
    if d < today:
        return "overdue"
    if d == today:
        return "due_today"
    if d <= today + timedelta(days=7):
        return "due_this_week"
    return "scheduled"


def is_actionable(status):
    return status in ("overdue", "due_today", "due_this_week")
