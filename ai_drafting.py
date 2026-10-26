"""The drafting agent: turns a company + touch number into a personalized
outreach draft, written in the voice of Dana, Daisy's outreach agent. Uses
the Claude API when ANTHROPIC_API_KEY is set; otherwise falls back to a
clearly-labeled stub so the app still works end to end.

Every draft comes back as a short list of style OPTIONS rather than one
fixed template; the review queue lets you pick (and still edit) whichever
one reads best, so two companies at the same touch number don't get
word-for-word identical emails. Draft copy is written to read like a real
person wrote it: no em dashes, no double hyphens, no AI-generated tells.
Whether a draft is a placeholder or model-generated is tracked separately
as metadata (`is_stub`) and shown only as a UI badge, never baked into the
email text itself.

Three kinds of drafts:
  - "cadence" drafts: the regular weekly intro/follow-up sequence while a
    company hasn't answered.
  - "handoff" drafts: a one-off message Dana sends the moment a company
    shows interest, introducing Maya (the human) to take it from there.
  - "checkin" drafts: a low-pressure note sent 3 months after a company
    says it's not interested, in case anything's changed.
"""

import json
import os
import re

from agent_identity import AGENT_NAME, COMPANY_NAME, COMPANY_ABOUT, HUMAN_NAME

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

NO_DASH_RULE = (
    "Never use em dashes or double hyphens anywhere in the subject or body, and "
    "never write anything that signals the email was AI-generated, automated, or "
    "templated. Write the way a real person emails: short sentences, natural "
    "punctuation (commas, periods, question marks, parentheses), occasional "
    "sentence fragments. It should read like it was typed by a person in a few "
    "minutes, not composed."
)

TOUCH_GUIDANCE = {
    1: f"This is the FIRST outreach this company has ever received from {AGENT_NAME}. "
       f"Introduce yourself as {AGENT_NAME} from {COMPANY_NAME}, briefly explain that "
       f"{COMPANY_NAME} is looking to expand and collaborate with property management "
       "/ real estate companies like this one on deal sourcing, and make a low-friction "
       "ask (e.g. a short call). Warm, concise, no pressure.",
    2: "This is the FIRST FOLLOW-UP after an unanswered first touch. Reference that "
       f"{AGENT_NAME} reached out before without being pushy, add one new piece of "
       f"value or context about {COMPANY_NAME}'s interest in collaborating, and keep "
       "the ask just as easy to say yes to.",
    3: "This is the SECOND FOLLOW-UP after two unanswered touches. Be more direct and "
       "a little more concise, out of respect for their inbox. Offer an easy out "
       "(e.g. 'if now isn't the right time, happy to check back later') while leaving "
       "the door open.",
}

DEFAULT_GUIDANCE = (
    "This is a later follow-up (touch {n}) after several unanswered attempts. Keep it "
    "short, low-pressure, and give them an easy way to opt out of future outreach, "
    "while still leaving the door open."
)

N_VARIANTS = 3


def _guidance(touch_number: int) -> str:
    return TOUCH_GUIDANCE.get(touch_number, DEFAULT_GUIDANCE.format(n=touch_number))


def generate_draft_options(company: dict, touch_number: int) -> list:
    """Returns a list of N_VARIANTS {"label", "subject", "body", "is_stub"} dicts,
    distinct in tone/angle, for the given company + touch number."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _generate_options_with_claude(company, touch_number, api_key)
        except Exception as exc:
            variants = _stub_cadence_variants(company, touch_number)
            for v in variants:
                v["generation_note"] = f"Claude API call failed ({exc}); showing a placeholder instead."
            return variants
    return _stub_cadence_variants(company, touch_number)


def generate_handoff_options(company: dict) -> list:
    """Returns a list of N_VARIANTS {"label", "subject", "body", "is_stub"} dicts:
    Dana connecting the company to Maya now that they've shown interest."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _generate_handoff_options_with_claude(company, api_key)
        except Exception as exc:
            variants = _stub_handoff_variants(company)
            for v in variants:
                v["generation_note"] = f"Claude API call failed ({exc}); showing a placeholder instead."
            return variants
    return _stub_handoff_variants(company)


def generate_reply_options(company: dict, reply_text: str) -> list:
    """Returns a list of N_VARIANTS {"label", "subject", "body", "is_stub"} dicts:
    Dana continuing an active conversation, responding to what the prospect
    actually said (`reply_text`), rather than another cold-cadence touch."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _generate_reply_options_with_claude(company, reply_text, api_key)
        except Exception as exc:
            variants = _stub_reply_variants(company, reply_text)
            for v in variants:
                v["generation_note"] = f"Claude API call failed ({exc}); showing a placeholder instead."
            return variants
    return _stub_reply_variants(company, reply_text)


def generate_checkin_options(company: dict) -> list:
    """Returns a list of N_VARIANTS {"label", "subject", "body", "is_stub"} dicts:
    a low-pressure check-in Dana sends a few months after a company said no,
    in case anything's changed on their end."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _generate_checkin_options_with_claude(company, api_key)
        except Exception as exc:
            variants = _stub_checkin_variants(company)
            for v in variants:
                v["generation_note"] = f"Claude API call failed ({exc}); showing a placeholder instead."
            return variants
    return _stub_checkin_variants(company)


def _claude_client(api_key):
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _parse_variant_array(text: str, expected: int) -> list:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON array found in model response")
    data = json.loads(match.group(0))
    if not isinstance(data, list) or not data:
        raise ValueError("model response was not a non-empty JSON array")
    return data[:expected] if len(data) > expected else data


def _generate_options_with_claude(company: dict, touch_number: int, api_key: str) -> list:
    client = _claude_client(api_key)
    contact = company.get("contact_name") or "there"
    notes = company.get("notes") or "(no notes on file)"

    system_prompt = (
        f"You are {AGENT_NAME}, an outreach agent working on behalf of {COMPANY_NAME}. "
        f"Here is what {COMPANY_NAME} actually does, for your own grounding -- "
        f"{COMPANY_ABOUT} Don't dump this whole description into a cold email; pull in "
        "a specific detail only where it's useful, not as a wall of facts. You draft short, "
        f"specific, genuinely interesting cold outreach emails, signed as {AGENT_NAME} "
        f"from {COMPANY_NAME}. Avoid generic cold-email filler; write like a sharp, "
        "likeable person, not a template. You NEVER invent facts, numbers, deal specifics, or "
        "personal details that are not present in the notes you're given or in the "
        f"{COMPANY_NAME} description above. If the notes are thin, write a more general "
        f"but still genuine email rather than fabricating specifics. You produce "
        f"{N_VARIANTS} distinctly different DRAFT "
        "OPTIONS for the same touch: different opening hooks, different structure, "
        "different tone (e.g. one direct, one more conversational/curious, one that "
        "leads with value), so they don't read like the same email reworded. "
        f"{NO_DASH_RULE} Respond with strict JSON: a JSON array of {N_VARIANTS} "
        'objects, each {"label": "two or three words describing the angle", '
        '"subject": "...", "body": "..."}. The body should be plain text, ready to '
        "paste into an email client."
    )

    user_prompt = f"""Company: {company.get('name')}
Contact: {contact}
Website/notes on file: {notes}
Pipeline stage: {company.get('stage')}
Touch number: {touch_number} (touches sent so far: {company.get('total_touches', 0)})

Guidance for this touch: {_guidance(touch_number)}

Write {N_VARIANTS} distinct draft options now as a JSON array."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1400,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    data = _parse_variant_array(text, N_VARIANTS)
    return [
        {
            "label": d.get("label", f"Option {i + 1}"),
            "subject": d["subject"].strip(),
            "body": d["body"].strip(),
            "is_stub": False,
        }
        for i, d in enumerate(data)
    ]


def _generate_handoff_options_with_claude(company: dict, api_key: str) -> list:
    client = _claude_client(api_key)
    contact = company.get("contact_name") or "there"
    notes = company.get("notes") or "(no notes on file)"

    system_prompt = (
        f"You are {AGENT_NAME}, an outreach agent working on behalf of {COMPANY_NAME}. "
        f"Here is what {COMPANY_NAME} actually does, for your own grounding -- "
        f"{COMPANY_ABOUT} "
        f"A company you've been reaching out to has just shown interest. Write "
        f"{N_VARIANTS} distinctly different short, warm reply options, still signed "
        f"as {AGENT_NAME}, that thank them for getting back and explicitly hand the "
        f"conversation off to {HUMAN_NAME} from {COMPANY_NAME}, who will reach out "
        f"directly to find time for a meeting. Make clear {HUMAN_NAME} is the point "
        "of contact going forward. Vary the opening and tone across the options (e.g. "
        "one enthusiastic, one efficient/professional, one more personal). Don't just "
        f"reword the same email. Do not invent facts beyond what's in the notes. "
        f"{NO_DASH_RULE} Respond with strict JSON: a JSON array of {N_VARIANTS} "
        'objects, each {"label": "two or three words describing the tone", '
        '"subject": "...", "body": "..."}.'
    )

    user_prompt = f"""Company: {company.get('name')}
Contact: {contact}
Notes on file: {notes}

Write {N_VARIANTS} distinct handoff options now as a JSON array."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    data = _parse_variant_array(text, N_VARIANTS)
    return [
        {
            "label": d.get("label", f"Option {i + 1}"),
            "subject": d["subject"].strip(),
            "body": d["body"].strip(),
            "is_stub": False,
        }
        for i, d in enumerate(data)
    ]


def _generate_checkin_options_with_claude(company: dict, api_key: str) -> list:
    client = _claude_client(api_key)
    contact = company.get("contact_name") or "there"
    notes = company.get("notes") or "(no notes on file)"

    system_prompt = (
        f"You are {AGENT_NAME}, an outreach agent working on behalf of {COMPANY_NAME}. "
        f"Here is what {COMPANY_NAME} actually does, for your own grounding -- "
        f"{COMPANY_ABOUT} "
        "This company was reached out to before and said it wasn't the right time or "
        "they weren't interested. It has now been a few months. Write "
        f"{N_VARIANTS} distinctly different short, low-pressure check-in options, "
        f"still signed as {AGENT_NAME}, that reconnect without being pushy about the "
        "earlier no. The angle is simply: it's been a while, wanted to check back in "
        "case anything's changed on their end (team, priorities, timing), no hard "
        "feelings either way. Do not reference specifics of the earlier rejection "
        "beyond acknowledging it's been some time since you last talked. Vary the "
        "opening and tone across the options. Do not invent facts beyond what's in "
        f"the notes. {NO_DASH_RULE} Respond with strict JSON: a JSON array of "
        f'{N_VARIANTS} objects, each {{"label": "two or three words describing the '
        'angle", "subject": "...", "body": "..."}}.'
    )

    user_prompt = f"""Company: {company.get('name')}
Contact: {contact}
Notes on file: {notes}

Write {N_VARIANTS} distinct check-in options now as a JSON array."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    data = _parse_variant_array(text, N_VARIANTS)
    return [
        {
            "label": d.get("label", f"Option {i + 1}"),
            "subject": d["subject"].strip(),
            "body": d["body"].strip(),
            "is_stub": False,
        }
        for i, d in enumerate(data)
    ]


def _generate_reply_options_with_claude(company: dict, reply_text: str, api_key: str) -> list:
    client = _claude_client(api_key)
    contact = company.get("contact_name") or "there"
    notes = company.get("notes") or "(no notes on file)"

    system_prompt = (
        f"You are {AGENT_NAME}, an outreach agent working on behalf of {COMPANY_NAME}, "
        "in an active email conversation with a prospect who just replied. Here is what "
        f"{COMPANY_NAME} actually does, for your own grounding -- {COMPANY_ABOUT} If "
        f"they're asking what {COMPANY_NAME} does, who it serves, or for more detail "
        "about the product, answer directly and accurately using the description above "
        "(pick the 1-3 most relevant facts, don't paste the whole thing) -- don't "
        "deflect the question or stay vague. You keep "
        f"the conversation moving on {COMPANY_NAME}'s behalf: answer what they asked, "
        "react to what they said, and move things toward a concrete next step (a "
        f"call, a time, a meeting) -- introducing {HUMAN_NAME} from {COMPANY_NAME} as "
        "the point of contact for actually scheduling that, if a meeting is the "
        f"natural next step and {HUMAN_NAME} hasn't already been introduced in this "
        f"thread. Write {N_VARIANTS} distinctly different reply options, varying tone "
        "and approach. Respond directly to the substance of what they wrote; don't "
        "ignore it or revert to generic cold-outreach language. Do not invent facts, "
        f"numbers, or commitments not present in the notes or the {COMPANY_NAME} "
        f"description above. {NO_DASH_RULE} Respond "
        f'with strict JSON: a JSON array of {N_VARIANTS} objects, each {{"label": '
        '"two or three words describing the angle", "subject": "...", "body": "..."}}.'
    )

    user_prompt = f"""Company: {company.get('name')}
Contact: {contact}
Notes on file: {notes}

What the prospect just said:
\"\"\"{reply_text}\"\"\"

Write {N_VARIANTS} distinct reply options now as a JSON array."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    data = _parse_variant_array(text, N_VARIANTS)
    return [
        {
            "label": d.get("label", f"Option {i + 1}"),
            "subject": d["subject"].strip(),
            "body": d["body"].strip(),
            "is_stub": False,
        }
        for i, d in enumerate(data)
    ]


# ---------------------------------------------------------------------------
# Stub generation (no API key configured). Deterministic, so the same company
# and touch always previews the same three options, but each of the three
# reads genuinely differently: a different hook, a different structure, a
# different close. All personalize off `notes` where available. Written in
# plain, natural prose on purpose -- no em dashes, no AI-sounding phrasing.
# `is_stub: True` marks these for the UI badge; that label never appears in
# the actual email text.
# ---------------------------------------------------------------------------

def _hook(company: dict) -> str:
    notes = (company.get("notes") or "").strip().rstrip(".")
    name = company.get("name", "your company")
    if notes:
        return f"{notes}"
    return f"what {name} is building"


def _stub_cadence_variants(company: dict, touch_number: int) -> list:
    if touch_number == 1:
        return _stub_touch1(company)
    if touch_number == 2:
        return _stub_touch2(company)
    return _stub_touch3plus(company, touch_number)


def _stub_touch1(company: dict) -> list:
    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    hook = _hook(company)
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"

    direct = {
        "label": "Direct & warm",
        "subject": f"Quick intro: {COMPANY_NAME} x {name}",
        "body": (
            f"{greeting},\n\nI'm {AGENT_NAME}. I work with {COMPANY_NAME}, and we're "
            f"looking to expand and collaborate with property management and real "
            f"estate teams on deal sourcing. {name} came up as a strong fit.\n\n"
            f"Would you be up for a quick 15 minute call sometime in the next couple "
            f"weeks? Happy to work around your schedule." + sig
        ),
        "is_stub": True,
    }

    curious = {
        "label": "Curious opener",
        "subject": f"Loved seeing what {name} is doing",
        "body": (
            f"{greeting},\n\nI came across {hook}, and it stuck with me. "
            f"I'm {AGENT_NAME} with {COMPANY_NAME}, and we're actively looking to "
            f"partner with teams doing exactly this kind of work on deal sourcing.\n\n"
            f"Curious whether there's room to compare notes. Open to a short call?"
            + sig
        ),
        "is_stub": True,
    }

    value_first = {
        "label": "Value-forward",
        "subject": f"A deal sourcing idea for {name}",
        "body": (
            f"{greeting},\n\n{COMPANY_NAME} works with property management and real "
            f"estate teams to source off-market deals worth a second look, and "
            f"given {hook}, I think there's a real fit with {name}.\n\n"
            f"I'm {AGENT_NAME}. Happy to walk through a couple of examples on a quick "
            f"call if that's useful, no pressure either way." + sig
        ),
        "is_stub": True,
    }

    return [direct, curious, value_first]


def _stub_touch2(company: dict) -> list:
    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    hook = _hook(company)
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"

    light = {
        "label": "Light touch",
        "subject": f"Re: Quick intro: {COMPANY_NAME} x {name}",
        "body": (
            f"{greeting},\n\nJust floating this back to the top of your inbox in "
            f"case it got buried last week. Still think {name} and {COMPANY_NAME} "
            f"could be a good fit on deal sourcing.\n\nNo worries if the timing's off, "
            f"just let me know." + sig
        ),
        "is_stub": True,
    }

    new_angle = {
        "label": "New angle",
        "subject": f"One more reason {name} + {COMPANY_NAME} could work",
        "body": (
            f"{greeting},\n\nFollowing up on my note from last week. One thing I "
            f"didn't mention: given {hook}, we've seen this kind of partnership "
            f"work especially well for teams in a similar spot.\n\nHappy to share a "
            f"couple of concrete examples if that'd help you decide whether it's "
            f"worth a conversation." + sig
        ),
        "is_stub": True,
    }

    social_proof = {
        "label": "Social proof",
        "subject": f"Still thinking about {name}",
        "body": (
            f"{greeting},\n\nWe've been having good early conversations with a few "
            f"other property management teams about deal sourcing partnerships, and "
            f"{name} was one of the first that came to mind.\n\nWould a 15 minute call "
            f"this week or next work on your end?" + sig
        ),
        "is_stub": True,
    }

    return [light, new_angle, social_proof]


def _stub_touch3plus(company: dict, touch_number: int) -> list:
    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"

    direct_ask = {
        "label": "Direct ask",
        "subject": f"Worth a quick call, {name}?",
        "body": (
            f"{greeting},\n\nI'll keep this one short. Still believe there's a "
            f"good reason for {name} and {COMPANY_NAME} to connect on deal sourcing. "
            f"If you're open to it, a 15 minute call is all I'm after.\n\nLet me know "
            f"either way?" + sig
        ),
        "is_stub": True,
    }

    warm_close = {
        "label": "Warm close",
        "subject": f"Still here when it's useful, {name}",
        "body": (
            f"{greeting},\n\nI know inboxes get full, so no hard feelings if this "
            f"isn't the right moment. Just wanted {name} to know the door's open "
            f"whenever it makes sense to talk deal sourcing with {COMPANY_NAME}."
            + sig
        ),
        "is_stub": True,
    }

    low_pressure = {
        "label": "Low-pressure check-in",
        "subject": f"Should I check back later, {name}?",
        "body": (
            f"{greeting},\n\nHaven't heard back, which is totally fine. Just "
            f"checking whether now's simply not the right time, or if this isn't a "
            f"fit at all. Either answer's helpful, and if it's the former I'm happy "
            f"to circle back in a few months instead." + sig
        ),
        "is_stub": True,
    }

    return [direct_ask, warm_close, low_pressure]


def _stub_handoff_variants(company: dict) -> list:
    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"

    enthusiastic = {
        "label": "Enthusiastic",
        "subject": f"Great to connect, looping in {HUMAN_NAME} from {COMPANY_NAME}",
        "body": (
            f"{greeting},\n\nThis made my day. Really glad to hear back! I'm "
            f"looping in {HUMAN_NAME} from {COMPANY_NAME}, who leads these "
            f"partnerships directly and will reach out shortly to grab time on your "
            f"calendar.\n\nExcited about where this could go for {name} and "
            f"{COMPANY_NAME}." + sig
        ),
        "is_stub": True,
    }

    efficient = {
        "label": "Efficient",
        "subject": f"Next step: introducing {HUMAN_NAME}",
        "body": (
            f"{greeting},\n\nThanks for the reply. {HUMAN_NAME} from {COMPANY_NAME} "
            f"owns these partnerships and will be in touch shortly to find a time "
            f"that works for a call.\n\nLooking forward to it." + sig
        ),
        "is_stub": True,
    }

    personal = {
        "label": "Personal",
        "subject": f"So glad this landed, {contact or 'thanks for writing back'}",
        "body": (
            f"{greeting},\n\nThank you for taking the time to write back. I know "
            f"inboxes are a lot, so I appreciate it. From here, {HUMAN_NAME} from "
            f"{COMPANY_NAME} will take the lead and reach out to find a time that "
            f"works for a real conversation.\n\nReally looking forward to {name} and "
            f"{COMPANY_NAME} working together." + sig
        ),
        "is_stub": True,
    }

    return [enthusiastic, efficient, personal]


def _stub_checkin_variants(company: dict) -> list:
    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"

    casual = {
        "label": "Casual check-in",
        "subject": f"Been a while, {name}",
        "body": (
            f"{greeting},\n\nIt's been a few months since we last talked, so I "
            f"wanted to check back in. Things change fast, priorities shift, so "
            f"figured it was worth asking: has anything changed on your end that "
            f"might make deal sourcing with {COMPANY_NAME} worth another look?\n\n"
            f"No worries at all if not, just wanted to leave the door open."
            + sig
        ),
        "is_stub": True,
    }

    direct = {
        "label": "Direct reconnect",
        "subject": f"Checking back in, {name}",
        "body": (
            f"{greeting},\n\nI know we talked a while back and the timing wasn't "
            f"right. Wanted to reconnect in case anything's shifted since then, "
            f"team, priorities, or just bandwidth. If it's still not the right fit, "
            f"totally understood, and I'll leave you be.\n\nWorth a quick chat?"
            + sig
        ),
        "is_stub": True,
    }

    warm = {
        "label": "Warm and low-key",
        "subject": f"Thinking of {name} again",
        "body": (
            f"{greeting},\n\nNo big pitch here, just wanted to say it's been a "
            f"minute since we last connected and {name} crossed my mind again. If "
            f"the timing's better now, I'd love to pick the conversation back up. "
            f"If not, I'll check in again down the road.\n\nHope things have been "
            f"going well." + sig
        ),
        "is_stub": True,
    }

    return [casual, direct, warm]


def _snippet(text: str, max_len: int = 140) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut + "..."


_ABOUT_QUESTION_PATTERNS = [
    re.compile(r"\bdaisy\b", re.IGNORECASE),
    re.compile(r"tell me more", re.IGNORECASE),
    re.compile(r"what (do|does|is) (you|your company|daisy)", re.IGNORECASE),
    re.compile(r"more (details?|info|information)", re.IGNORECASE),
    re.compile(r"\bwho are you\b", re.IGNORECASE),
    re.compile(r"\bwhat'?s? daisy\b", re.IGNORECASE),
]


def _is_asking_about_company(reply_text: str) -> bool:
    text = reply_text or ""
    return any(p.search(text) for p in _ABOUT_QUESTION_PATTERNS)


def is_asking_about_company(reply_text: str) -> bool:
    """Public check so callers (app.py) can route a reply to the accurate
    "about Daisy" response BEFORE the generic handoff, even on the very
    first reply -- answering a real question takes priority over
    introducing Maya."""
    return _is_asking_about_company(reply_text)


def _stub_about_company_variants(company: dict) -> list:
    """Real, grounded answers to "what does Daisy do?" -- different angles,
    but every fact in here is pulled from COMPANY_ABOUT, never invented."""
    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"

    product_overview = {
        "label": "Product overview",
        "subject": f"More on {COMPANY_NAME}",
        "body": (
            f"{greeting},\n\nHappy to explain. {COMPANY_ABOUT.split('. ')[0]}. In "
            f"practice that means one place for financial visibility, capital project "
            f"and compliance work (like FISP and Local Law 97), resident support, and "
            f"day-to-day operations, instead of juggling separate vendors for each.\n\n"
            f"Happy to go deeper on whichever part is most relevant for {name}. Worth "
            f"a quick call?" + sig
        ),
        "is_stub": True,
    }

    scale_and_traction = {
        "label": "Scale & traction",
        "subject": f"A bit more about {COMPANY_NAME}",
        "body": (
            f"{greeting},\n\nGood question. {COMPANY_NAME} runs a unified property "
            f"management platform for condo and co-op boards and HOAs. Right now "
            f"we're managing 258+ buildings and growing, with $310M+ in budgets under "
            f"management and a 130-person onsite team. Boards get real-time financial "
            f"visibility instead of waiting on a manager for updates.\n\nWould it be "
            f"useful to walk through how that could apply to {name} specifically?"
            + sig
        ),
        "is_stub": True,
    }

    resident_experience = {
        "label": "Resident & board experience",
        "subject": f"How {COMPANY_NAME} works day to day",
        "body": (
            f"{greeting},\n\nOf course. The short version: {COMPANY_NAME} replaces "
            f"the usual siloed setup (separate people for finances, maintenance, "
            f"compliance, resident requests) with one team and platform. Residents get "
            f"24/7 support with about a 2-minute average response time, and boards get "
            f"clear visibility into finances and ongoing projects rather than "
            f"surprises.\n\nHappy to jump on a quick call if you want the full picture."
            + sig
        ),
        "is_stub": True,
    }

    return [product_overview, scale_and_traction, resident_experience]


def _stub_reply_variants(company: dict, reply_text: str) -> list:
    if _is_asking_about_company(reply_text):
        return _stub_about_company_variants(company)

    name = company.get("name", "your company")
    contact = company.get("contact_name")
    greeting = f"Hi {contact}" if contact else "Hi there"
    sig = f"\n\nBest,\n{AGENT_NAME}\n{COMPANY_NAME}"
    quote = _snippet(reply_text)

    direct_next_step = {
        "label": "Direct next step",
        "subject": f"Re: {name} and {COMPANY_NAME}",
        "body": (
            f"{greeting},\n\nThanks for getting back to me, really glad to hear it. "
            f"On what you mentioned ({quote}), that makes sense, and I think the best "
            f"next step is a quick call to talk specifics. I'll loop in {HUMAN_NAME} "
            f"from {COMPANY_NAME}, who can find a time that works for you.\n\nSound "
            f"good?" + sig
        ),
        "is_stub": True,
    }

    clarifying = {
        "label": "Clarifying question",
        "subject": f"Quick follow-up, {name}",
        "body": (
            f"{greeting},\n\nAppreciate the reply. Just want to make sure I understand "
            f"before we move forward: you mentioned {quote}. Can you tell me a bit more "
            f"about what you're looking for there?\n\nHappy to jump on a quick call too "
            f"if that's easier." + sig
        ),
        "is_stub": True,
    }

    warm_continuation = {
        "label": "Warm continuation",
        "subject": f"Good to hear from you, {name}",
        "body": (
            f"{greeting},\n\nThis is great to hear. Taking in what you said ({quote}), "
            f"I think there's real alignment here. Let's find time to talk it through "
            f"properly. I'll have {HUMAN_NAME} from {COMPANY_NAME} reach out to get "
            f"something on the calendar, unless you'd rather coordinate directly with "
            f"me for now.\n\nEither way, looking forward to it." + sig
        ),
        "is_stub": True,
    }

    return [direct_next_step, clarifying, warm_continuation]
