"""The "a reminder is due" notification, for every page this person opens.

WHY A CONTEXT PROCESSOR AT ALL. The notification has to appear at the top of the
screen wherever the person happens to be, which is a property of the LAYOUT
(``core/templates/base.html``) and not of any one view. This project already
does exactly that four times over — ``core.context_processors.theme`` feeds the
sidebar and its badges, ``impersonation_status`` feeds the "return to admin"
banner, ``people.context_processors.work_shift_banner`` feeds the end-of-shift
strip and ``licensing.context_processors.license_status`` feeds the licence
warning — each an app-owned module registered in ``settings.TEMPLATES``. This is
the fifth, written the same way, rather than a new mechanism beside them.

FAILS SOFT, LIKE EVERY OTHER ONE. ``core/context_processors.py`` says it in its
own first paragraph and means it: a context processor runs on EVERY rendered
page, so one that raises takes down the page it was only meant to decorate.
Everything below is wrapped, and every failure path returns ``{}`` — the same
neutral value an anonymous visitor gets — so a malformed seat row, a cache
backend that is down or a missing table can cost the banner and nothing else.

THE OWNER'S CONSTRAINT IS WHAT SHAPES THE ORDER OF THE TWO CHECKS. "This must
not make the site slow or heavy", so:

1. THE CHEAP GATE RUNS FIRST. ``marketing/access.py::can_reach_marketing``
   answers from the seat data ``core.context_processors.theme`` has ALREADY
   loaded and memoised for this same request (``people.role_nav`` keeps a
   per-request memo, which is why ``access._person_roles`` goes through it), so
   for the overwhelming majority of logins — Commercial, Technical, Supply, with
   no Marketing seat at all — this returns False having touched the database not
   once, and the reminder machinery is never entered. It is also the honest
   gate: only these people can hold a reminder, because only they can reach the
   screen that creates one.
2. ONLY THEN THE CACHED LOOKUP. ``marketing/reminders.py::due_notification``
   serves the answer from the shared cache when it can, and otherwise runs ONE
   indexed query for this one user. See that module for the index, for what the
   cache is worth on each backend, and for why every write invalidates it.

There is no scheduler, no background job and no polling loop anywhere in this
feature; "due" is a comparison made at render time. See
``marketing/models.py::ReminderState`` for why that is not a shortcut.

WHY THE ADMIN AND THE GENERAL MANAGER PASS THE GATE AND STILL SEE NOTHING. Both
are inside ``can_reach_marketing`` (they may open the section, read-only), so
they do reach step 2 — and it answers ``{}`` for them, because they cannot
create a reminder (``views.reminder_add`` is gated on ``can_edit``, which they
do not have) and this query only ever returns rows they own themselves. That is
the right outcome twice over: the banner never shows them somebody else's
private note, and it never shows them one of their own that cannot exist.

WHAT THE BANNER RENDERS, AND THE CHECK IT RUNS. The payload carries the
attached case's DOCUMENT NUMBER, and that number IS put through
``marketing/access.py::case_access_for``, exactly as ``services._report_row``
puts a report's case number through it.

An earlier version of this module argued the opposite at length: a reminder has
exactly one reader, who was shown that very number by the scoped picker when
they chose it, so there was no third party for the check to protect, and running
it would cost a query on every page render. Both halves were wrong. A seat can
LOSE case access after the reminder is set - the Commercial ``PersonRole`` is
removed and the person is Marketing-only - and the banner then went on printing
the number on every page while the company page's own Cases tab showed that same
person nothing. Two screens contradicting each other about one viewer is the
precise failure ``case_access_for`` exists to prevent, and this app has had to
close that same hole three times already. The cost objection was wrong because
the payload is CACHED: the check runs when the payload is BUILT, not per render,
so a warm page pays nothing for it.

``marketing/reminders.py::_visible_ids_for`` resolves it and fails CLOSED - if
there is no request to ask, or the access layer raises, the number is withheld
and the rest of the banner still renders.
"""
from __future__ import annotations


def reminder_notice(request):
    """``{"reminder_notice": {...}}`` when one of THIS person's reminders is
    due, and ``{}`` the rest of the time.

    An empty dict, not a key holding ``None``: ``base.html`` tests the key with
    a plain ``{% if %}``, and a missing key and a falsy one read identically
    there — while ``{}`` keeps a template that never looks at it from paying for
    a variable it does not use. Every other context processor in this project
    returns ``{}`` from its own no-op paths for the same reason.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    try:
        from .access import can_reach_marketing

        if not can_reach_marketing(request):
            return {}
        from . import reminders

        notice = reminders.due_notification(user, request)
    except Exception:
        # See the module docstring: the banner is a decoration on every page in
        # the site, and no failure of it may cost the page.
        return {}
    if not notice:
        return {}
    return {"reminder_notice": notice}
