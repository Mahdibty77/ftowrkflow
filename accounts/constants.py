"""Organisational units and roles used across the platform.

An organisation is modelled as UNIT x ROLE. Each unit may have any combination
of a manager, a supervisor and one or more experts. A unit can also exist with
only some of those roles populated (for example a Technical unit with only a
manager and no supervisor) - the user accounts are still defined, they are
simply not assigned.

Originally three of the four units - Commercial, Technical, Supply - were the
ones the TO/PI case workflow routed between; MARKETING was the fourth, and is
deliberately outside that workflow: it holds no case, appears in no routing
rule, and has neither an inbox nor a case archive. "Marketing Observer" and
"Marketing Expert" are therefore not new roles at all - they are this new unit
paired with the two roles that already exist, which is why nothing had to be
added to Role. (Adding them to Role instead would have handed every other unit
a "Marketing Expert" seat it should never have.)

PURCHASING and WAREHOUSE joined the workflow later, for the stage that begins
once a case is Final Approved: Purchasing builds a Purchase Invoice (a fourth
FormKind, alongside Inquiry/TO/PI) and sends it on to Warehouse. Unlike every
earlier handoff in this workflow, Purchasing's own turn does NOT take the case
away from Commercial - Commercial keeps its existing Burn / Final Closed power
the entire time Purchasing is working, on purpose (the product owner's own
instruction): two units hold real access to the same case at once, which nothing
before this needed. See cases/services.py's inbox_filter_q and allowed_actions
for exactly how that is kept safe (Purchasing's own branch keys off the SAME
FINAL_APPROVED status Commercial's branch already uses, rather than a status
of its own the way every other unit's branch has one).

QC (Quality Control) exists as a seatable unit - CHOICES below, seats can be
created for it - but is deliberately NOT in WORKFLOW yet, because its own
behaviour has not been specified. Giving a unit a real Case-routing role without
building the matching allowed_actions/inbox_filter_q branches is not a smaller,
safer version of adding it - it is a silent near-total lockout (the seat clears
every gate that would have refused it, then matches no branch and is left with
next to no actions at all) - see the MARKETING precedent above, and do not
repeat this mistake for QC before its behaviour is actually designed.

ROLE LABELS VS. ROLE CODES - read this before renaming anything again. MANAGER
and SUPERVISOR are permanent, permission-bearing codes (every gate in the
codebase checks ``Role.MANAGER``/``Role.SUPERVISOR``, never the display text)
but their CHOICES labels below are not "Manager"/"Supervisor" - a rename swept
through the whole platform: MANAGER now displays as "Supervisor" / "سرپرست"
(taking over the words SUPERVISOR used to show), and SUPERVISOR now displays
as "Observer" / "ناظر". Nothing about who holds which code, or what either
code is allowed to do, changed - only the label. Every OTHER string in this
codebase that spelled either role out inside a longer sentence rather than
through ``Role.LABELS`` (approval-request banners, the archive "Manager view"
toggle, "Marketing Supervisor" in the Marketing docs/help text) was updated to
match in the same pass - see the locale catalog for the full list of msgids
this touched.
"""
from django.utils.translation import gettext_lazy as _

# CHOICES BELOW ARE ``gettext_lazy``-WRAPPED, NOT PLAIN STRINGS. Unit.LABELS /
# Role.LABELS / SupplyKind.LABELS / Gender.LABELS feed
# accounts.models.Profile.unit_label / role_label / title_line — rendered on
# nearly every page (the sidebar footer, People, the Console, a company's
# sub-header, a case's "holding unit" field) — so a plain string here would
# show "Marketing · Expert" to a Persian-language viewer exactly as it shows
# to an English one. ``gettext_lazy`` (not the eager ``gettext``) is required
# because these CHOICES lists are built once, at import time, long before any
# request (and therefore any viewer's chosen language) exists — a lazy proxy
# instead re-resolves against whichever language is active at the moment it
# is finally rendered to text, per request, after
# accounts.middleware.LanguageMiddleware has activated that viewer's own
# language. Same reasoning cases/constants.py's own CHOICES docstring spells
# out in full for CaseStatus/EventAction; this module simply did not have it
# applied yet.
class Unit:
    COMMERCIAL = "COMMERCIAL"
    TECHNICAL = "TECHNICAL"
    SUPPLY = "SUPPLY"
    # Outside the TO/PI workflow - see the module docstring.
    MARKETING = "MARKETING"
    # Joined the workflow for the post-Final-Approved stage - see the module
    # docstring's "PURCHASING and WAREHOUSE" section.
    PURCHASING = "PURCHASING"
    WAREHOUSE = "WAREHOUSE"
    # Seatable, deliberately NOT in WORKFLOW yet - see the module docstring's
    # "QC" section before ever adding this to WORKFLOW.
    QC = "QC"

    # Units a case can actually be routed to. Named on its own so a
    # routing/archive/report rule can ask "is this a workflow unit" without
    # spelling them all out again, and so that adding a non-workflow unit
    # (MARKETING, QC) later cannot silently widen any of them.
    WORKFLOW = (COMMERCIAL, TECHNICAL, SUPPLY, PURCHASING, WAREHOUSE)

    CHOICES = [
        (COMMERCIAL, _("Commercial")),
        (TECHNICAL, _("Technical")),
        (SUPPLY, _("Supply")),
        (MARKETING, _("Marketing")),
        (PURCHASING, _("Purchasing")),
        (WAREHOUSE, _("Warehouse")),
        (QC, _("Quality Control")),
    ]

    LABELS = dict(CHOICES)


class Role:
    # Codes are permanent and permission-bearing - never renamed. See this
    # module's own docstring ("ROLE LABELS VS. ROLE CODES") for why MANAGER's
    # own CHOICES label below reads "Supervisor" and SUPERVISOR's reads
    # "Observer": a display-only rename, not a role swap.
    MANAGER = "MANAGER"
    SUPERVISOR = "SUPERVISOR"
    EXPERT = "EXPERT"

    CHOICES = [
        (MANAGER, _("Supervisor")),
        (SUPERVISOR, _("Observer")),
        (EXPERT, _("Expert")),
    ]

    LABELS = dict(CHOICES)


class SupplyKind:
    """Supply experts are split into Internal and External (managers/supervisors are not)."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"

    CHOICES = [
        (INTERNAL, _("Internal Supply")),
        (EXTERNAL, _("External Supply")),
    ]
    LABELS = dict(CHOICES)


class Gender:
    """Used only to choose the honorific (Mr./Ms.) shown before a signer's
    last name on exported documents — see accounts.models.Profile.honorific.
    """
    MALE = "MALE"
    FEMALE = "FEMALE"

    CHOICES = [
        (MALE, _("Male")),
        (FEMALE, _("Female")),
    ]
    LABELS = dict(CHOICES)

    # Deliberately plain strings, NOT gettext_lazy: this dict feeds
    # honorific_last_name, which is stamped onto EXPORTED DOCUMENTS (the
    # signature/identity box on a TO/PI PDF) — a fixed business convention of
    # the document itself, not platform chrome, and it must read the same
    # "Mr./Ms." regardless of which language the signer happens to have
    # chosen in their own Settings. Out of this round's scope; see the task
    # that added Unit/Role/SupplyKind/Gender.CHOICES above for the boundary.
    HONORIFIC = {MALE: "Mr.", FEMALE: "Ms."}


class Language:
    """The two interface languages a person can pick in Settings, for the
    platform CHROME only — sidebar, tabs, buttons, field labels, status
    labels, page titles. Never the CONTENT: a company's own name, a person's
    own comment/report/reminder text, or anything else someone typed keeps
    whatever language it was written in regardless of this setting.

    Unlike every other choice class in this module, the codes here are
    lowercase ("en" / "fa"), not uppercase — because these are not this
    project's own vocabulary, they are handed straight to Django's own i18n
    machinery (django.utils.translation.activate, the gettext catalog
    lookup, and the settings.LANGUAGES / LOCALE_PATHS wiring in
    ftworkflow/settings.py), which only recognises lowercase language codes.
    See accounts.models.Profile.language for the per-person, server-side
    record of this choice, and accounts.middleware.LanguageMiddleware for
    where it is activated on every request.
    """
    ENGLISH = "en"
    PERSIAN = "fa"

    CHOICES = [
        (ENGLISH, "English"),
        (PERSIAN, "فارسی"),
    ]
    LABELS = dict(CHOICES)
