"""The Marketing unit's own screens.

One screen so far — the Marketing Expert workspace — and one tab on it, the
project role chart. The tab strip is real markup rather than a heading, so the
second tab is a line in ``TABS`` and a block in the template and nothing else;
see the note on ``TABS`` for why it has only one entry today.

WHO MAY OPEN IT. Everything under this app is Marketing's own work, so the
rule is a single one and it is spelled out in ``_viewer_unit`` and applied by
``_marketing_only``: the viewer's ACTIVE seat must belong to the Marketing
unit. That is the whole gate. In particular:

* Marketing Expert  — yes. This is their workspace.
* Marketing Supervisor — yes, and not grudgingly: a supervisor who cannot see
  the work of their own unit is not supervising it. A Marketing Manager, if
  the unit is ever given one, is admitted by the same rule, because the rule
  is about the unit and not about the rank inside it.
* Commercial / Technical / Supply, at every rank — no. A hidden nav link is
  not a permission; this is enforced here, on the URL, and answers 403 to a
  direct GET.
* The general manager — no. The GM's window on this platform is
  ``reports.dashboard``, which is built from case figures; Marketing holds no
  case and has no dashboard card, and inventing a way in here would be
  inventing the report that was deliberately not built. When Marketing's own
  numbers are specified, that is the moment to decide what the GM sees.
* An administrator — no, for the same reason it is not a Commercial screen:
  ``is_admin`` on this platform is the user-and-code-table console, not a
  master key over unit work. An admin who needs to look at this page can be
  given a Marketing seat, which is the platform's own answer for "somebody
  needs to see another unit's screen" and leaves a trace that they did.

Anyone refused gets the same short page with a 403 on it, inside their own
navigation, rather than a redirect — a redirect would have to choose a
destination for a seat kind this app knows nothing about, and "you are not in
this unit" is the honest answer to give.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.constants import Unit

from . import rolechart

# The tab strip on the Marketing Expert workspace.
#
# ONE ENTRY, on purpose. The strip itself is here — the markup, the active
# state, the ``?tab=`` handling — because the owner asked for a tabbed screen
# and because a strip added later is a bigger change than a strip added now.
# What is NOT here is invented company: no second tab is listed, because no
# second tab has been specified, and a "Campaigns — coming soon" placeholder
# would be this app promising a screen nobody has asked for. Adding the real
# second tab is one tuple below and one {% if %} in the template.
TABS = (
    ("roles", "Projects & Roles"),
)
DEFAULT_TAB = TABS[0][0]


def _viewer_unit(request):
    """The unit of the seat this person is actually working right now.

    Resolved exactly the way ``cases.views.case_detail`` resolves it, and for
    the same reason: one human may hold several seats, and on a secondary seat
    the login's own ``Profile`` still describes the PRIMARY one. Reading the
    profile alone would refuse a Commercial expert who also holds a Marketing
    seat and has switched to it — the seat the sidebar is offering this link
    from. The profile is the fallback for a login with no PersonRole rows at
    all, which is most of them.
    """
    profile = getattr(request.user, "profile", None)
    fallback = (getattr(profile, "unit", "") or "").strip()
    try:
        from people.role_nav import work_context
        role = work_context(request).role
    except Exception:
        # The seat layer is an enhancement over the profile, never a
        # precondition for it: if it cannot answer, the profile still can, and
        # a page must not 500 because a seat row is malformed.
        return fallback
    if role is None:
        return fallback
    return (getattr(role, "unit", "") or "").strip() or fallback


def _marketing_only(request):
    """``None`` when the viewer may proceed, otherwise the refusal to return."""
    if _viewer_unit(request) == Unit.MARKETING:
        return None
    return render(request, "marketing/denied.html", status=403)


@login_required
def home(request):
    """The Marketing Expert workspace.

    The chart's data comes from ``rolechart.sample_chart()`` — a plain Python
    dict built from a literal, with no model, no query and no case behind it.
    That call is the seam: when the owner specifies how a project and its role
    rows are stored, this line becomes the query that builds the same
    ``{slot: organisation}`` mapping and hands it to ``rolechart.build()``, and
    neither the template nor the stylesheet changes. See the module docstring
    of ``marketing/rolechart.py``.
    """
    refusal = _marketing_only(request)
    if refusal is not None:
        return refusal

    active = (request.GET.get("tab") or "").strip() or DEFAULT_TAB
    if active not in dict(TABS):
        active = DEFAULT_TAB

    return render(request, "marketing/home.html", {
        "tabs": [{"key": k, "label": lb, "is_active": k == active}
                 for k, lb in TABS],
        "active_tab": active,
        "chart": rolechart.sample_chart(),
    })
