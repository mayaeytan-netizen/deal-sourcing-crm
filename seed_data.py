"""Fictional but plausible property management / real estate seed companies."""

from datetime import date, timedelta

TODAY = date.today()


def _d(days_ago):
    return (TODAY - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# Each entry: name, contact_name, contact_email, website, notes, stage,
# days_since_added, days_since_last_touch (None if never touched), total_touches, replies
SEED_COMPANIES = [
    dict(
        name="Meridian Property Group",
        contact_name="Dana Whitfield",
        contact_email="dana@meridianpg.com",
        website="meridianpg.com",
        notes="Mid-size PM company in Columbus, OH; ~1,400 units under management. "
              "Has been expanding into flips in up-and-coming neighborhoods since 2023.",
        stage="Not Contacted",
        days_added=2, days_last_touch=None, total_touches=0,
    ),
    dict(
        name="Harborline Residential",
        contact_name="Marcus Ibe",
        contact_email="marcus@harborline.com",
        website="harborline.com",
        notes="Boston-area PM firm, family-run, ~600 units. Mentioned on LinkedIn "
              "they're looking to grow their acquisitions arm in 2026.",
        stage="Not Contacted",
        days_added=1, days_last_touch=None, total_touches=0,
    ),
    dict(
        name="Cobalt Realty Partners",
        contact_name="Priya Nandakumar",
        contact_email="priya@cobaltrp.com",
        website="cobaltrealtypartners.com",
        notes="Phoenix-based, buys distressed single-family homes and resells "
              "after light renovation. Does ~40 flips/year.",
        stage="Contacted",
        days_added=14, days_last_touch=4, total_touches=1,
    ),
    dict(
        name="Stonegate Asset Management",
        contact_name="Eli Brandt",
        contact_email="eli.brandt@stonegateam.com",
        website="stonegateam.com",
        notes="Regional PM + light flipping operation across the Carolinas. "
              "Recently raised a small fund to buy more SFR inventory.",
        stage="Contacted",
        days_added=20, days_last_touch=6, total_touches=1,
    ),
    dict(
        name="Pinewood Capital Holdings",
        contact_name="Renee Castillo",
        contact_email="renee@pinewoodcap.com",
        website="pinewoodcapital.com",
        notes="Denver, mixed PM + BRRRR investor. Fast replies on LinkedIn, "
              "seems very active in local investor meetups.",
        stage="Follow-up 1",
        days_added=25, days_last_touch=3, total_touches=2,
    ),
    dict(
        name="Lakeside Home Partners",
        contact_name="Tom Okafor",
        contact_email="tom@lakesidehp.com",
        website="lakesidehomepartners.com",
        notes="Chicago-suburbs PM shop managing ~900 units; website mentions a "
              "sister acquisitions team that buys 2-4 unit multifamily to flip.",
        stage="Follow-up 1",
        days_added=23, days_last_touch=4, total_touches=2,
    ),
    dict(
        name="Brightfield Property Co.",
        contact_name="Alexis Moreau",
        contact_email="alexis@brightfieldpc.com",
        website="brightfieldpropertyco.com",
        notes="Atlanta PM company, growing fast -- added 300 doors last year "
              "per a local business journal writeup. No flip arm mentioned yet.",
        stage="Follow-up 2",
        days_added=31, days_last_touch=3, total_touches=3,
    ),
    dict(
        name="Redwood Trail Management",
        contact_name="Sam Okonkwo",
        contact_email="sam@redwoodtrailmgmt.com",
        website="redwoodtrailmgmt.com",
        notes="Portland-area, boutique PM + renovation-and-resell shop. "
              "~15 flips/year, mostly bungalow-style homes.",
        stage="Follow-up 2",
        days_added=29, days_last_touch=4, total_touches=3,
    ),
    dict(
        name="Summit Ridge Realty",
        contact_name="Jordan Pak",
        contact_email="jordan@summitridgerealty.com",
        website="summitridgerealty.com",
        notes="Salt Lake City; replied to first follow-up asking for more detail "
              "on deal flow and typical margins.",
        stage="Responded",
        days_added=18, days_last_touch=2, total_touches=2,
    ),
    dict(
        name="Ironwood Property Solutions",
        contact_name="Keisha Ward",
        contact_email="keisha@ironwoodps.com",
        website="ironwoodps.com",
        notes="Dallas-Fort Worth, replied enthusiastically and set up an intro "
              "call for next week -- wants to see a sample deal packet.",
        stage="Interested",
        days_added=40, days_last_touch=5, total_touches=3,
    ),
    dict(
        name="Clearview Housing Group",
        contact_name="Nate Fielding",
        contact_email="nate@clearviewhg.com",
        website="clearviewhousinggroup.com",
        notes="Tampa, mid-size PM; expressed interest and asked for a follow-up "
              "deck covering recent flips and typical hold times.",
        stage="Interested",
        days_added=35, days_last_touch=6, total_touches=3,
    ),
    dict(
        name="Thornbury Estates Management",
        contact_name="Olivia Hargrove",
        contact_email="olivia@thornburyem.com",
        website="thornburyestatesmgmt.com",
        notes="Said they already have an exclusive sourcing relationship and "
              "aren't looking to add another partner right now.",
        stage="Declined",
        days_added=45, days_last_touch=10, total_touches=2,
    ),
    dict(
        name="Vantage Point Realty Advisors",
        contact_name="Chris Dubois",
        contact_email="chris@vantagepointra.com",
        website="vantagepointra.com",
        notes="Nashville; after three touches went quiet entirely, no reply on "
              "any channel. Deprioritizing for now.",
        stage="Declined",
        days_added=50, days_last_touch=14, total_touches=4,
    ),
    dict(
        name="Birchwood Capital Partners",
        contact_name="Maria Suarez",
        contact_email="maria@birchwoodcp.com",
        website="birchwoodcapitalpartners.com",
        notes="Signed a small pilot deal-sourcing agreement in September; first "
              "deal already closed through the partnership.",
        stage="Closed",
        days_added=70, days_last_touch=20, total_touches=5,
    ),
]
