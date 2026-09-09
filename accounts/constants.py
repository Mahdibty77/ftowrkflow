"""Organisational units and roles used across the platform.

An organisation is modelled as UNIT x ROLE. Each unit may have any combination
of a manager, a supervisor and one or more experts. A unit can also exist with
only some of those roles populated (for example a Technical unit with only a
manager and no supervisor) - the user accounts are still defined, they are
simply not assigned.

Three of the four units - Commercial, Technical, Supply - are the ones the
TO/PI case workflow routes between. MARKETING is the fourth, and it is
deliberately outside that workflow: it holds no case, appears in no routing
rule, and has neither an inbox nor a case archive. "Marketing Supervisor" and
"Marketing Expert" are therefore not new roles at all - they are this new unit
paired with the two roles that already exist, which is why nothing had to be
added to Role. (Adding them to Role instead would have handed every other unit
a "Marketing Expert" seat it should never have.)
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

    # The three units a case can actually be routed to. Named on its own so a
    # routing/archive/report rule can ask "is this a workflow unit" without
    # spelling the three out again, and so that adding a fifth non-workflow
    # unit later cannot silently widen any of them.
    WORKFLOW = (COMMERCIAL, TECHNICAL, SUPPLY)

    CHOICES = [
        (COMMERCIAL, _("Commercial")),
        (TECHNICAL, _("Technical")),
        (SUPPLY, _("Supply")),
        (MARKETING, _("Marketing")),
    ]

    LABELS = dict(CHOICES)


class Role:
    MANAGER = "MANAGER"
    SUPERVISOR = "SUPERVISOR"
    EXPERT = "EXPERT"

    CHOICES = [
        (MANAGER, _("Manager")),
        (SUPERVISOR, _("Supervisor")),
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
