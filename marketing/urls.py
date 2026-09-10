"""Two entry points: the Marketing workspace, and the company directory.

``/marketing/`` is the workspace — the role chart (see ``views.home``). That
screen used to carry a tab strip, driven by a ``?tab=…`` query parameter
rather than a route of its own per tab. It has one panel now — the role chart
— so there is no ``tab`` parameter any more and nothing links to one; an
unknown query string on this URL is simply ignored, as it always was. The
only parameter the page still reads is the optional ``?case=`` deep link (see
``views.home``).

``/marketing/directory/`` is the company directory — the section that replaced
the "Companies" tab that screen used to carry (see ``views.py``'s module
docstring). REAL ROUTES, one per screen, not a ``?tab=`` on an existing page:
a company detail page is a destination a reader links to, bookmarks and comes
back to, which is exactly what the tab parameter could never be, and it is why
the case archive and the case detail page are two URLs rather than one screen
with a switch on it. This section is modelled on that pair, so its URLs are
shaped the same way:

    directory/                      the list          -> marketing:directory
    directory/<pk>/                 one company       -> marketing:company_detail
    directory/<pk>/contacts/add/    add a contact     -> marketing:contact_add
    directory/<pk>/contacts/<id>/remove/
                                    remove a contact  -> marketing:contact_remove
    directory/<pk>/reports/add/     report, step 1    -> marketing:report_add
    directory/<pk>/reports/create/  report, step 2    -> marketing:report_create
    directory/<pk>/reminders/add/   set a reminder    -> marketing:reminder_add
    reminders/                      redirects to      -> marketing:my_tasks
    tasks/                          "My Tasks" hub     -> marketing:my_tasks
    tasks/all/                      admin-wide My Tasks -> marketing:my_tasks_all
    tasks/add/                      add / next reminder -> marketing:my_tasks_add
    tasks/<id>/report/               close it out       -> marketing:my_tasks_report
    tasks/reports/add/               standalone report   -> marketing:my_tasks_report_add

THE REMINDER LIST IS NOT UNDER ``directory/``, AND THAT IS THE POINT OF ITS
ADDRESS. Everything under ``directory/`` is about a COMPANY and carries that
company's id; a person's reminder list is about the PERSON — it spans every
company they have set one on — so it hangs off the section root instead, with no
id in the path at all. Whose list it is comes from the session and can never
come from the URL, which is the whole of the visibility rule (see
``marketing/models.py::Reminder``). SETTING one is the other way round: it is
reached from one company's page and is about that company, so it lives under
``directory/<pk>/`` beside the contact and report flows it is modelled on.

``tasks/`` IS THE NEW "MY TASKS" HUB (see ``views.my_tasks``'s own docstring)
AND ``reminders/`` NO LONGER RENDERS A PAGE OF ITS OWN — it 302s to
``tasks/`` so every existing link by that name keeps working (see
``views.reminder_list``). ``tasks/`` sits OUTSIDE ``directory/`` for the
identical reason ``reminders/`` always did: it is about the PERSON, not one
company, and it is reached from the sidebar's own "Personal" nav group rather
than from anywhere under this app's own Marketing-gated navigation — see
``core/templates/base.html``. ``tasks/add/`` serves BOTH "add a brand-new
task" and, carrying ``?client=``/``?case=``/``?next=``, the "set your next
reminder" step that follows closing one out — ONE route for both, because the
form the two moments show a person is the identical one (see
``views.my_tasks_add``). ``tasks/<id>/report/`` is the OTHER half of closing a
reminder out — writing the report that accounts for it
(``views.my_tasks_report``) — and it is POST-capable but NOT
``@require_POST``: unlike the retired ``reminder_retime``/``reminder_done``
pair (see the comment above ``tasks/`` in the URL list below), its GET
renders the write-a-report SCREEN (the way ``report_add``'s own GET does),
and its POST is the one request that actually calls
``marketing/reminders.py::close_with_report``.

``tasks/all/`` IS THE ADMIN-WIDE VARIANT OF ``tasks/`` — the SAME view
(``views.my_tasks``), reached with ``scope="all"`` bound in the URL itself
(a ``path()`` extra keyword argument, not a query string a viewer could
strip or add) rather than a second view function or a second template — see
``views.my_tasks``'s own docstring for the full story of why this is one
parameterised page and not two that could drift apart. It sits under
``tasks/`` rather than off the section root for the same "about the PERSON,
or here, about EVERY person" reasoning ``tasks/`` itself already carries,
just widened one step further. It is NOT reached from anywhere ``tasks/``
itself is (the sidebar's own "Personal" nav group) — its only real entry
point is the admin-only "People" nav group instead
(``people/templates/people/_nav.html``) — but the URL is gated on the view
itself (``views._my_tasks_is_admin``), a plain 403 to anyone else regardless
of how they arrived, exactly the reasoning the module docstring already gives
for every other admin-only refusal in this app.

``tasks/reports/add/`` IS A THIRD, SEPARATE ROUTE — the STANDALONE "ADD
REPORT" screen on My Tasks' own Reports tab (``views.my_tasks_report_add``),
reached with no reminder in play at all: company and case are both optional
and independently searchable rather than implied by a reminder or a URL.
It hangs off ``tasks/`` rather than ``directory/<pk>/`` for the identical
reason ``tasks/`` itself does — it is about the PERSON writing the report,
not about one company's own page. It is distinct from ``tasks/<id>/report/``
above (note the plural "reports" versus a reminder's own numeric id) and the
two can never collide: the ``<int:reminder_id>`` converter on that route
never matches the literal segment "reports".

THE MUTATING REMINDER ROUTE, TODAY, IS ``tasks/<id>/report/``
(``views.my_tasks_report``) — it carries the reminder's own id in the path,
exactly as the contact-removal route does, so the id the write is about
belongs in the address, not in a body parameter the view might forget to
re-check. It carries no owner of its own: the signed-in person IS the owner,
and a route that could name a different one would be a route that has to be
checked for it. THERE USED TO BE TWO SUCH ROUTES, ``reminders/<id>/time/``
and ``reminders/<id>/done/`` (``views.reminder_retime``/``reminder_done``),
POST-only in the identical shape — a plain reschedule or a mark-done with no
report ever required. Both are gone: they were a live, working bypass of the
mandatory close-only-via-a-report cycle this whole feature is built around,
still reachable from ``marketing/templates/marketing/company_detail.html``'s
own Reminders tab after the rest of the app had already moved to
close-via-report, and were retired for exactly that reason once nothing
legitimate was left calling them — see the comment left above ``tasks/`` in
the URL list below.

The removal route is POST-only (see ``views.contact_remove``) and carries the
contact's own id in the path rather than in the body, so the two ids a removal
is about — which company, which person — are both part of the address and
neither can be swapped by a body parameter the view forgot to re-check.

THE TWO REPORT ROUTES ARE THE OWNER'S TWO STEPS, and they are two URLs for the
same reason the archive and a case detail page are: ``report_add`` is a screen a
reader opens, reloads and comes back to (it only asks which case, if any, the
report is about), while ``report_create`` is the write and is POST-only (see
``views.report_create``). A single URL switching on the request method would
have made the writing step reachable by GET, which is precisely what
``contact_remove`` is shaped to avoid.

The ``<int:pk>`` is a ``cases.Client`` id — the SAME shared directory the chart
and Commercial both use, not a Marketing-only mirror of it (see
``marketing/models.py``'s module docstring), so a company's id means the same
thing here as it does everywhere else in the platform.

Everything under "entities/" is JSON, called by
``marketing/static/marketing/js/chart_interact.js``.
"""
from django.urls import path

from . import views

app_name = "marketing"

urlpatterns = [
    path("", views.home, name="home"),
    path("directory/", views.directory, name="directory"),
    path("directory/<int:pk>/", views.company_detail, name="company_detail"),
    path("directory/<int:pk>/contacts/add/", views.contact_add, name="contact_add"),
    path("directory/<int:pk>/contacts/<int:contact_id>/remove/",
         views.contact_remove, name="contact_remove"),
    path("directory/<int:pk>/reports/add/", views.report_add, name="report_add"),
    path("directory/<int:pk>/reports/create/",
         views.report_create, name="report_create"),
    path("directory/<int:pk>/reminders/add/",
         views.reminder_add, name="reminder_add"),
    path("reminders/", views.reminder_list, name="reminder_list"),
    # ``reminders/<id>/time/`` (``reminder_retime``) and ``reminders/<id>/
    # done/`` (``reminder_done``) USED TO BE HERE — a plain "set a new time"
    # / "mark dealt with" pair that closed (or reopened) a reminder with NO
    # report ever required. They were removed, routes and views both, as a
    # live bypass of the mandatory close-only-via-a-report cycle
    # ``tasks/<id>/report/`` below now enforces as the ONE way a reminder
    # ever closes — see the comment left in ``views.py`` where they used to
    # be defined for the fuller story, and this module's own "THE MUTATING
    # REMINDER ROUTE, TODAY" section below for what replaced them.
    path("tasks/", views.my_tasks, name="my_tasks"),
    # The admin-wide variant — see this module's own head comment's
    # "``tasks/all/``" section and ``views.my_tasks``'s own docstring. The
    # SAME view function as the line above, bound to ``scope="all"`` here in
    # the URL itself rather than read from anything a viewer's own request
    # could set.
    path("tasks/all/", views.my_tasks, {"scope": "all"}, name="my_tasks_all"),
    path("tasks/add/", views.my_tasks_add, name="my_tasks_add"),
    path("tasks/reports/add/",
         views.my_tasks_report_add, name="my_tasks_report_add"),
    path("tasks/<int:reminder_id>/report/",
         views.my_tasks_report, name="my_tasks_report"),
    path("entities/clients/search/", views.client_search, name="client_search"),
    path("entities/clients/create/", views.client_create, name="client_create"),
    path("entities/clients/connections/", views.client_connections, name="client_connections"),
    path("entities/connection/toggle/", views.connection_toggle, name="connection_toggle"),
    path("entities/label/companies/", views.label_companies, name="label_companies"),
    path("entities/label/toggle/", views.label_toggle, name="label_toggle"),
    # There was a ``entities/us/connections/`` route here, pointing at a
    # ``views.us_connections``. Both are gone, along with the
    # ``usConnectionsUrl`` key ``marketing/templates/marketing/home.html`` used
    # to hand the chart: no client ever read that key, so nothing could reach
    # the route. The "us" card is served by ``client_connections`` and
    # ``all_cases_search`` instead. (``services.us_connections`` still exists —
    # ``views.home`` counts it for the card's badge.)
    path("entities/clients/case-counts/", views.client_case_counts, name="client_case_counts"),
    path("entities/cases/search/", views.all_cases_search, name="all_cases_search"),
]
