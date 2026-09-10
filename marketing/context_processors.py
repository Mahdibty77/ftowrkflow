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

THE GATE WAS WRONG FOR A WHILE, AND IS FIXED HERE RATHER THAN QUIETLY LEFT.
An earlier version of this module gated on ``marketing/access.py
::can_reach_marketing`` — a Marketing seat, or GM/admin — because at the time
that was also the entire population that could ever HOLD a reminder: only
someone who could open ``reminder_add`` could create one. The "My Tasks" round
moved the ground out from under that gate without anyone updating it:
reminders (and the personal "My Tasks" hub they live in) are now open to
EVERY logged-in user with a linked ``people.Person`` record, seat or no seat
at all — see ``marketing/views.py``'s own "My Tasks" section head comment, and
``core/context_processors.py::theme``'s ``nav_show_person_requests``, which
opens the sidebar's "Personal" nav group (and, inside it, "My Tasks") on the
IDENTICAL test. Gating the BANNER on ``can_reach_marketing`` left a plain
Commercial Expert, or a person with no seat whatsoever, with a due reminder
that showed correctly in their own My Tasks table but never surfaced as the
top-of-page notification anywhere else in the site — the notification's own
reason for existing ("the top of the screen wherever the person happens to
be") quietly failing for most of its actual audience while still working for
the one population — a Marketing seat holder — that happened to satisfy a gate
nobody had gone back to widen.

THE OWNER'S CONSTRAINT IS WHAT SHAPES THE ORDER OF THE TWO CHECKS. "This must
not make the site slow or heavy", so:

1. THE CHEAP GATE RUNS FIRST. ``people.work_shift.person_for_user`` answers
   from a per-request memo on the ``User`` instance (see that function's own
   docstring — the identical memo ``core.context_processors.theme`` already
   reads for ``nav_show_person_requests``, so a page that renders both context
   processors pays for this lookup once, not twice), so a login with no linked
   Person record — an account that was never given a personnel record at all —
   returns ``None`` cheaply and the reminder machinery is never entered. This
   is now the honest gate: it is exactly "does this login have anything a
   reminder could belong to", which is the same question ``my_tasks``'s own
   view-level gate asks on arrival (see ``marketing/views.py
   ::_my_tasks_person_or_redirect``) — a banner that could fire for a login
   the destination page itself would turn away is exactly the kind of drift
   this project keeps one gate for, not two that could disagree.
2. ONLY THEN THE CACHED LOOKUP. ``marketing/reminders.py::due_notification``
   serves the answer from the shared cache when it can, and otherwise runs ONE
   indexed query for this one user. See that module for the index, for what the
   cache is worth on each backend, and for why every write invalidates it.

There is no scheduler, no background job and no polling loop anywhere in this
feature; "due" is a comparison made at render time. See
``marketing/models.py::ReminderState`` for why that is not a shortcut.

WHY AN ADMIN, THE GENERAL MANAGER, OR ANYONE WITH NO REMINDERS AT ALL PASSES
THE GATE AND STILL SEES NOTHING. ``due_notification`` (via
``marketing/reminders.py::_due_rows``) filters on ``owner=user`` alone — no
seat check of its own, verified by reading that module fresh rather than
assumed from an earlier round's investigation — so it only ever returns rows
this exact login created for themselves. A platform admin or a General
Manager has a linked Person record only if one was deliberately set up for
them (most are not), and even then they can only ever see a reminder they
personally set — the query never returns anyone else's. That is the right
outcome twice over: the banner never shows a viewer somebody else's private
note, and it never shows them one of their own that does not exist.

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

AUDIT #134 FOLLOW-UP (this round) — ``reminder_bell``, ONE NEW ADDITIVE KEY,
FOR A WIDGET THAT NOW RENDERS EVEN WITH NOTHING DUE. The owner's ask this
round was that the topbar bell itself should always be there (and always
clickable straight into My Tasks) for anyone who could ever hold a reminder,
styled neutrally when nothing is due and only turning urgent — coloured,
with the shake ``static/css/app.css`` now drives — once something actually
is. ``reminder_notice`` could not be widened to carry that on its own
without breaking its existing, narrower contract: it is read all over
``base.html`` with a plain ``{% if reminder_notice %}`` specifically to mean
"something is due", and every one of those checks (the coloured styling, the
badge, the always-visible summary bubble, the dropdown panel's own content)
still needs to mean exactly that after this round, not "the bell merely
exists". So this is a SECOND, independent key instead: ``True`` whenever the
gate below is passed, ``{}``/absent whenever it is not (the same "missing
key and a falsy one read identically" reasoning :func:`reminder_notice`'s
own docstring already gives for why it returns ``{}`` and not a key holding
``None``). It costs nothing extra to compute — it is just "did
``person_for_user`` return something", which the function already had to
know before it could decide whether to run the due-reminder query at all.
"""
from __future__ import annotations


def reminder_notice(request):
    """``{"reminder_notice": {...}, "reminder_bell": True}`` when one of
    THIS person's reminders is due; ``{"reminder_bell": True}`` alone when
    this login could hold one but nothing is due right now; ``{}`` when this
    login could never hold a reminder at all (or is not authenticated, or
    the lookup failed — see the module docstring's "FAILS SOFT" section).

    TWO SEPARATE SIGNALS, ON PURPOSE — see the module docstring's "AUDIT
    #134 FOLLOW-UP" section for the fuller reasoning. ``reminder_notice``
    keeps meaning exactly what it always meant ("something is due, and here
    is its data"); ``reminder_bell`` is the new, wider "may this login see
    the bell AT ALL" flag ``base.html`` now gates the whole widget on, so a
    person with nothing currently due still gets a neutral, clickable bell
    instead of the widget vanishing outright.

    Both keys are plain empty-dict-or-populated, never a key holding
    ``None``: ``base.html`` tests each with a plain ``{% if %}``, and a
    missing key and a falsy one read identically there — while ``{}`` keeps
    a template that never looks at either from paying for a variable it does
    not use. Every other context processor in this project returns ``{}``
    from its own no-op paths for the same reason.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    try:
        from people.work_shift import person_for_user

        # THE SAME GATE ``nav_show_person_requests`` USES, NOT
        # ``can_reach_marketing`` — see the module docstring's "THE GATE WAS
        # WRONG FOR A WHILE" section. A reminder belongs to a LOGIN, and every
        # login with a linked Person may hold one via "My Tasks" now, seat or
        # no seat at all; a Marketing-seat check here would silently withhold
        # the banner from most of that population while still letting them
        # see the identical reminder in their own My Tasks table.
        if person_for_user(user) is None:
            return {}
        from . import reminders

        notice = reminders.due_notification(user, request)
    except Exception:
        # See the module docstring: the banner is a decoration on every page in
        # the site, and no failure of it may cost the page.
        return {}
    # Past this point the login has definitely passed the person-record gate
    # above, so the bell itself always renders now (AUDIT #134 follow-up) —
    # neutrally styled by base.html/app.css when `notice` is empty, urgent
    # when it is not. `reminder_notice` is only added to the context when
    # there IS something due, preserving its old, narrower contract exactly.
    if not notice:
        return {"reminder_bell": True}
    return {"reminder_notice": notice, "reminder_bell": True}
