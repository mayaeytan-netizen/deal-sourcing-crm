# BDR Dana -- Deal Sourcing CRM

A small CRM for tracking outreach to a list of target companies, with an AI
agent that drafts personalized follow-ups on a cadence. Built around a
draft-and-approve workflow: the agent writes, you decide what goes out.
Nothing is ever sent automatically. By default, "Approve & mark sent" just
logs the touch and advances the cadence -- no email leaves the machine. Real
sending is available but strictly opt-in (see "Connecting real email
sending" below): it's off unless you configure SMTP credentials yourself,
and even then it only fires when you explicitly check "Actually send this"
on a specific draft, on top of approving it.

## Stack

Flask + SQLite (stdlib `sqlite3`, zero external DB setup) + server-rendered
Jinja templates + Chart.js (via CDN) for the dashboard. No Node/npm required.

## Run it

```bash
cd "<this folder>"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Then open **http://127.0.0.1:5050**. The database (`crm.db`) is created and
seeded with 14 fictional property-management/real-estate companies the first
time it's missing, so the app is demo-ready immediately. Delete `crm.db` and
restart to get a fresh seed.

## How the pieces fit together

- `schema.sql` -- two tables: `companies` and `touches`.
- `cadence.py` -- the follow-up rules. No answer: follow up again a week
  later, then a week after that, and so on (`CADENCE_INTERVAL_DAYS = 7`).
  Explicit "not interested" (stage set to `Declined`): go quiet, then check
  back in 3 months in case anything's changed (`CHECKIN_INTERVAL_DAYS = 90`),
  repeating every 3 months until the company replies or the stage changes.
  `due_status()` buckets a company into overdue / due today / due this week
  / scheduled / none. `Responded`, `Interested`, and `Closed` stop getting
  new cadence dates entirely -- once a real conversation starts or the deal
  is done, the agent gets out of the way.
- `agent_identity.py` -- the agent's persona: **Dana**, writing on behalf of
  **Daisy**, who hands off to **Maya** (you) once a company shows interest.
  Change these three names in one place to re-brand.
- `ai_drafting.py` -- the drafting agent. Every draft comes back as 3 options
  in a different tone/angle (so two companies at the same touch number don't
  get the same email reworded), written in plain, natural prose on purpose:
  no em dashes, no AI-sounding phrasing, nothing that gives away it was
  generated. Given a company + touch number, it returns draft options signed
  by Dana, introducing Daisy and its interest in expanding/collaborating on
  deal sourcing. Touch 1 reads as an intro, touch 2 as a first follow-up,
  touch 3+ as a more direct (but still warm) follow-up. It only uses what's
  in the company's `notes` field -- it's instructed not to invent specifics.
  The moment you mark a touch as "got a reply" (company_detail page), Dana
  automatically drafts a one-off **handoff** email -- thanking them and
  introducing Maya from Daisy as the point of contact for setting up a
  meeting -- and drops it in the review queue tagged "Handoff". If instead a
  company is marked **Declined** ("not interested"), the cadence doesn't
  stop: 3 months later Dana drafts a low-pressure **check-in** ("has
  anything changed on your end?"), tagged "Check-in" in the queue. Same
  draft-and-approve rules apply everywhere: nothing sends until you approve
  it.
- `app.py` -- routes for companies CRUD, the review queue, and the dashboard.
  Approving a draft is the only thing that logs a touch, bumps
  `total_touches`, sets `last_touch_date`/`next_touch_date`, and
  auto-advances the pipeline stage (Not Contacted → Contacted → Follow-up 1
  → Follow-up 2).
- `seed_data.py` / `db.py` -- the demo seed data and the one-time seeding
  logic that reconstructs a plausible touch history for it.

## Connecting a real API key

Right now there's no `ANTHROPIC_API_KEY` in this environment, so
`ai_drafting.py` falls back to a clearly-labeled placeholder generator
(every stub draft is tagged `[STUB DRAFT ...]` in the body and shows a
"Placeholder" badge in the review queue). To wire up real Claude-generated
drafts:

1. Get an API key from the [Anthropic Console](https://console.anthropic.com/).
2. Set it as an environment variable before starting the app:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   python3 app.py
   ```
3. That's it -- `ai_drafting.py` already checks for `ANTHROPIC_API_KEY` and
   calls the Claude API (`claude-sonnet-5` by default; override with
   `CLAUDE_MODEL`) instead of the stub the moment the key is present. No code
   changes needed.

If the API call ever fails (bad key, network issue, etc.), the app falls
back to the placeholder draft rather than breaking the review queue, and
labels the draft with the error.

## Connecting real email sending

Off by default. `email_sender.py` only sends when `SMTP_HOST`,
`SMTP_USERNAME`, and `SMTP_PASSWORD` are all set in the environment --
without them, approving a draft just logs it, same as before.

**Using Gmail** (easiest if you don't have another provider in mind):

1. Turn on 2-Step Verification on the Google account you want to send from:
   https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
   (pick "Mail" as the app). Google gives you a 16-character password --
   that's what goes below, *not* your normal Gmail password.
3. Set these and restart the app:
   ```bash
   export SMTP_HOST=smtp.gmail.com
   export SMTP_PORT=587
   export SMTP_USERNAME=youraddress@gmail.com
   export SMTP_PASSWORD=<the 16-character app password>
   export SMTP_FROM_NAME="Dana @ Daisy"
   python3 app.py
   ```

**Using another provider** (Outlook, a custom domain, SendGrid/Mailgun/Resend's
SMTP relay, etc.) -- same four variables, just point `SMTP_HOST`/`SMTP_PORT`
at that provider's SMTP relay and use its credentials. No code changes
needed either way.

Once configured, the review queue shows a "Live sending is on" banner, and
each draft gets an **"Actually send this to `<email>`"** checkbox -- it's
unchecked by default, so approving still just logs the touch unless you
explicitly tick it. Checking it and clicking approve pops one more
JS confirmation before it actually calls SMTP.

**Careful with the seed data**: the 14 demo companies have made-up email
addresses. Sending to them will bounce or silently go nowhere -- don't check
"Actually send" on a seeded company. Either add a company with your own
email (or a friend's, with their OK) to test with, or edit an existing
company's contact email on its detail page first.

## Automatically picking up real replies

Also off unless configured, and it reuses the exact same `SMTP_USERNAME` /
`SMTP_PASSWORD` from real sending above -- a Gmail app password works for
reading mail over IMAP too, nothing extra to set up if you already did the
sending setup.

What it does: once configured, `email_reader.py` connects over IMAP and
looks for new (unread) messages from any email address that matches a
company's contact email on file. A background thread checks automatically
every `INBOX_POLL_SECONDS` (45s by default) while the app is running, and
there's also a **"Check inbox now"** button on the dashboard for an
immediate check. When it finds a match, it logs the reply exactly like
clicking "Mark got a reply" by hand -- the company shows up in the
dashboard's "Needs a response" section, and Dana drafts the next message
(the first-ever handoff, or a conversation continuation after that)
straight into the review queue. It only ever touches messages that match a
known contact email; everything else in the inbox is left alone, not even
marked as read.

Two things worth knowing:
- **Gmail's IMAP access has to be turned on**: Gmail Settings -> See all
  settings -> "Forwarding and POP/IMAP" tab -> enable IMAP. If it's off,
  the check will fail with a login/command error rather than finding mail.
- It's bounded to fail within ~20 seconds rather than ever hanging the app,
  even on a flaky or restricted network -- if a check fails, the dashboard
  button will say so, and the background poller just quietly retries on the
  next cycle instead of crashing.

To point it at a non-Gmail IMAP server, set `IMAP_HOST` (default
`imap.gmail.com`) and `IMAP_PORT` (default `993`).

## What's intentionally not built

- No OAuth-based inbox connection (no "sign in with Google" flow) -- the
  inbox reader uses the same app-password/IMAP approach as real sending,
  which is enough for one person's own inbox but wouldn't scale past that.
- No auth/multi-user support -- this is a single-player tool for one
  person's pipeline.
