"""Who the outreach agent is, for drafting copy and UI labels.

Change these three constants to re-brand the agent persona without touching
drafting logic elsewhere.
"""

AGENT_NAME = "Dana"
COMPANY_NAME = "Daisy"
HUMAN_NAME = "Maya"

# Real facts about Daisy (joindaisy.com), so the drafting agent can answer
# "what does Daisy do?" accurately instead of describing it generically or
# making something up. Keep this short and factual -- it's meant to be
# selectively drawn from in a reply, not dumped verbatim into an email.
COMPANY_ABOUT = (
    "Daisy is a modern, tech-forward property management platform for condo "
    "and co-op boards and HOAs. It replaces siloed teams and an overworked "
    "property manager with one unified operating system covering: financial "
    "management (real-time visibility into a building's finances), capital "
    "projects (FISP and Local Law 97 compliance work, delivered on time and "
    "on budget), resident experience (24/7 support through an app, ~2 minute "
    "average response time), compliance (inspections, violation resolution), "
    "and day-to-day operations (predictive maintenance, common space "
    "upkeep). Daisy currently manages 258+ buildings and growing, with "
    "$310M+ in budgets under management, a 130-person onsite team, and runs "
    "80+ board meetings a month."
)
